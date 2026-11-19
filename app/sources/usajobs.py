"""USAJOBS (federal jobs) through the official search API.

Needs USAJOBS_API_KEY and USAJOBS_EMAIL (the address the key was issued to) in the
board's .env.
Searches by job series (`series`, e.g. "2210" for IT) and/or `keywords`, nationwide; the cheap
filters then keep remote and local positions. Federal titles rarely name the technology ("IT
Specialist (APPSW)"), so jobs found by a keyword don't have to pass title_include. Announcements open only to current federal
employees are skipped unless `public_only: false`.
"""
import os
import time

from ..web import html_to_text, session

API = "https://data.usajobs.gov/api/search"
SCHEDULE = {"1": "full-time", "2": "part-time", "3": "shift work", "4": "intermittent", "5": "job sharing",
            "6": "multiple schedules"}
OFFERING = {"15317": "permanent", "15318": "temporary", "15319": "term", "15320": "detail",
            "15321": "temporary promotion", "15322": "seasonal", "15323": "summer", "15326": "recent graduates",
            "15327": "multiple", "15328": "internship", "15522": "intermittent"}


def _search(headers, params):
    out = []
    for page in range(1, 4):   # 500 per page; a nationwide IT search is a few hundred
        resp = session.get(API, params={**params, "ResultsPerPage": 500, "Page": page}, headers=headers, timeout=60)
        if not resp.ok:
            raise RuntimeError(f"usajobs: HTTP {resp.status_code}")
        sr = resp.json().get("SearchResult") or {}
        items = sr.get("SearchResultItems") or []
        out += [i["MatchedObjectDescriptor"] for i in items]
        if len(out) >= int(sr.get("SearchResultCountAll") or 0) or not items:
            break
        time.sleep(1)
    return out


def _name(entries, codes):
    e = (entries or [{}])[0]
    return (e.get("Name") or codes.get(str(e.get("Code")), "")).lower()


def _job(m):
    d = (m.get("UserArea") or {}).get("Details") or {}
    remote = bool(d.get("RemoteIndicator"))
    locs = [l.get("LocationName") for l in m.get("PositionLocation") or [] if l.get("LocationName")]
    loc = "Remote (United States)" if remote else "; ".join(dict.fromkeys(locs)) or m.get("PositionLocationDisplay", "")
    pay = next((r for r in m.get("PositionRemuneration") or [] if r.get("RateIntervalCode") == "PA"), None)
    lo, hi = (int(float(pay["MinimumRange"])), int(float(pay["MaximumRange"]))) if pay else (None, None)
    schedule, offering = _name(m.get("PositionSchedule"), SCHEDULE), _name(m.get("PositionOfferingType"), OFFERING)
    grade = f"{(m.get('JobGrade') or [{}])[0].get('Code', '')}-{d.get('LowGrade', '')}" + (
        f"/{d['HighGrade']}" if d.get("HighGrade") and d.get("HighGrade") != d.get("LowGrade") else "")
    facts = [f"Agency: {m.get('OrganizationName')} ({m.get('DepartmentName')})", f"Grade: {grade}",
             f"Schedule: {schedule or 'not stated'}; appointment: {offering or 'not stated'}",
             f"Remote: {'yes' if remote else 'no'}; telework eligible: {'yes' if d.get('TeleworkEligible') else 'no'}",
             f"Security clearance: {d.get('SecurityClearance') or 'not stated'}",
             f"Who may apply: {d.get('HiringPathDisplay') or ', '.join(d.get('HiringPath') or [])}",
             f"Applications close: {(m.get('ApplicationCloseDate') or '')[:10]}"]
    duties = d.get("MajorDuties") or []
    desc = "\n\n".join(filter(None, ["\n".join(facts), html_to_text(d.get("JobSummary")),
                                     "Major duties:\n" + "\n".join(f"- {html_to_text(x)}" for x in duties) if duties else "",
                                     html_to_text(m.get("QualificationSummary")),
                                     html_to_text(d.get("Requirements"))]))
    return {"source": "usajobs", "external_id": str(m.get("PositionID") or m.get("PositionURI")),
            "company": m.get("OrganizationName") or m.get("DepartmentName"), "title": m.get("PositionTitle"),
            "url": (m.get("PositionURI") or "").replace(":443", ""), "location": loc,
            "workplace": "remote" if remote else "hybrid" if d.get("TeleworkEligible") else "onsite",
            "employment_type": "temporary" if offering in ("temporary", "seasonal", "summer", "intermittent")
                               else "internship" if offering == "internship" else schedule,
            "description": desc[:60_000], "posted_at": m.get("PublicationStartDate"),
            "pay": f"${lo:,} - ${hi:,} per year" if lo else "", "pay_min": lo, "pay_max": hi}


def fetch(cfg):
    key, email = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not key or not email:
        raise RuntimeError("usajobs: USAJOBS_API_KEY and USAJOBS_EMAIL aren't set (add them to the board's .env)")
    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    base = {"DatePosted": min(int(cfg.get("days") or 30), 60)}
    # keyword searches first, so a job found both ways keeps its topic match (see Rules.pre)
    searches = [{"Keyword": k} for k in cfg.get("keywords") or []]
    searches += [{"JobCategoryCode": str(s)} for s in cfg.get("series") or []]
    if not searches:
        raise RuntimeError("usajobs: set `series` and/or `keywords`")
    out, seen = [], set()
    for params in searches:
        for m in _search(headers, {**base, **params}):
            d = (m.get("UserArea") or {}).get("Details") or {}
            if cfg.get("public_only", True) and "public" not in (d.get("HiringPath") or []):
                continue
            j = _job(m)
            j["_topic_match"] = "Keyword" in params
            if j["external_id"] not in seen:
                seen.add(j["external_id"])
                out.append(j)
        time.sleep(1)
    return out
