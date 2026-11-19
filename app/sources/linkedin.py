"""LinkedIn guest job search (no login)."""
import re
import time
import urllib.parse

from ..web import get, html_to_text, pay_from_text

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
WT = {"onsite": "1", "remote": "2", "hybrid": "3"}


def _txt(s):
    return html_to_text(s).replace("\n", " ").strip()


def _cards(page):
    out = []
    for card in page.split("<li>")[1:]:
        m = re.search(r"jobPosting:(\d+)", card)
        if not m:
            continue

        def g(pat):
            r = re.search(pat, card, re.S)
            return _txt(r.group(1)) if r else ""

        out.append({
            "external_id": m.group(1),
            "title": g(r'base-search-card__title">(.*?)</h3>'),
            "company": g(r'base-search-card__subtitle">(.*?)</h4>'),
            "location": g(r'job-search-card__location">(.*?)</span>'),
            "posted_at": (re.search(r'datetime="([^"]+)"', card) or [None, ""])[1],
        })
    return out


def fetch(cfg):
    workplace = cfg.get("workplace", ["remote"])
    base = {
        "location": cfg.get("location", "United States"),
        "f_TPR": f"r{int(cfg.get('days', 7)) * 86400}",
        "f_WT": ",".join(WT[w] for w in workplace if w in WT),
    }
    if cfg.get("full_time_only", True):
        base["f_JT"] = "F"
    if cfg.get("distance"):
        base["distance"] = str(cfg["distance"])
    label = "remote" if workplace == ["remote"] else "/".join(workplace)
    jobs = {}
    for kw in cfg.get("keywords", []):
        for start in range(0, 10 * int(cfg.get("pages", 3)), 10):
            resp = get(SEARCH + urllib.parse.urlencode({**base, "keywords": kw, "start": start}))
            got = _cards(resp.text) if resp is not None and resp.ok else []
            for c in got:
                jobs.setdefault(c["external_id"], {
                    **c, "source": "linkedin", "workplace": label if label in ("remote", "hybrid", "onsite") else "unknown",
                    "url": f"https://www.linkedin.com/jobs/view/{c['external_id']}",
                })
            if len(got) < 10:
                break
            time.sleep(1.5)
    return list(jobs.values())


def enrich(job):
    resp = get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job['external_id']}")
    time.sleep(1.2)
    if resp is None or not resp.ok:
        return job
    page = resp.text
    job["ats_links"] = re.findall(r'https?(?::|%3A)(?://|%2F%2F)[^"\'<>\s]*(?:greenhouse\.io|ashbyhq\.com|lever\.co)[^"\'<>\s]*',
                                  page)
    m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', page, re.S)
    if m:
        job["description"] = html_to_text(m.group(1))
    crit = dict((_txt(k), _txt(v)) for k, v in re.findall(
        r'description__job-criteria-subheader">\s*(.*?)\s*</h3>\s*<span[^>]*>\s*(.*?)\s*</span>', page, re.S))
    if crit.get("Employment type"):
        job["employment_type"] = crit["Employment type"].lower()
    sal = re.search(r'salary compensation__salary">\s*(.*?)\s*<', page, re.S)
    pay, lo, hi = pay_from_text(_txt(sal.group(1)) if sal else "")
    if not lo:
        pay, lo, hi = pay_from_text(job.get("description", ""))
    if lo:
        job.update(pay=pay, pay_min=lo, pay_max=hi)
    return job
