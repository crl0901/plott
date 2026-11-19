"""dice.com job search.

No API key: Dice's search pages are server-rendered and embed the result list as JSON (30 jobs a
page, with a short summary). Jobs that pass the cheap filters get their full description from the
job's detail page (its schema.org JobPosting data).
"""
import json
import logging
import re
import time

from ..web import get, html_to_text

log = logging.getLogger(__name__)
SEARCH_URL = "https://www.dice.com/jobs"
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_WORKPLACE = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-Site"}
_POSTED = [(1, "ONE"), (3, "THREE"), (7, "SEVEN")]


def _job_list(html):
    """The search results JSON embedded in the page's Next.js flight data."""
    txt = "".join(json.loads(f'"{c}"') for c in _FLIGHT.findall(html))
    i = txt.find('{"jobList":')
    if i < 0:
        raise RuntimeError("dice: page layout changed (no job list found)")
    obj, _ = json.JSONDecoder().raw_decode(txt, i)
    return obj["jobList"]


def _salary(text):
    """'USD 120,001.00 - 160,000.00 per year' or '100,000 - 120,000' -> (min, max). Hourly rates and
    text like 'Depends on Experience' -> (None, None)."""
    if not text or re.search(r"hour|/\s*hr\b|\bhr\b", text, re.I):
        return None, None
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    nums = [int(n) for n in nums if n >= 10_000]
    return (min(nums), max(nums)) if nums else (None, None)


def _workplace(types):
    types = types or []
    if "Remote" in types:
        return "remote"
    if "Hybrid" in types:
        return "hybrid"
    if "On-Site" in types:
        return "onsite"
    return "unknown"


def _job(j):
    lo, hi = _salary(j.get("salary"))
    wp = _workplace(j.get("workplaceTypes"))
    loc = (j.get("jobLocation") or {}).get("displayName") or ""
    return {
        "source": "dice", "external_id": j.get("guid") or j["id"], "company": j.get("companyName"),
        "title": j.get("title"), "url": j.get("detailsPageUrl"),
        "location": loc or ("United States (remote)" if wp == "remote" else ""),
        "workplace": wp, "employment_type": (j.get("employmentType") or "").lower(),
        "description": j.get("summary") or "", "posted_at": j.get("postedDate"),
        "pay": j.get("salary") or "", "pay_min": lo, "pay_max": hi,
    }


def fetch(cfg):
    base = {"countryCode": "US"}
    wp = [_WORKPLACE[w] for w in cfg.get("workplace", ["remote"]) if w in _WORKPLACE]
    if wp:
        base["filters.workplaceTypes"] = "|".join(wp)
    if cfg.get("location"):
        base.update(location=cfg["location"], radius=int(cfg.get("radius", 35)), radiusUnit="mi")
    days = int(cfg.get("days", 7))
    posted = next((code for d, code in _POSTED if days <= d), None)
    if posted:
        base["filters.postedDate"] = posted
    if cfg.get("employment_type", "FULLTIME"):
        base["filters.employmentType"] = cfg.get("employment_type", "FULLTIME")
    # Default to employers posting their own jobs; "Recruiter" listings are mostly staffing agencies.
    employers = cfg.get("employer_types", ["Direct Hire"])
    if employers:
        base["filters.employerType"] = "|".join(employers)

    out, failed = {}, []
    for kw in cfg.get("keywords", []):
        for page in range(1, int(cfg.get("pages", 2)) + 1):
            jobs = _search({**base, "q": kw, "page": page})
            if jobs is None:
                failed.append(kw)
                break
            data = jobs.get("data") or []
            for j in data:
                out.setdefault(j.get("guid") or j["id"], _job(j))
            meta = jobs.get("meta") or {}
            if len(data) < (meta.get("pageSize") or 30):
                break
            time.sleep(1)
        time.sleep(1)
    if failed:
        if not out:
            raise RuntimeError(f"dice: no results page could be read ({', '.join(failed)})")
        log.warning("dice: skipped %s (page without results data)", ", ".join(failed))
    return list(out.values())


def _search(params):
    """One results page; retried once, since Dice now and then serves a page without the data."""
    for attempt in range(2):
        resp = get(SEARCH_URL, params=params)
        if resp is not None and resp.ok:
            try:
                return _job_list(resp.text)
            except (RuntimeError, ValueError, KeyError):
                pass
        time.sleep(3)
    return None


def enrich(job):
    """Full description (and salary, if the search didn't have one) from the job's detail page."""
    resp = get(job["url"], tries=2)
    if resp is None or not resp.ok:
        return job
    for m in _LD.finditer(resp.text):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("@type") != "JobPosting":
            continue
        desc = html_to_text(d.get("description") or "")
        if len(desc) > len(job.get("description") or ""):
            job["description"] = desc[:60_000]
        if d.get("jobLocationType") == "TELECOMMUTE" and job.get("workplace") == "unknown":
            job["workplace"] = "remote"
        return job
    return job
