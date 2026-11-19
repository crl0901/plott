"""HTTP and text helpers shared by the scrapers."""
import html
import re
import time
import warnings
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


def get(url, params=None, tries=3, timeout=30):
    """GET with retry on 429/5xx/network errors. Returns a Response (any status) or None."""
    resp = None
    for i in range(tries):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
        except requests.RequestException:
            resp = None
        time.sleep(3 * (i + 1))
    return resp


def html_to_text(markup, base_url=None, keep_links=False):
    soup = BeautifulSoup(markup or "", "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "iframe", "head"]):
        t.decompose()
    if keep_links:
        for a in soup.find_all("a", href=True):
            href = urljoin(base_url or "", a["href"])
            a.replace_with(f"{a.get_text(' ', strip=True)} <{href}>")
    for br in soup.find_all(["br", "p", "li", "div", "h1", "h2", "h3", "h4", "tr"]):
        br.append("\n")
    text = soup.get_text(" ")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def unescape_html_text(s):
    """Greenhouse returns HTML that is itself HTML-escaped."""
    return html_to_text(html.unescape(s or ""))


def money(s):
    """Annual dollar figures in a string (ignores hourly rates)."""
    vals = []
    for m in re.finditer(r"\$\s?(\d{2,3}(?:,\d{3})+(?:\.\d+)?|\d{2,3}(?:\.\d+)?\s?[kK])", s or ""):
        v = m.group(1).replace(",", "").replace(" ", "")
        v = float(v[:-1]) * 1000 if v[-1] in "kK" else float(v)
        if v >= 30000:
            vals.append(int(v))
    return vals


def pay_from_text(text):
    """Find the first salary range in free text -> (display, min, max)."""
    for m in re.finditer(r"\$\s?[\d,\.]+\s?[kK]?\s*(?:-|–|—|to)\s*(?:USD\s*)?\$?\s?[\d,\.]+\s?[kK]?", text or ""):
        v = money(m.group(0).replace("to", "- $"))
        if len(v) >= 2:
            return m.group(0).strip(), min(v), max(v)
    return "", None, None
