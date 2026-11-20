"""talent.com job search.

No API key: the search page embeds its results in Next.js flight data (20 jobs a page, with a short
excerpt). Talent.com shows at most 2 pages per search and has no working remote filter, so the
search is by place (default: the whole US) and remote jobs are found by the board's own rules.
Jobs that pass the cheap filters get their full description from the job page's schema.org
JobPosting data. robots.txt allows /jobs and /view.
"""
import json
import logging
import re
import time

from ..web import get, html_to_text
from .generic import enrich_from_url

log = logging.getLogger(__name__)
BASE = "https://www.talent.com"
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.S)
_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_REMOTE = {"yes": "remote", "remote": "remote", "country": "remote", "hybrid": "hybrid"}   # "country": remote anywhere in the country


def _job_list(html):
    """The results array in the page's flight data."""
    txt = "".join(json.loads(f'"{c}"') for c in _FLIGHT.findall(html or ""))
    i = txt.find('"jobs":[')
    if i < 0:
        raise RuntimeError("talent: page layout changed (no job list found)")
    jobs, _ = json.JSONDecoder().raw_decode(txt, i + len('"jobs":'))
    return jobs


def _pay(j):
    lo, hi = j.get("enrich_salary_min") or None, j.get("enrich_salary_max") or None
    if not lo or str(j.get("enrich_salary_type") or "").lower() not in ("", "year", "yearly", "annual"):
        return "", None, None
    lo, hi = int(lo), int(hi or lo)
    if lo < 30_000:
        return "", None, None
    return (f"${lo:,} - ${hi:,} a year" if hi != lo else f"${lo:,} a year"), lo, hi


def _job(j):
    loc = ", ".join(filter(None, [j.get("enrich_geo_city"), j.get("enrich_geo_region1")])) or j.get("source_location") or ""
    wp = _REMOTE.get(str(j.get("enrich_geo_remote") or "").lower())
    if not wp:
        wp = "remote" if "remote" in loc.lower() else "onsite" if loc else "unknown"
    pay, lo, hi = _pay(j)
    return {
        "source": "talent", "external_id": j["id"],
        "company": j.get("enrich_company_name") or j.get("system_company_name"),
        "title": j.get("source_title"), "url": f"{BASE}/view?id={j['id']}",
        "location": loc, "workplace": wp,
        "employment_type": ", ".join(j.get("enrich_jobtype") or []).lower(),
        "description": j.get("source_jobdesc_text") or "",
        "posted_at": j.get("system_date_found"),   # when Talent.com first saw it
        "pay": pay, "pay_min": lo, "pay_max": hi,
    }


def fetch(cfg):
    base = {"l": cfg.get("location") or "United States"}
    if cfg.get("location"):
        base["radius"] = int(cfg.get("radius", 35))
    if days := int(cfg.get("days", 7)):
        base["date"] = days
    out, failed = {}, []
    for kw in cfg.get("keywords", []):
        for page in range(1, min(int(cfg.get("pages", 2)), 2) + 1):
            resp = get(f"{BASE}/jobs", params={**base, "k": kw, "p": page})
            try:
                jobs = _job_list(resp.text) if resp is not None and resp.ok else None
            except (RuntimeError, ValueError):
                jobs = None
            if jobs is None:
                if page == 1:
                    failed.append(kw)
                break
            for j in jobs:
                if j.get("id") and str(j.get("enrich_geo_country") or "us").lower() == "us":
                    out.setdefault(j["id"], _job(j))
            if len(jobs) < 20:
                break
            time.sleep(2)
        time.sleep(2)
    if failed:
        if not out:
            raise RuntimeError(f"talent: no results page could be read ({', '.join(failed)})")
        log.warning("talent: skipped %s (page could not be read)", ", ".join(failed))
    return list(out.values())


def enrich(job):
    """Full description from the job page's schema.org JobPosting data."""
    resp = get(job["url"], tries=2)
    if resp is None or not resp.ok:
        job["_retry"] = True
        return job
    for m in _LD.finditer(resp.text):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        items = d.get("@graph", [d]) if isinstance(d, dict) else d if isinstance(d, list) else []
        d = next((x for x in items if isinstance(x, dict) and x.get("@type") == "JobPosting"), None)
        if d:
            desc = html_to_text(d.get("description") or "")
            if len(desc) > len(job.get("description") or ""):
                job["description"] = desc[:60_000]
            if d.get("jobLocationType") == "TELECOMMUTE":
                job["workplace"] = "remote"
            return job
    return enrich_from_url(job)   # some job pages have no structured data: read the page text
