"""Remote-only job sites with public feeds."""
import re
import time
import defusedxml.ElementTree as ET   # refuses entity-expansion and external-entity XML

from html import unescape

from ..web import get, html_to_text, pay_from_text


def _check(resp, name):
    if resp is None or not resp.ok:
        raise RuntimeError(f"{name}: HTTP {getattr(resp, 'status_code', 'error')}")
    return resp


def remoteok(cfg):
    data = _check(get("https://remoteok.com/api"), "remoteok").json()
    want = {t.lower() for t in cfg.get("tags", [])}
    out = []
    for j in data[1:]:  # first element is a legal notice
        tags = {t.lower() for t in j.get("tags", [])}
        if want and not (want & tags):
            continue
        desc = html_to_text(j.get("description"))
        lo, hi = j.get("salary_min") or None, j.get("salary_max") or None
        out.append({"source": "remoteok", "external_id": str(j["id"]), "company": j.get("company"),
                    "title": j.get("position"), "url": j.get("url"), "location": j.get("location") or "Remote",
                    "workplace": "remote", "description": desc, "posted_at": j.get("date"),
                    "pay": f"${lo:,} - ${hi:,}" if lo and hi else "", "pay_min": lo, "pay_max": hi})
    return out


def remotive(cfg):
    params = {k: cfg[k] for k in ("category", "search") if cfg.get(k)}
    data = _check(get("https://remotive.com/api/remote-jobs", params=params), "remotive").json()
    out = []
    for j in data.get("jobs", []):
        desc = html_to_text(j.get("description"))
        pay, lo, hi = pay_from_text(j.get("salary") or "")
        out.append({"source": "remotive", "external_id": str(j["id"]), "company": j.get("company_name"),
                    "title": j.get("title"), "url": j.get("url"), "location": j.get("candidate_required_location"),
                    "workplace": "remote", "employment_type": (j.get("job_type") or "").replace("_", "-"),
                    "description": desc, "posted_at": j.get("publication_date"),
                    "pay": j.get("salary") or "", "pay_min": lo, "pay_max": hi})
    return out


def weworkremotely(cfg):
    feeds = cfg.get("feeds") or [cfg.get("feed", "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss")]
    out = []
    for feed in feeds:
        root = ET.fromstring(_check(get(feed), "weworkremotely").content)
        for item in root.iter("item"):
            g = lambda tag: (item.findtext(tag) or "").strip()
            company, _, title = g("title").partition(": ")
            desc = html_to_text(g("description"))
            pay, lo, hi = pay_from_text(desc)
            out.append({"source": "weworkremotely", "external_id": g("guid") or g("link"),
                        "company": company if title else "", "title": title or company, "url": g("link"),
                        "location": g("region") or "Remote", "workplace": "remote",
                        "employment_type": g("type").lower(), "description": desc,
                        "posted_at": g("pubDate"), "pay": pay, "pay_min": lo, "pay_max": hi})
    return out


def _yearly_pay(lo, hi, period, currency):
    """(text, min, max) from a structured salary; min/max only for yearly USD figures."""
    if not lo and not hi:
        return "", None, None
    lo, hi = int(float(lo or hi)), int(float(hi or lo))
    yearly = (period or "year").lower().startswith(("year", "annual"))
    text = f"{'$' if (currency or 'USD') == 'USD' else (currency or '') + ' '}{lo:,} - {hi:,}" + ("" if yearly else f" per {period}")
    return (text, lo, hi) if yearly and (currency or "USD") == "USD" else (text, None, None)


def _remote_location(countries):
    """A remote job's country list as a location the filters understand."""
    countries = [c for c in countries or [] if c]
    if not countries:
        return "Remote (worldwide)"
    if "United States" in countries or "USA" in countries:
        return "Remote (United States)"
    return "Remote (" + ", ".join(countries[:6]) + ("…" if len(countries) > 6 else "") + ")"


def himalayas(cfg):
    """himalayas.app search: each term in `search`, `pages` pages of 20 (default 2). Jobs restricted to
    countries that don't include the US are skipped unless `us_only: false`."""
    out, seen = [], set()
    for term in cfg.get("search") or []:
        for page in range(1, int(cfg.get("pages") or 2) + 1):
            data = _check(get("https://himalayas.app/jobs/api/search", params={"q": term, "page": page}),
                          "himalayas").json()
            jobs = data.get("jobs") or []
            for j in jobs:
                countries = j.get("locationRestrictions") or []
                if j["guid"] in seen or (cfg.get("us_only", True) and countries and "United States" not in countries):
                    continue
                seen.add(j["guid"])
                pay, lo, hi = _yearly_pay(j.get("minSalary"), j.get("maxSalary"), j.get("salaryPeriod"), j.get("currency"))
                out.append({"source": "himalayas", "external_id": j["guid"], "company": j.get("companyName"),
                            "title": j.get("title"), "url": j.get("applicationLink") or j["guid"],
                            "location": _remote_location(j.get("locationRestrictions")), "workplace": "remote",
                            "employment_type": (j.get("employmentType") or "").lower(),
                            "description": html_to_text(j.get("description")), "posted_at": str(j.get("pubDate") or ""),
                            "pay": pay, "pay_min": lo, "pay_max": hi})
            if len(jobs) < 20:
                break
            time.sleep(1)
    return out


def jobicy(cfg):
    """jobicy.com remote jobs: each tag in `tags` (US-only by default; `geo` changes it), up to 100 each."""
    out, seen = [], set()
    for tag in cfg.get("tags") or [None]:
        params = {"count": 100}
        if (geo := cfg.get("geo", "usa")) and geo != "anywhere":   # "anywhere": no region filter
            params["geo"] = geo
        if tag:
            params["tag"] = tag
        if cfg.get("industry"):
            params["industry"] = cfg["industry"]
        data = _check(get("https://jobicy.com/api/v2/remote-jobs", params=params), "jobicy").json()
        for j in data.get("jobs") or []:
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            pay, lo, hi = _yearly_pay(j.get("salaryMin"), j.get("salaryMax"), j.get("salaryPeriod"), j.get("salaryCurrency"))
            desc = html_to_text(j.get("jobDescription"))
            if not lo:
                pay2, lo, hi = pay_from_text(desc)
                pay = pay or pay2
            jt = j.get("jobType")
            out.append({"source": "jobicy", "external_id": str(j["id"]), "company": j.get("companyName"),
                        "title": unescape(j.get("jobTitle")), "url": j.get("url"),
                        "location": _remote_location([j.get("jobGeo")] if j.get("jobGeo") not in (None, "Anywhere") else []),
                        "workplace": "remote", "employment_type": ", ".join(jt).lower() if isinstance(jt, list) else (jt or "").lower(),
                        "description": desc, "posted_at": j.get("pubDate"), "pay": pay, "pay_min": lo, "pay_max": hi})
        time.sleep(1)
    return out
