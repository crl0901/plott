"""Adzuna (US job aggregator) through its official API.

Needs ADZUNA_APP_ID and ADZUNA_APP_KEY in the board's .env. Each entry in `titles` is searched in job
titles (`title_only`), once nationwide for postings that mention remote work and, if `near` is set,
once around that place. Adzuna's API only returns a 500-character excerpt, so jobs that pass the
cheap filters get their full posting from the job link before scoring, and the "really remote?"
check then runs on it. Salaries Adzuna estimated itself (`salary_is_predicted`) are ignored.
"""
import os
import time

from ..web import get, html_to_text

API = "https://api.adzuna.com/v1/api/jobs/us/search/{page}"


def _get(params, page):
    resp = get(API.format(page=page), params=params)   # retries 429/5xx with backoff
    if resp is None:
        raise RuntimeError("adzuna: no response")
    if resp.status_code in (401, 403):
        raise RuntimeError(f"adzuna: HTTP {resp.status_code} (check ADZUNA_APP_ID / ADZUNA_APP_KEY)")
    if not resp.ok:   # never put the URL in errors: it carries the API key
        raise RuntimeError(f"adzuna: HTTP {resp.status_code}")
    return resp.json().get("results") or []


def _job(r, remote):
    loc = (r.get("location") or {}).get("display_name") or ""
    predicted = str(r.get("salary_is_predicted")) == "1"
    lo, hi = (None, None) if predicted else (r.get("salary_min"), r.get("salary_max"))
    lo, hi = (int(lo), int(hi or lo)) if lo else (None, None)
    et = " ".join(filter(None, ["contract" if r.get("contract_type") == "contract" else "",
                                (r.get("contract_time") or "").replace("_", "-")]))
    return {"source": "adzuna", "external_id": str(r["id"]), "company": (r.get("company") or {}).get("display_name"),
            "title": html_to_text(r.get("title")), "url": r.get("redirect_url"), "location": loc,
            "workplace": "remote" if remote else "unknown", "employment_type": et,
            "description": html_to_text(r.get("description")), "posted_at": r.get("created"),
            "pay": f"${lo:,} - ${hi:,}" if lo else "", "pay_min": lo, "pay_max": hi}


def fetch(cfg):
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("adzuna: ADZUNA_APP_ID and ADZUNA_APP_KEY aren't set (add them to the board's .env)")
    base = {"app_id": app_id, "app_key": app_key, "content-type": "application/json", "results_per_page": 50,
            "sort_by": "date", "max_days_old": int(cfg.get("days") or 3)}
    near = cfg.get("near")   # {where: "Austin, TX", miles: 35}
    out, seen = [], set()
    for title in cfg.get("titles") or []:
        searches = [({"title_only": title, "what": "remote"}, True, int(cfg.get("pages") or 2))]
        if near:
            searches.append(({"title_only": title, "where": near["where"],
                              "distance": round(float(near.get("miles", 35)) * 1.609)}, False, 1))
        for params, remote, pages in searches:
            for page in range(1, pages + 1):
                results = _get({**base, **params}, page)
                for r in results:   # the same job is often listed once per city: keep the first
                    j = _job(r, remote)
                    key = ((j["company"] or "").lower(), j["title"].lower())
                    if str(r.get("id")) not in seen and key not in seen:
                        seen.update({str(r.get("id")), key})
                        out.append(j)
                time.sleep(1)
                if len(results) < 50:
                    break
    return out
