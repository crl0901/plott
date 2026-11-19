"""Any careers/search page URL: fetch it and have Claude pull out the listings."""
import hashlib
import logging

from .. import claude
from ..web import get, html_to_text, pay_from_text

log = logging.getLogger(__name__)
MAX_PAGE_CHARS = 300_000


def fetch(cfg):
    if not claude.available():
        raise RuntimeError("`url` sources need Claude to read the page; skipped")
    url = cfg["url"]
    resp = get(url)
    if resp is None or not resp.ok:
        raise RuntimeError(f"url {url}: HTTP {getattr(resp, 'status_code', 'error')}")
    text = html_to_text(resp.text, base_url=url, keep_links=True)
    if len(text) > MAX_PAGE_CHARS:
        raise RuntimeError(f"url {url}: page is {len(text):,} chars, too large to extract; use a narrower search URL")
    if len(text) < 200:
        raise RuntimeError(f"url {url}: page has almost no text (probably JavaScript-rendered); not supported")
    out = []
    for j in claude.extract_jobs(text, url, cfg.get("company", ""), cfg.get("model", claude.DEFAULT_MODEL)):
        if not j.get("url") or not j.get("title"):
            continue
        pay, lo, hi = pay_from_text(j.get("pay"))
        out.append({"source": "url", "external_id": hashlib.sha1(j["url"].encode()).hexdigest()[:16],
                    "company": j.get("company") or cfg.get("company", ""), "title": j["title"], "url": j["url"],
                    "location": j.get("location", ""), "workplace": "remote" if "remote" in j.get("location", "").lower()
                    else "unknown", "pay": j.get("pay", ""), "pay_min": lo, "pay_max": hi, "description": ""})
    return out


def enrich_from_url(job):
    resp = get(job["url"], tries=2)
    if resp is not None and resp.ok and "html" in resp.headers.get("content-type", ""):
        text = html_to_text(resp.text)
        if len(text) > len(job.get("description") or ""):
            job["description"] = text[:60_000]
            if not job.get("pay_min"):
                pay, lo, hi = pay_from_text(text)
                if lo:
                    job.update(pay=job.get("pay") or pay, pay_min=lo, pay_max=hi)
    return job
