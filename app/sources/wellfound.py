"""wellfound.com (formerly AngelList Talent): startup jobs.

No API key: Wellfound's role pages (/role/r/<role> for remote jobs, /role/l/<role>/<place> for a
place) are server-rendered and embed the results, with full descriptions, in their page data.
robots.txt allows /role/ and /jobs/ (it disallows /search). Pages are not strictly newest first,
so the board's age rule drops old listings. Applying needs a free Wellfound account.
"""
import json
import logging
import re
import time

from ..web import get, money

log = logging.getLogger(__name__)
BASE = "https://wellfound.com"
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SLUG = re.compile(r"[a-z0-9-]+")


def _page(url):
    """(jobs, {job ref: company name}) from one role page; "unknown" for a role or place Wellfound
    doesn't have; None when it couldn't be read."""
    resp = get(url)
    if resp is None or not resp.ok:
        return None
    if "/role/" not in resp.url:   # Wellfound sends unknown role or place names to its general page
        return "unknown"
    m = _NEXT.search(resp.text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))["props"]["pageProps"]["apolloState"]["data"]
    except (ValueError, KeyError, TypeError):
        return None
    jobs = [v for k, v in data.items() if k.startswith("JobListingSearchResult:")]
    owners = {ref["__ref"]: s.get("name") for k, s in data.items() if k.startswith("StartupResult:")
              for ref in s.get("highlightedJobListings") or []}
    return jobs, owners


def _job(j, company):
    kind = (j.get("remoteConfig") or {}).get("kind") or ""
    if j.get("remote"):
        countries = j.get("acceptedRemoteLocationNames") or []
        loc = "Remote (" + ", ".join(countries[:6]) + ")" if countries else "Remote"
        wp = "remote"
    else:
        loc = "; ".join(j.get("locationNames") or [])
        wp = "hybrid" if "HYBRID" in kind else "onsite" if loc else "unknown"
    pay = (j.get("compensation") or "").split("•")[0].strip()
    vals = money(pay)
    return {
        "source": "wellfound", "external_id": str(j["id"]), "company": company,
        "title": j.get("title"), "url": f"{BASE}/jobs/{j['id']}-{j.get('slug') or 'job'}",
        "location": loc, "workplace": wp, "employment_type": j.get("jobType") or "",
        "description": j.get("description") or "", "posted_at": j.get("liveStartAt"),
        "pay": pay if vals else "", "pay_min": min(vals) if vals else None, "pay_max": max(vals) if vals else None,
    }


def fetch(cfg):
    place = str(cfg.get("location") or "").strip().lower()
    if place and not _SLUG.fullmatch(place):
        raise RuntimeError(f"wellfound: location must be a Wellfound place name like 'austin', not {place!r}")
    out, failed, unknown = {}, [], []
    for role in cfg.get("roles", []):
        role = str(role).strip().lower()
        if not _SLUG.fullmatch(role):
            failed.append(role)
            continue
        base = f"{BASE}/role/l/{role}/{place}" if place else f"{BASE}/role/r/{role}"
        for page in range(1, int(cfg.get("pages", 3)) + 1):
            got = _page(base if page == 1 else f"{base}?page={page}")
            if got == "unknown":
                if page == 1:
                    unknown.append(role)
                break
            if got is None:
                if page == 1:
                    failed.append(role)
                break
            jobs, owners = got
            for j in jobs:
                if j.get("id"):
                    out.setdefault(str(j["id"]), _job(j, owners.get(f"JobListingSearchResult:{j['id']}") or ""))
            if not jobs:
                break
            time.sleep(3)
        time.sleep(3)
    if unknown:
        msg = (f"wellfound: no such role{'s' if len(unknown) > 1 else ''} {', '.join(unknown)}"
               + (f" in {place}" if place else "") + " (use the names in wellfound.com/role/r/<role> links)")
        if not out:
            raise RuntimeError(msg)
        cfg.setdefault("_warnings", []).append(msg)   # shown in the run log
    if failed:
        if not out:
            raise RuntimeError(f"wellfound: no role page could be read ({', '.join(failed)})")
        log.warning("wellfound: skipped %s (page could not be read)", ", ".join(failed))
    return list(out.values())
