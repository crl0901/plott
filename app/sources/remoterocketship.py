"""remoterocketship.com.

With REMOTEROCKETSHIP_API_KEY set, uses their jobs API: filtering happens on
their side (US, remote, full-time, seniority, salary floor, excluded keywords) and every job comes
with its full description. Without a key, falls back to reading the public search page's embedded
Next.js data (about 20 jobs per title, summaries only).
"""
import json
import os
import re

import requests

from ..web import get

API_URL = "https://www.remoterocketship.com/api/openclaw/jobs/"  # trailing slash required
# Their firewall rejects python-requests' default user agent; any plain identifier works.
API_UA = "Plott/1.0 (job tracker)"


def fetch(cfg):
    key = os.environ.get("REMOTEROCKETSHIP_API_KEY")
    return _fetch_api(cfg, key) if key else _fetch_page(cfg)


def _job(j, desc):
    sal = j.get("salaryRange") or {}
    yearly = "year" in (sal.get("salaryType") or "")
    co = j.get("company") or {}
    loc = j.get("location") or "United States (remote)"
    return {
        "source": "remoterocketship", "external_id": str(j["id"]), "company": co.get("name"),
        "title": j.get("roleTitle"),
        "url": j.get("url") or f"https://www.remoterocketship.com/company/{co.get('slug')}/jobs/{j.get('slug')}",
        "location": loc, "workplace": j.get("locationType") or "remote",
        "employment_type": j.get("employmentType") or "", "description": desc,
        "posted_at": j.get("created_at"), "pay": sal.get("salaryHumanReadableText") or "",
        "pay_min": sal.get("minSalaryAsUSD") if yearly else None,
        "pay_max": sal.get("maxSalaryAsUSD") if yearly else None,
    }


def _fetch_api(cfg, key):
    filters = {
        "itemsPerPage": int(cfg.get("items_per_page", 50)),
        "jobTitleFilters": cfg.get("job_titles", []),
        "locationFilters": cfg.get("countries", ["United States"]),
        "seniorityFilters": cfg.get("seniority", ["senior", "expert"]),
        "employmentTypeFilters": cfg.get("employment_types", ["full-time"]),
        "showRemoteJobs": True, "showHybridJobs": False, "showOnsiteJobs": False,
        "excludedKeywordFilters": cfg.get("exclude_keywords", []),
        "sortBy": "DateAdded",
    }
    if cfg.get("min_salary"):
        filters["minSalaryFilter"] = int(cfg["min_salary"])
        filters["showJobsWithoutSalaryWithMinSalaryFilter"] = True
    headers = {"Authorization": f"Bearer {key}", "User-Agent": API_UA}
    out = {}
    for page in range(1, int(cfg.get("pages", 4)) + 1):
        try:
            resp = requests.post(API_URL, json={"filters": {**filters, "page": page}, "includeJobDescription": True},
                                 headers=headers, timeout=60)
        except requests.RequestException as e:
            raise RuntimeError(f"remoterocketship API: {e}")
        if resp.status_code != 200:
            try:
                msg = resp.json().get("message") or resp.json().get("error", {}).get("message")
            except ValueError:
                msg = resp.text[:120]
            raise RuntimeError(f"remoterocketship API: HTTP {resp.status_code} {msg}")
        data = resp.json()
        for j in data.get("jobOpenings") or []:
            desc = "\n\n".join(filter(None, [j.get("roleDescription"), j.get("roleRequirements"), j.get("benefits")]))
            if not desc:
                desc = j.get("twoLineJobDescriptionSummary") or ""
            if j.get("techStack"):
                desc += "\n\nTech stack: " + ", ".join(j["techStack"])
            out.setdefault(str(j["id"]), _job(j, desc))
        if not (data.get("pagination") or {}).get("hasNextPage"):
            break
    return list(out.values())


def _fetch_page(cfg):
    out = {}
    for title in cfg.get("job_titles", []):
        for page in range(1, int(cfg.get("page_fallback_pages", 1)) + 1):
            params = {"jobTitle": title, "sort": "DateAdded"}
            if page > 1:
                params["page"] = page
            resp = get("https://www.remoterocketship.com/", params=params)
            if resp is None or not resp.ok:
                raise RuntimeError(f"remoterocketship: HTTP {getattr(resp, 'status_code', 'error')}")
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
            if not m:
                raise RuntimeError("remoterocketship: page layout changed (no __NEXT_DATA__)")
            jobs = json.loads(m.group(1))["props"]["pageProps"].get("initialJobOpenings") or []
            for j in jobs:
                desc = "\n".join(filter(None, [j.get("twoLineJobDescriptionSummary"),
                                               "Tech stack: " + ", ".join(j.get("techStack") or [])]))
                out.setdefault(str(j["id"]), {**_job(j, desc), "location": j.get("location") or ""})
            if len(jobs) < 20:
                break
    return list(out.values())
