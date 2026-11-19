"""Scrape pipeline: fetch -> dedupe -> cheap rule filters -> enrich -> Claude score (optional) -> shortlist.

Without Claude, every job that passes the rule filters goes to Shortlisted without a score."""
import logging
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import claude, config, db, discovery, skills
from .sources import FETCHERS, ats, enrich
from .sources.ats import configured_slugs

log = logging.getLogger(__name__)

US_OK = re.compile(r"\b(us|u\.s\.|usa|united states|america|north america|anywhere|worldwide|global)\b", re.I)
NON_US = re.compile(r"\b(europe|emea|uk|united kingdom|england|germany|india|canada|latam|latin america|brazil|"
                    r"poland|spain|philippines|apac|asia|australia|mexico|argentina|portugal|netherlands|france|"
                    r"ireland|israel|singapore|japan|colombia|romania|ukraine|south africa|nigeria|pakistan|toronto|ontario|"
                    r"vancouver|montreal|quebec|british columbia|london|berlin|amsterdam|dublin|bangalore|bengaluru)\b", re.I)

# Wording that says a role really is remote. LinkedIn's "Remote" filter tags plenty of office jobs.
REMOTE_WORDS = re.compile(r"\bremote\b|work(ing)?[- ]from[- ](home|anywhere)|\bwfh\b|telecommut|distributed team|"
                          r"anywhere in the (us|u\.s\.|united states)|home[- ]based|virtual (role|position)", re.I)
COUNTRYWIDE = re.compile(r"^\s*(united states|usa|us|remote|anywhere)\b", re.I)

state = {"running": False, "started_at": None, "log": [], "last": None}
_lock = threading.Lock()


def _say(msg):
    log.info(msg)
    state["log"].append(f"{time.strftime('%H:%M:%S')} {msg}")
    state["log"] = state["log"][-300:]


def posted_date(value):
    """A job's posting date as an aware datetime, or None if it's missing or unreadable. Sources
    send ISO timestamps or plain dates, epoch milliseconds (Lever), or RSS dates (We Work Remotely)."""
    v = str(value or "").strip()
    if not v:
        return None
    try:
        if v.isdigit():
            n = int(v)
            dt = datetime.fromtimestamp(n / 1000 if n > 1e11 else n, timezone.utc)
        else:
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError:
                dt = parsedate_to_datetime(v)
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Any money amount in a posting ("$120,000", "$95/hr", "150k", "USD 140,000"). Used only to decide that
# a posting can't contain pay; whether a job really lists pay is settled after Claude reads it.
MONEY = re.compile(r"[$€£]\s?\d|\b(?:usd|us\$)\s?\d|\b\d{2,3}(?:,\d{3})+\b|\b\d{2,3}(?:\.\d)?\s?k\b", re.I)


def has_pay(j, extra=""):
    return bool(j.get("pay_min") or j.get("pay_max") or (j.get("pay") or "").strip() or (extra or "").strip())


def _rx(patterns):
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I) if patterns else None


class Rules:
    def __init__(self, p):
        f = p.get("filters", {})
        self.include = _rx(f.get("title_include", []))
        self.exclude = _rx(f.get("title_exclude", []))
        self.bad_co = _rx(f.get("company_exclude", []))
        self.clearance = _rx(f.get("clearance_patterns", []))
        self.contract = _rx(f.get("contract_patterns", []))
        self.local = _rx(f.get("local_locations", []))
        self.min_pay = int(f.get("min_pay", 0) or 0)
        self.allow_contract = bool(f.get("allow_contract", False))
        self.allow_onsite_local = bool(f.get("allow_local_onsite", True))
        self.max_age_days = int(f.get("max_age_days", 0) or 0)
        self.remote_us_only = bool(f.get("remote_us_only", True))
        self.allow_no_pay = bool(f.get("allow_no_pay", True))

    def pre(self, j):
        """Checks on title/company/location/pay before fetching a description. Returns a reason or None."""
        title, co = j.get("title") or "", j.get("company") or ""
        # _topic_match: found by a keyword search whose hits rarely say it in the title (USAJOBS titles
        # read "IT Specialist (APPSW)"), so the title doesn't have to match; everything else still applies
        if self.include and not self.include.search(title) and not j.get("_topic_match"):
            return "title doesn't match"
        if self.exclude and self.exclude.search(title):
            return "title excluded"
        if self.bad_co and self.bad_co.search(co):
            return "company excluded"
        loc, wp = j.get("location") or "", j.get("workplace") or "unknown"
        if wp == "remote" or (wp == "unknown" and "remote" in loc.lower()):
            if self.remote_us_only and NON_US.search(loc) and not US_OK.search(loc):
                return f"remote outside US ({loc})"
        elif j["source"] != "linkedin":  # LinkedIn searches are already location-scoped
            if not (self.allow_onsite_local and self.local and self.local.search(loc)):
                return f"not remote and not local ({loc or 'no location'})"
        return self.post(j, desc=False)

    def post(self, j, desc=True):
        """Checks that need the description too."""
        if desc and (r := self.fake_remote(j)):
            return r
        if r := self.too_old(j):
            return r
        if desc and not self.allow_no_pay and not has_pay(j) and not MONEY.search(j.get("description") or ""):
            return "no pay listed"
        if j.get("pay_max") and self.min_pay and j["pay_max"] < self.min_pay:
            return f"pay tops out at ${j['pay_max']:,}"
        text = (j.get("title") or "") + "\n" + ((j.get("description") or "") if desc else "")
        if self.clearance and (m := self.clearance.search(text)):
            return f"clearance: {m.group(0)}"
        et = (j.get("employment_type") or "").lower()
        if not self.allow_contract:
            if et and any(w in et for w in ("contract", "temporary", "part", "intern")):
                return f"employment type: {et}"
            if self.contract and (m := self.contract.search(text)):
                return f"contract: {m.group(0)}"
        return None


    def too_old(self, j):
        """Jobs whose posted date is older than max_age_days. No date (or one we can't read) passes."""
        if not self.max_age_days or not (dt := posted_date(j.get("posted_at"))):
            return None
        age = (datetime.now(timezone.utc) - dt).days
        if age > self.max_age_days:
            return f"posted over {self.max_age_days} days ago ({age} days)"
        return None

    def fake_remote(self, j):
        """LinkedIn tags office jobs as remote, and Adzuna's "remote" search matches any posting that
        mentions the word. If the listing's location is a specific city outside the local area and the
        posting never says remote, it isn't remote."""
        if j.get("source") not in ("linkedin", "adzuna") or j.get("workplace") != "remote" or not j.get("description"):
            return None
        loc = j.get("location") or ""
        if not loc or COUNTRYWIDE.search(loc) or (self.local and self.local.search(loc)):
            return None
        if REMOTE_WORDS.search(f"{j.get('title') or ''}\n{j['description']}"):
            return None
        return f"not remote: listed in {loc} and the posting never mentions remote work"


def run(trigger="manual"):
    if not _lock.acquire(blocking=False):
        return False
    started = db.now()
    state.update(running=True, started_at=started, log=[])
    summary = {"sources": [], "shortlisted": 0, "screened_out": 0, "scored": 0, "errors": []}
    try:
        _run(summary)
    except Exception as e:  # keep the server alive; surface the error in the UI
        log.exception("scrape failed")
        summary["errors"].append(f"run failed: {e}")
        _say(f"ERROR run failed: {e}")
    finally:
        db.record_run(started, trigger, summary)
        state.update(running=False, last={"started_at": started, "finished_at": db.now(), **summary})
        _lock.release()
    return True


def expire_applied(prof, quiet=False):
    """Applied cards with no change for cleanup.applied_no_response_days (default 60) go to No Response.
    quiet: write to the server log only, not the scrape log shown in the board."""
    days = int((prof.get("cleanup") or {}).get("applied_no_response_days", 60) or 0)
    moved = db.expire_applied(days) if days > 0 else []
    if moved and quiet:
        log.info("Cleanup: moved %d card(s) applied %d+ days ago to No Response", len(moved), days)
    elif moved:
        _say(f"Cleanup: moved {len(moved)} card(s) applied {days}+ days ago to No Response")
        for r in moved:
            _say(f"  no response: {r['company']} – {r['title']}")
    return moved


def _cleanup(prof):
    expire_applied(prof)
    days = int((prof.get("cleanup") or {}).get("shortlisted_max_days", 30) or 0)
    if days <= 0:
        return
    removed = db.prune_shortlisted(days)
    if removed:
        _say(f"Cleanup: removed {len(removed)} card(s) left in Shortlisted {days}+ days")
        for r in removed:
            _say(f"  removed {r['company']} – {r['title']}")


def _run(summary):
    prof = config.profile()
    _cleanup(prof)
    rules = Rules(prof)
    resume = config.resume()
    sc = prof.get("scoring", {})
    model, min_score = sc.get("model", claude.DEFAULT_MODEL), int(sc.get("min_score", 65))
    budget = int(sc.get("max_scored_per_run", 60))
    criteria = sc.get("criteria", "")
    use_claude = claude.available() and sc.get("use_claude", True) is not False
    _say("Scoring with Claude (" + claude.auth_mode() + ")" if use_claude else
         "Claude scoring is off: jobs that pass the filters go to Shortlisted without a score")

    disc = prof.get("discovery", {})
    disc_on = disc.get("enabled", True)
    candidates = []
    sources = config.sources()
    for cfg in sources:
        if cfg.get("enabled") is False:
            continue
        name = cfg.get("name") or cfg.get("type")
        fn = FETCHERS.get(cfg.get("type"))
        if not fn:
            summary["errors"].append(f"{name}: unknown type {cfg.get('type')!r}")
            continue
        _say(f"Fetching {name}…")
        if cfg.get("type") in ats.FETCH_ONE:
            cfg["_prefilter"] = rules.pre   # lets big company lists drop descriptions of jobs that fail it
        try:
            jobs = fn(cfg)
        except Exception as e:
            summary["errors"].append(f"{name}: {e}")
            _say(f"ERROR {name}: {e}")
            continue
        for w in cfg.get("_warnings", []):
            summary["errors"].append(f"{name}: {w}")
            _say(f"WARN {name}: {w}")
        for n in cfg.get("_notes", []):
            _say(f"{name}: {n}")
        _ingest(name, jobs, rules, candidates, summary, disc_on)

    if disc_on:
        found = discovery.backfill(rules.bad_co)
        if found:
            _say(f"Discovered {found} new company board(s) from saved job links")
        skip = configured_slugs(sources)
        companies = [c for c in discovery.enabled() if (c["ats"], c["slug"]) not in skip]
        _say(f"Scanning {len(companies)} discovered company boards…")
        jobs = []
        for c in companies:
            try:
                got = ats.fetch_company(c["ats"], c["slug"], c["name"])
                real = next((j["company"] for j in got if j.get("company")), None) if c["ats"] == "greenhouse" else None
                discovery.record(c["ats"], c["slug"], "ok", len(got), name=real)
                jobs += got
            except ats.BoardNotFound as e:
                discovery.record(c["ats"], c["slug"], "not found", disable=True)
                _say(f"  {e}; switched off")
            except Exception as e:
                discovery.record(c["ats"], c["slug"], f"error: {e}")
                _say(f"  WARN {e}")
        _ingest(f"Discovered companies ({len(companies)})", jobs, rules, candidates, summary, disc_on)

    # Same job found by two sources in one run
    uniq = {}
    for j in candidates:
        uniq.setdefault(j["dedupe_key"], j)
    candidates = _prioritize(list(uniq.values()), resume, prof)
    by_src = Counter(j["source"] for j in candidates)
    _say(f"{len(candidates)} candidates to screen (budget {budget} per run): "
         + ", ".join(f"{k} {v}" for k, v in by_src.most_common()))

    batch_size = int(sc.get("batch_size", 5))
    pending = iter(candidates)
    left = len(candidates)
    solo = []   # postings that look like they hold instructions for Claude: each is scored alone
    while summary["scored"] < budget:
        batch = [solo.pop(0)] if solo and use_claude else []
        for j in ([] if batch else pending):  # enrich + description-based rules until we have a full batch
            left -= 1
            j = enrich(j)
            if j.get("_retry"):
                _say(f"  later {j['company']} – {j['title']}: couldn't fetch its description")
                continue
            if disc_on and (n := discovery.note(j, rules.bad_co)):
                _say(f"  discovered {n} company board(s) via {j['company']}")
            if r := rules.post(j):
                db.mark_seen(j, r)
                summary["screened_out"] += 1
                _say(f"  skip {j['company']} – {j['title']}: {r}")
                continue
            if use_claude and claude.suspicious(j):
                # Scored in a batch of its own, so its text can't change the scores of other postings
                _say(f"  {j['company']} – {j['title']}: posting contains instruction-like text; scoring it alone")
                if batch:
                    solo.append(j)
                    continue
                batch.append(j)
                break
            batch.append(j)
            if len(batch) >= min(batch_size, budget - summary["scored"]):
                break
        if not batch:
            break
        if not use_claude:
            for j in batch:
                summary["scored"] += 1
                _add_unscored(j, summary)
            continue
        try:
            results = claude.score_jobs(batch, resume, criteria, model, prof.get("categories"),
                                         skills.prompt_text(prof))
        except claude.UsageLimitError as e:
            summary["errors"].append(f"Claude usage limit: {e}")
            _say(f"Claude usage limit reached; {len(batch) + left} jobs left for the next run")
            break
        except Exception as e:
            summary["errors"].append(f"scoring batch: {e}")
            _say(f"ERROR scoring batch of {len(batch)}: {e}")
            continue  # not marked seen, so they're retried next run
        for i, j in enumerate(batch):
            res = results.get(i)
            if not res:
                continue  # missing from Claude's reply: retried next run
            summary["scored"] += 1
            _apply(j, res, rules, min_score, summary)
    if left > 0 and summary["scored"] >= budget:
        rest = Counter(j["source"] for j in candidates[len(candidates) - left:])
        summary["waiting"] = left
        _say(f"Hit the per-run budget of {budget}; {left} jobs wait for the next run ("
             + ", ".join(f"{k} {v}" for k, v in rest.most_common()) + ")")
    _say(f"Done: {summary['shortlisted']} shortlisted, {summary['screened_out']} screened out"
         + (f", {summary['scored']} scored by Claude" if use_claude else ""))


def _resume_terms(resume):
    """Skill keywords from the resume's Skills section, e.g. 'Terraform', 'GitHub Actions'."""
    m = re.search(r"^## Skills\s*$(.*?)^## ", resume, re.M | re.S | re.I)
    terms = set()
    for line in (m.group(1) if m else "").splitlines():
        line = re.sub(r"^\s*-\s*(\*\*[^*]+\*\*)?", "", line)
        for t in re.split(r"[,/]", re.sub(r"\([^)]*\)", "", line)):
            t = t.strip()
            if len(t) >= 2 and not t.lower().startswith("and "):
                terms.add(t)
    return [re.compile(r"(?<![\w])" + re.escape(t) + r"(?![\w])", re.I) for t in sorted(terms)]


def _prioritize(candidates, resume, prof):
    """Best-first within each source, then sources take turns, so one big source can't use the whole
    per-run budget and the strongest matches get looked at first. "Best" is the weighted skill tiers in
    profile.yaml when set (core +3, working +1, gap -2), else a count of the resume's Skills terms."""
    tiers = skills.compile_skills(prof)
    terms = None if tiers else _resume_terms(resume)
    groups = {}
    for j in candidates:
        text = f"{j.get('title') or ''}\n{j.get('description') or ''}"
        j["_pre"] = skills.weight(tiers, text) if tiers else sum(1 for t in terms if t.search(text))
        groups.setdefault(j["source"], []).append(j)
    for g in groups.values():
        g.sort(key=lambda j: -j["_pre"])
    out, lists = [], list(groups.values())
    while any(lists):
        for g in lists:
            if g:
                out.append(g.pop(0))
    return out


def _ingest(name, jobs, rules, candidates, summary, disc_on):
    new = reasons = found = 0
    why = Counter()
    for j in jobs:
        j["external_id"] = str(j["external_id"])
        j["dedupe_key"] = db.dedupe_key(j.get("company"), j.get("title"))
        if disc_on:
            found += discovery.note(j, rules.bad_co)
        if db.is_known(j["source"], j["external_id"], j["dedupe_key"]):
            continue
        new += 1
        if r := rules.pre(j):
            reasons += 1
            why[r.split(":")[0].split("(")[0].strip()] += 1
            # Company boards are re-read in full every run and these checks are cheap, so their
            # rejects aren't stored (a few thousand boards would add ~100k rows to Screened out).
            if j["source"] not in ats.FETCH_ONE:
                db.mark_seen(j, r)
            continue
        candidates.append(j)
    summary["sources"].append({"name": name, "found": len(jobs), "new": new, "filtered": reasons})
    _say(f"{name}: {len(jobs)} found, {new} new, {reasons} filtered by rules"
         + (f" ({', '.join(f'{k}: {v}' for k, v in why.most_common(4))})" if why else "")
         + (f"; discovered {found} new company board(s)" if found else ""))


def _add_unscored(j, summary):
    """No Claude: the job passed every rule filter, so it goes to Shortlisted without a score."""
    db.add_job({**j, "status": "shortlisted", "category": "Other"})
    summary["shortlisted"] += 1
    _say(f"  + {j['company']} – {j['title']}")


def _apply(j, res, rules, min_score, summary):
    if res["employment_type"] in ("contract", "part-time", "internship") and not rules.allow_contract:
        res["verdict"], res["skip_reason"] = "skip", res["skip_reason"] or res["employment_type"]
    if not rules.allow_no_pay and not has_pay(j, res.get("pay")):
        res["verdict"], res["skip_reason"] = "skip", res["skip_reason"] or "no pay listed"
    if res["verdict"] == "shortlist" and res["score"] >= min_score:
        db.add_job({**j, "status": "shortlisted", "score": res["score"], "category": res["category"],
                    "fit_summary": res["fit_summary"], "gaps": res["gaps"],
                    "workplace": res["workplace"] if res["workplace"] != "unknown" else j.get("workplace"),
                    "employment_type": res["employment_type"], "pay": j.get("pay") or res["pay"]})
        summary["shortlisted"] += 1
        _say(f"  ★ {res['score']} {j['company']} – {j['title']}")
    else:
        db.mark_seen(j, f"score {res['score']}: {res['skip_reason'] or 'weak fit'}", res["score"])
        summary["screened_out"] += 1
        _say(f"  skip {res['score']} {j['company']} – {j['title']}: {res['skip_reason'] or 'weak fit'}")
