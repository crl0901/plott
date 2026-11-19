"""Company applicant-tracking-system boards with public JSON/XML APIs, plus slug discovery."""
import json
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from urllib.parse import unquote, urlparse

from .. import config
from ..web import UA, get, html_to_text, pay_from_text, unescape_html_text

# Patterns that pull a company's board slug out of any URL or text.
SLUG_PATTERNS = {
    "greenhouse": re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([A-Za-z0-9_-]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    "lever": re.compile(r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)"),
    "smartrecruiters": re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    "workable": re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
    "rippling": re.compile(r"ats\.rippling\.com/([A-Za-z0-9_-]+)"),
    # Subdomain boards: <slug>.recruitee.com, <slug>.breezy.hr, <slug>.bamboohr.com, <slug>.jobs.personio.de
    "recruitee": re.compile(r"(?<![\w.-])([a-z0-9][a-z0-9-]*)\.recruitee\.com", re.I),
    "breezy": re.compile(r"(?<![\w.-])([a-z0-9][a-z0-9-]*)\.breezy\.hr", re.I),
    "bamboohr": re.compile(r"(?<![\w.-])([a-z0-9][a-z0-9-]*)\.bamboohr\.com", re.I),
    "personio": re.compile(r"(?<![\w.-])([a-z0-9][a-z0-9-]*)\.jobs\.personio\.(?:de|com)", re.I),
}
NOT_SLUGS = {"embed", "api", "v1", "jobs", "job_app", "static", "assets", "favicon.ico", "careers",
             "j", "www", "app", "help", "support", "marketplace", "partners", "cdn", "external-referrals",
             "oneclick-ui"}


class BoardNotFound(RuntimeError):
    """The company's board doesn't exist (404): the slug is wrong or the company left this ATS."""


def find_slugs(text):
    """Yield (ats, slug) pairs for every ATS board link in a URL or block of text."""
    text = unquote(text or "")
    for ats, pat in SLUG_PATTERNS.items():
        for m in pat.finditer(text):
            slug = m.group(1).lower().strip(".")
            if slug and slug not in NOT_SLUGS:
                yield ats, slug


def _workplace(loc, remote_flag=None, wp=None):
    wp = (wp or "").lower()
    if wp in ("remote", "hybrid"):
        return wp
    if wp in ("onsite", "on-site", "in office", "inoffice"):
        return "onsite"
    if remote_flag or "remote" in (loc or "").lower():
        return "remote"
    if "hybrid" in (loc or "").lower():
        return "hybrid"
    return "onsite" if loc else "unknown"


class Throttled(RuntimeError):
    """The ATS refused the request (403/429): too many requests from this IP."""


def _get(ats, slug, url, params=None):
    resp = get(url, params=params)
    if resp is not None and resp.status_code in (404, 410):
        raise BoardNotFound(f"{ats}/{slug}: board not found")
    if resp is not None and resp.status_code in (403, 429):
        raise Throttled(f"{ats}/{slug}: HTTP {resp.status_code} (throttled)")
    if resp is None or not resp.ok:
        raise RuntimeError(f"{ats}/{slug}: HTTP {getattr(resp, 'status_code', 'error')}")
    # Subdomain ATSs redirect unknown companies to their marketing site instead of a 404
    if resp.history and slug.lower() not in (urlparse(resp.url).netloc + urlparse(resp.url).path).lower():
        raise BoardNotFound(f"{ats}/{slug}: board not found")
    return resp


def _get_json(ats, slug, url, params=None):
    resp = _get(ats, slug, url, params)
    try:
        return resp.json()
    except ValueError:
        raise BoardNotFound(f"{ats}/{slug}: board not found") from None


def _greenhouse(slug, name):
    """The board's job list without descriptions (small and fast even for big boards). Jobs that pass
    the cheap filters get their description from enrich_greenhouse()."""
    data = _get_json("greenhouse", slug, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", None)
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append({"source": "greenhouse", "external_id": str(j["id"]), "company": j.get("company_name") or name,
                    "title": j.get("title"), "url": j.get("absolute_url"), "location": loc,
                    "workplace": _workplace(loc), "description": "", "posted_at": j.get("first_published") or j.get("updated_at"),
                    "pay": "", "pay_min": None, "pay_max": None, "board": slug})
    return out


def enrich_greenhouse(job):
    """Description (and pay found in it) for one Greenhouse job."""
    slug = job.get("board") or next((s for a, s in find_slugs(job.get("url") or "") if a == "greenhouse"), None)
    if not slug:
        return job
    resp = get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job['external_id']}", tries=2)
    if resp is None or not resp.ok:
        job["_retry"] = True   # scored on a later run instead of without a description
        return job
    desc = unescape_html_text(resp.json().get("content"))
    if desc:
        job["description"] = desc
        if not job.get("pay"):
            job["pay"], job["pay_min"], job["pay_max"] = pay_from_text(desc)
    return job


def _lever(slug, name):
    try:
        data = _get_json("lever", slug, f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"})
    except BoardNotFound:  # boards hosted at jobs.eu.lever.co only exist on Lever's EU API
        data = _get_json("lever", slug, f"https://api.eu.lever.co/v0/postings/{slug}", {"mode": "json"})
    out = []
    for j in data:
        cats = j.get("categories") or {}
        desc = "\n\n".join(filter(None, [j.get("descriptionPlain"), *[
            l.get("text", "") + "\n" + unescape_html_text(l.get("content")) for l in j.get("lists", [])],
            j.get("additionalPlain")]))
        sr = j.get("salaryRange") or {}
        lo, hi = sr.get("min"), sr.get("max")
        pay = f"${lo:,} - ${hi:,} {sr.get('interval', '')}".strip() if lo and hi else ""
        if not lo:
            pay, lo, hi = pay_from_text(desc)
        if sr.get("interval") and "year" not in sr["interval"]:
            lo = hi = None
        out.append({"source": "lever", "external_id": j["id"], "company": name, "title": j.get("text"),
                    "url": j.get("hostedUrl"), "location": cats.get("location", ""),
                    "workplace": _workplace(cats.get("location"), wp=j.get("workplaceType")),
                    "employment_type": (cats.get("commitment") or "").lower(), "description": desc,
                    "posted_at": str(j.get("createdAt", "")), "pay": pay, "pay_min": lo, "pay_max": hi})
    return out


def _ashby(slug, name):
    data = _get_json("ashby", slug, f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                     {"includeCompensation": "true"})
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        desc = j.get("descriptionPlain") or ""
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        pay, lo, hi = pay_from_text(comp.replace("–", "-")) if comp else pay_from_text(desc)
        out.append({"source": "ashby", "external_id": j["id"], "company": name, "title": j.get("title"),
                    "url": j.get("jobUrl"), "location": j.get("location", ""),
                    "workplace": _workplace(j.get("location"), j.get("isRemote"), j.get("workplaceType")),
                    "employment_type": (j.get("employmentType") or "").lower(), "description": desc,
                    "posted_at": j.get("publishedAt"), "pay": comp or pay, "pay_min": lo, "pay_max": hi})
    return out


def _job(source, ext_id, name, title, url, loc, workplace, **kw):
    j = {"source": source, "external_id": str(ext_id), "company": name, "title": title, "url": url,
         "location": loc or "", "workplace": workplace, "employment_type": "", "description": "",
         "posted_at": None, "pay": "", "pay_min": None, "pay_max": None}
    j.update(kw)
    if j["description"] and not j["pay_min"]:
        pay, lo, hi = pay_from_text(j["description"])
        if lo:
            j.update(pay=j["pay"] or pay, pay_min=lo, pay_max=hi)
    return j


def _yearly(lo, hi, period):
    """Pay range as (text, min, max); min/max only when it's a yearly figure."""
    try:
        lo, hi = int(float(lo)), int(float(hi or lo))
    except (TypeError, ValueError):
        return "", None, None
    text = f"${lo:,} - ${hi:,}" + (f" per {period}" if period else "")
    return (text, lo, hi) if (period or "year").lower().startswith("year") else (text, None, None)


MAX_PAGES = 50   # paginated boards: at most this many pages (5,000 jobs on SmartRecruiters)


def _smartrecruiters(slug, name):
    """Job list without descriptions; enrich_smartrecruiters() fetches them for jobs that pass the filters."""
    out, offset = [], 0
    for _ in range(MAX_PAGES):
        data = _get_json("smartrecruiters", slug, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                         {"limit": 100, "offset": offset})
        for j in data.get("content", []):
            loc = j.get("location") or {}
            out.append(_job("smartrecruiters", j["id"], ((j.get("company") or {}).get("name") or "").strip() or name,
                            j.get("name"), f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                            loc.get("fullLocation") or ", ".join(filter(None, [loc.get("city"), loc.get("region")])),
                            _workplace(loc.get("fullLocation"), loc.get("remote"), "hybrid" if loc.get("hybrid") else None),
                            employment_type=((j.get("typeOfEmployment") or {}).get("label") or "").lower(),
                            posted_at=j.get("releasedDate"), board=slug))
        offset += 100
        if offset >= (data.get("totalFound") or 0):
            break
    return out


def enrich_smartrecruiters(job):
    resp = get(f"https://api.smartrecruiters.com/v1/companies/{job.get('board')}/postings/{job['external_id']}", tries=2)
    if resp is None or not resp.ok:
        job["_retry"] = True
        return job
    sections = ((resp.json().get("jobAd") or {}).get("sections") or {})
    parts = [f"{s.get('title', '')}\n{html_to_text(s.get('text'))}" for k in
             ("jobDescription", "qualifications", "additionalInformation", "companyDescription")
             if (s := sections.get(k)) and s.get("text")]
    return _with_description(job, "\n\n".join(parts))


def _with_description(job, desc):
    if desc:
        job["description"] = desc[:60_000]
        if not job.get("pay_min"):
            pay, lo, hi = pay_from_text(desc)
            if lo:
                job.update(pay=job.get("pay") or pay, pay_min=lo, pay_max=hi)
    return job


def _workable(slug, name):
    """Job list without descriptions; enrich_workable() fetches them for jobs that pass the filters."""
    data = _get_json("workable", slug, f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    out, by_code = [], {}
    for j in data.get("jobs", []):   # a job posted in several cities is listed once per city
        if (seen := by_code.get(j["shortcode"])) is not None:
            seen["locations"] = (seen.get("locations") or []) + (j.get("locations") or [])
            continue
        by_code[j["shortcode"]] = j = dict(j)
    for j in by_code.values():
        locs = j.get("locations") or [{"city": j.get("city"), "region": j.get("state"), "country": j.get("country")}]
        names = dict.fromkeys(", ".join(filter(None, [l.get("city"), l.get("region"), l.get("country")])) for l in locs)
        loc = "; ".join(filter(None, names))
        out.append(_job("workable", j["shortcode"], data.get("name") or name, j.get("title"), j.get("url"), loc,
                        _workplace(loc, j.get("telecommuting")),
                        employment_type=(j.get("employment_type") or "").lower(),
                        posted_at=j.get("published_on") or j.get("created_at"), board=slug))
    return out


def enrich_workable(job):
    resp = get(f"https://apply.workable.com/api/v2/accounts/{job.get('board')}/jobs/{job['external_id']}", tries=2)
    if resp is None or not resp.ok:
        job["_retry"] = True
        return job
    d = resp.json()
    if d.get("workplace") in ("remote", "hybrid", "on_site"):
        job["workplace"] = "onsite" if d["workplace"] == "on_site" else d["workplace"]
    if (t := d.get("type")) and not job.get("employment_type"):
        job["employment_type"] = str(t).lower().replace("_", "-")
    return _with_description(job, "\n\n".join(html_to_text(d.get(k)) for k in
                                                ("description", "requirements", "benefits") if d.get(k)))


def _recruitee(slug, name):
    data = _get_json("recruitee", slug, f"https://{slug}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        if j.get("status") not in (None, "published"):
            continue
        sal = j.get("salary") or {}
        pay, lo, hi = _yearly(sal.get("min"), sal.get("max"), sal.get("period")) if sal.get("min") else ("", None, None)
        wp = "remote" if j.get("remote") else "hybrid" if j.get("hybrid") else "onsite" if j.get("on_site") else None
        out.append(_job("recruitee", j["id"], j.get("company_name") or name, j.get("title"), j.get("careers_url"),
                        j.get("location"), _workplace(j.get("location"), wp=wp),
                        employment_type=(j.get("employment_type_code") or "").replace("_", " "),
                        description="\n\n".join(html_to_text(j.get(k)) for k in ("description", "requirements") if j.get(k)),
                        posted_at=(j.get("published_at") or j.get("created_at") or "").replace(" UTC", "+00:00"),
                        pay=pay, pay_min=lo, pay_max=hi))
    return out


def _breezy(slug, name):
    """No descriptions in the list; the default enricher reads the job page for jobs that pass the filters."""
    data = _get_json("breezy", slug, f"https://{slug}.breezy.hr/json")
    out = []
    for j in data if isinstance(data, list) else []:
        locs = j.get("locations") or [j.get("location") or {}]
        loc = "; ".join(l.get("name") or "" for l in locs if l.get("name"))
        remote = any(l.get("is_remote") for l in locs)
        pay, lo, hi = pay_from_text(j.get("salary") or "")
        out.append(_job("breezy", j["id"], (j.get("company") or {}).get("name") or name, j.get("name"), j.get("url"),
                        loc, _workplace(loc, remote), employment_type=((j.get("type") or {}).get("name") or "").lower(),
                        posted_at=j.get("published_date"), pay=j.get("salary") or "", pay_min=lo, pay_max=hi))
    return out


BAMBOO_WORKPLACE = {"0": "onsite", "1": "remote", "2": "hybrid"}


def _bamboohr(slug, name):
    """No descriptions or dates in the list; enrich_bamboohr() fetches them for jobs that pass the filters."""
    data = _get_json("bamboohr", slug, f"https://{slug}.bamboohr.com/careers/list")
    out = []
    for j in data.get("result", []):
        a, l = j.get("atsLocation") or {}, j.get("location") or {}
        loc = ", ".join(filter(None, [a.get("city") or l.get("city"), a.get("state") or l.get("state"), a.get("country")]))
        wp = "remote" if j.get("isRemote") else BAMBOO_WORKPLACE.get(str(j.get("locationType")))
        out.append(_job("bamboohr", j["id"], name, j.get("jobOpeningName"), f"https://{slug}.bamboohr.com/careers/{j['id']}",
                        loc, _workplace(loc, wp=wp), employment_type=(j.get("employmentStatusLabel") or "").lower(),
                        board=slug))
    return out


def enrich_bamboohr(job):
    resp = get(f"https://{job.get('board')}.bamboohr.com/careers/{job['external_id']}/detail", tries=2)
    if resp is None or not resp.ok:
        job["_retry"] = True
        return job
    d = ((resp.json().get("result") or {}).get("jobOpening") or {})
    job["posted_at"] = d.get("datePosted") or job.get("posted_at")
    comp = d.get("compensation")
    if comp and not job.get("pay"):
        job["pay"] = str(comp)
    return _with_description(job, "\n\n".join(filter(None, [html_to_text(d.get("description")), str(comp or "")])))


RIPPLING_WORKPLACE = {"REMOTE": "remote", "HYBRID": "hybrid", "ON_SITE": "onsite"}


def _rippling(slug, name):
    """No descriptions or dates in the list; enrich_rippling() reads them from the job page. The list
    repeats a job once per location, so locations are merged by job id."""
    items, page = {}, 0
    for _ in range(MAX_PAGES):
        data = _get_json("rippling", slug, f"https://ats.rippling.com/api/v2/board/{slug}/jobs",
                         {"page": page, "pageSize": 100})
        for j in data.get("items", []):
            seen = items.setdefault(j["id"], {**j, "locations": []})
            seen["locations"] += [l for l in j.get("locations") or [] if l not in seen["locations"]]
        page += 1
        if page >= (data.get("totalPages") or 0):
            break
    out = []
    for j in items.values():
        locs = j["locations"]
        loc = "; ".join(l.get("name") or "" for l in locs if l.get("name"))
        types = {RIPPLING_WORKPLACE.get(l.get("workplaceType")) for l in locs} - {None}
        wp = "remote" if "remote" in types else "hybrid" if "hybrid" in types else "onsite" if types else None
        out.append(_job("rippling", j["id"], name, j.get("name"), j.get("url"), loc, _workplace(loc, wp=wp)))
    return out


def enrich_rippling(job):
    resp = get(job["url"], tries=2)
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S) if resp is not None and resp.ok else None
    if not m:
        job["_retry"] = True
        return job
    try:
        post = json.loads(m.group(1))["props"]["pageProps"]["apiData"]["jobPost"]
    except (ValueError, KeyError, TypeError):
        job["_retry"] = True
        return job
    job["posted_at"] = post.get("createdOn") or job.get("posted_at")
    et = (post.get("employmentType") or {})
    job["employment_type"] = str(et.get("id") or et.get("label") or "").lower()
    desc = post.get("description") or {}
    text = "\n\n".join(html_to_text(v) for v in (desc.values() if isinstance(desc, dict) else [desc]) if v)
    return _with_description(job, text)


def _personio(slug, name):
    resp = _get("personio", slug, f"https://{slug}.jobs.personio.de/xml")
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        raise BoardNotFound(f"personio/{slug}: board not found") from None
    out = []
    for p in root.iter("position"):
        t = lambda tag: (p.findtext(tag) or "").strip()
        offices = [t("office")] + [o.text.strip() for o in p.findall("additionalOffices/office") if o.text]
        loc = "; ".join(filter(None, offices))
        desc = "\n\n".join(f"{d.findtext('name') or ''}\n{html_to_text(d.findtext('value'))}"
                            for d in p.findall("jobDescriptions/jobDescription"))
        et = " ".join(filter(None, [t("employmentType"), t("schedule")])).lower()
        out.append(_job("personio", t("id"), t("subcompany") or name, t("name"),
                        f"https://{slug}.jobs.personio.de/job/{t('id')}", loc, _workplace(loc),
                        employment_type=et.replace("permanent", "").strip(), description=desc,
                        posted_at=t("createdAt") or None))
    return out


# ---------- Workday ----------
# Workday's job API answers Python's HTTP client with 429s within a few requests (it flags the TLS
# fingerprint), but not curl, so these requests go through curl, one at a time, WD_INTERVAL apart.
# Boards are searched with the source's `search` terms (OR'd) instead of being listed in full: big
# employers have thousands of jobs at 20 per request.
WD_INTERVAL = 2.0     # seconds between Workday requests (all boards share one IP)
WD_PAGES = 2          # pages of 20 search results read per board
WD_SLUG = re.compile(r"([a-z0-9_-]+)\.(wd\d+)/([^/\s]+)")
WD_MULTI = re.compile(r"^\d+ locations?$", re.I)
_wd_lock = threading.Lock()
_wd_last = [0.0]


def _wd_api(slug):
    m = WD_SLUG.fullmatch(slug)
    if not m:
        raise RuntimeError(f"workday/{slug}: expected tenant.wdN/site, e.g. acme.wd5/careers")
    tenant, dc, site = m.groups()
    host = f"https://{tenant}.{dc}.myworkdayjobs.com"
    return host, site, f"{host}/wday/cxs/{tenant}/{site}"


def _wd(slug, url, body=None):
    """One Workday API call through curl, spaced WD_INTERVAL after the previous one. A 429 waits
    30 seconds and tries once more before giving up on the board."""
    for attempt in range(2):
        with _wd_lock:
            wait = _wd_last[0] + WD_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _wd_last[0] = time.monotonic()
        cmd = ["curl", "-s", "--globoff", "--proto", "=https", "--max-filesize", "50M", "--max-time", "30",
               "-A", UA, "-H", "Accept: application/json",
               "-H", "Accept-Language: en-US", "-w", "\n%{http_code}"]
        if body is not None:
            cmd += ["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
        try:
            r = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=45)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"workday/{slug}: timed out") from None
        text, _, code = r.stdout.rpartition("\n")
        code = int(code) if code.strip().isdigit() else 0
        if code == 429 and attempt == 0:
            time.sleep(30)
            continue
        if code in (404, 410, 422):
            raise BoardNotFound(f"workday/{slug}: board not found")
        if code in (403, 429):
            raise Throttled(f"workday/{slug}: HTTP {code} (throttled)")
        if code != 200:
            raise RuntimeError(f"workday/{slug}: HTTP {code or 'error'}")
        try:
            return json.loads(text)
        except ValueError:
            raise RuntimeError(f"workday/{slug}: not a JSON response") from None
    raise Throttled(f"workday/{slug}: HTTP 429 (throttled)")


def _wd_posted(text):
    """'Posted Today' / 'Posted Yesterday' / 'Posted 5 Days Ago' / 'Posted 30+ Days Ago' -> ISO date."""
    t = (text or "").lower()
    if "today" in t:
        n = 0
    elif "yesterday" in t:
        n = 1
    elif m := re.search(r"(\d+)(\+?)\s*days?", t):
        n = int(m.group(1)) + (1 if m.group(2) else 0)
    else:
        return None
    return (date.today() - timedelta(days=n)).isoformat()


def _workday(slug, name, cfg=None):
    cfg = cfg or {}
    terms = [str(t).strip() for t in cfg.get("search") or [] if str(t).strip()]
    if not terms:
        raise RuntimeError("workday: add `search:` terms to this source in sources.yaml")
    host, site, api = _wd_api(slug)
    prefilter, pages = cfg.get("_prefilter"), int(cfg.get("pages") or WD_PAGES)
    out, total = [], None
    for page in range(pages):
        data = _wd(slug, f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": page * 20,
                                        "searchText": " OR ".join(terms)})
        posts = data.get("jobPostings") or []
        total = data.get("total") or 0 if total is None else total   # only the first page has it
        for p in posts:
            path, loc = p.get("externalPath") or "", p.get("locationsText") or ""
            j = _job("workday", f"{slug.split('/')[0]}{path}", name, p.get("title"), f"{host}/en-US/{site}{path}",
                     loc, _workplace(loc), posted_at=_wd_posted(p.get("postedOn")), board=slug, wd_path=path)
            if WD_MULTI.match(loc):   # "3 Locations": only the job page says where
                j["workplace"] = "unknown"
                if prefilter and prefilter({**j, "location": "Remote", "workplace": "remote"}) is None:
                    j = enrich_workday(j)
            out.append(j)
        if len(posts) < 20 or (page + 1) * 20 >= total:
            break
    return out


def enrich_workday(job):
    try:
        _, _, api = _wd_api(job.get("board") or "")
        info = _wd(job["board"], api + job["wd_path"]).get("jobPostingInfo") or {}
    except (RuntimeError, KeyError):
        job["_retry"] = True
        return job
    loc = "; ".join(dict.fromkeys(filter(None, [info.get("location"), *(info.get("additionalLocations") or [])])))
    rt = (info.get("remoteType") or "").lower()
    if loc or rt:
        job["location"] = loc or job.get("location")
        job["workplace"] = _workplace(job["location"], wp="remote" if "remote" in rt else "hybrid" if "hybrid" in rt else None)
    if info.get("timeType"):
        job["employment_type"] = info["timeType"].lower()
    if info.get("startDate"):
        job["posted_at"] = info["startDate"]
    return _with_description(job, html_to_text(info.get("jobDescription")))


FETCH_ONE = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "smartrecruiters": _smartrecruiters,
             "workable": _workable, "recruitee": _recruitee, "breezy": _breezy, "bamboohr": _bamboohr,
             "rippling": _rippling, "personio": _personio, "workday": _workday}


def pretty(slug):
    return re.sub(r"[-_.]+", " ", slug).title()


def fetch_company(ats, slug, name=None, cfg=None):
    if ats == "workday":   # needs the source's search terms
        return _workday(slug, name or pretty(slug.split(".")[0]), cfg)
    return FETCH_ONE[ats](slug, name or pretty(slug))


def _companies(cfg):
    """(slug, name) for each company in a sources.yaml entry: its `companies` list plus, if set, its
    `companies_file` (a text file in the config folder: one slug per line, `# Name` optional)."""
    seen = set()
    for c in cfg.get("companies") or []:
        slug, name = (str(c["slug"]), c.get("name")) if isinstance(c, dict) else (str(c), None)
        if slug.lower() not in seen:
            seen.add(slug.lower())
            yield slug.lower(), name or pretty(slug)
    if cfg.get("companies_file"):
        path = os.path.join(config.CONFIG_DIR, os.path.basename(cfg["companies_file"]))
        with open(path, encoding="utf-8") as f:
            for line in f:
                slug, _, name = line.partition("#")
                slug = slug.strip().lower()
                if slug and slug not in seen:
                    seen.add(slug)
                    yield slug, name.strip() or pretty(slug)


WORKERS = 4        # boards read at once
STOP_AFTER = 10    # throttled responses before the rest of the list is skipped for this run


def _fetch_all(ats, cfg, companies=None, workers=WORKERS):
    """Fetch every company in a sources.yaml entry, a few boards at a time; one bad slug doesn't sink
    the rest. If the ATS starts refusing requests, the rest of the list is skipped until the next run
    rather than hammering it. Long lists report failures as one summary warning."""
    companies = list(_companies(cfg)) if companies is None else companies
    out, errors, throttled, skipped = [], [], [], 0
    stop = threading.Event()

    def one(slug, name):
        if stop.is_set():
            return None
        try:
            jobs = fetch_company(ats, slug, name, cfg)
        except Throttled as e:
            throttled.append(str(e))
            if len(throttled) >= STOP_AFTER:
                stop.set()
            raise
        # Ashby lists include every description (~7.5 KB each); keep them only for jobs that pass the
        # cheap filters, so a few thousand boards don't hold hundreds of MB in memory.
        if prefilter := cfg.get("_prefilter"):
            for j in jobs:
                if j.get("description") and prefilter(j):
                    j["description"] = ""
        return jobs

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, slug, name) for slug, name in companies]
        for fut in futures:
            try:
                jobs = fut.result()
            except Throttled:
                continue
            except Exception as e:
                errors.append(str(e))
                continue
            if jobs is None:
                skipped += 1
            else:
                out += jobs
    warnings = []
    if throttled:
        warnings.append(f"{ats} refused {len(throttled)} requests (too many from this IP)"
                        + (f"; skipped the other {skipped} boards until the next run" if skipped else ""))
    # Boards that no longer exist are normal churn in a long companies_file: name them in the run log
    # (so their lines can be deleted) without counting them as errors.
    if cfg.get("companies_file"):
        gone = [e.split(":")[0].split("/", 1)[1] for e in errors if "board not found" in e]
        errors = [e for e in errors if "board not found" not in e]
        if gone:
            cfg.setdefault("_notes", []).append(f"{len(gone)} boards not found (closed or moved; delete them "
                                                f"from {cfg['companies_file']}): {', '.join(gone)}")
    if len(errors) > 5:
        warnings.append(f"{len(errors)} of {len(companies)} boards couldn't be read, "
                        f"e.g. {', '.join(e.split(':')[0] for e in errors[:3])}")
    else:
        warnings += errors
    if not out and (warnings or cfg.get("_notes")) and companies:
        raise RuntimeError("; ".join(warnings + cfg.get("_notes", [])))
    if warnings:
        cfg["_warnings"] = warnings
    return out


def greenhouse(cfg):
    return _fetch_all("greenhouse", cfg)


def lever(cfg):
    return _fetch_all("lever", cfg)


def ashby(cfg):
    return _fetch_all("ashby", cfg)


def workday(cfg):
    """Workday boards are searched slowly (see WD_INTERVAL), so a long list is split across runs:
    each run takes the next `boards_per_run` boards (by date), and the whole list comes round again
    every len/boards_per_run days."""
    companies = list(_companies(cfg))
    n, per = len(companies), int(cfg.get("boards_per_run") or 0)
    if 0 < per < n:
        start = (date.today().toordinal() * per) % n
        companies = (companies * 2)[start:start + per]
        cfg.setdefault("_notes", []).append(
            f"searched boards {start + 1}-{(start + per - 1) % n + 1} of {n} this run; "
            f"the rest come round on later days")
    return _fetch_all("workday", cfg, companies=companies, workers=1)


def fetcher(ats):
    """fetch(cfg) for one ATS type, for the source registry."""
    return lambda cfg: _fetch_all(ats, cfg)


def configured_slugs(sources):
    """(ats, slug) pairs already listed in sources.yaml, so discovery doesn't fetch them twice."""
    return {(s["type"], slug) for s in sources if s.get("type") in FETCH_ONE for slug, _ in _companies(s)}
