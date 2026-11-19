"""Hacker News "Ask HN: Who is hiring?" threads, through HN's public Algolia API.

Each top-level comment is one company's post. By convention its first line reads
"Company | Role(s) | Location | Remote/Onsite | Salary | ...", which gives the title, location, and
pay the cheap filters need; the whole comment is the description.
"""
import re
from datetime import datetime, timezone

from ..web import get, html_to_text, pay_from_text

API = "https://hn.algolia.com/api/v1"
ROLE = re.compile(r"engineer|developer|admin|architect|sre\b|devops|analyst|manager|lead\b|specialist|"
                  r"consultant|scientist|designer|head of|director|technician|operations|ops\b", re.I)

NOT_ROLE = re.compile(r"\s*(remote|onsite|on-site|in[- ]office|hybrid|full[- ]?time|part[- ]?time|contract|"
                      r"intern|visa|[$€£]|\d)", re.I)


def _threads(months):
    resp = get(f"{API}/search_by_date", params={"tags": "story,author_whoishiring", "hitsPerPage": 12})
    if resp is None or not resp.ok:
        raise RuntimeError(f"hackernews: HTTP {getattr(resp, 'status_code', 'error')}")
    hits = [h for h in resp.json().get("hits", []) if (h.get("title") or "").lower().startswith("ask hn: who is hiring")]
    return hits[:months]


def _post(c):
    text = c.get("text") or ""
    first = html_to_text(re.split(r"<p>", text, maxsplit=1)[0]).strip()
    parts = [p.strip() for p in first.split("|") if p.strip() and not re.search(r"https?://|www\.", p)]
    if len(parts) < 2:
        return None
    company = re.sub(r"\s*\(.*?\)\s*$", "", parts[0])[:80]
    roles = [p for p in parts[1:] if ROLE.search(p) and len(p) < 120]
    other = [p for p in parts[1:] if not NOT_ROLE.match(p)]
    title = "; ".join(roles) or (other or parts[1:])[0][:120]
    low = first.lower()
    workplace = ("remote" if re.search(r"\bremote\b", low) else "hybrid" if "hybrid" in low
                 else "onsite" if re.search(r"onsite|on-site|in office|in-office", low) else "unknown")
    loc = "; ".join(p for p in parts[1:] if p not in roles)[:200]
    desc = html_to_text(text)
    pay, lo, hi = pay_from_text(first)
    return {"source": "hackernews", "external_id": str(c["id"]), "company": company, "title": title,
            "url": f"https://news.ycombinator.com/item?id={c['id']}", "location": loc, "workplace": workplace,
            "employment_type": "contract" if re.search(r"\bcontract\b(?!.*full[- ]time)", low) else "",
            "description": desc[:60_000], "posted_at": c.get("created_at"),
            "pay": pay, "pay_min": lo, "pay_max": hi}


def fetch(cfg):
    """Top-level posts from the latest `months` (default 1) monthly threads."""
    out = []
    for hit in _threads(int(cfg.get("months") or 1)):
        resp = get(f"{API}/items/{hit['objectID']}", timeout=60)
        if resp is None or not resp.ok:
            raise RuntimeError(f"hackernews: thread {hit['objectID']}: HTTP {getattr(resp, 'status_code', 'error')}")
        for c in resp.json().get("children") or []:
            if c.get("text") and (job := _post(c)):
                out.append(job)
    return out
