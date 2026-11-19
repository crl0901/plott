"""HTTP and text helpers shared by the scrapers."""
import functools
import html
import ipaddress
import re
import socket
import threading
import time
import warnings
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


MAX_BYTES = 50 * 1024 * 1024   # largest response read (big company boards with descriptions are ~20 MB)
MAX_SECONDS = 120              # longest one request may take, including a slow body
MAX_REDIRECTS = 5


class BlockedURL(Exception):
    """Not http(s), or the host is not a public internet address."""


@functools.lru_cache(maxsize=4096)
def _public_host(host):
    """True when every address the host resolves to is on the public internet. Raises on a DNS failure
    (failures are not cached)."""
    infos = socket.getaddrinfo(host, None)
    return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)


def url_allowed(url):
    """Job links come from scraped data, so a link must not reach this computer or the local network."""
    try:
        p = urlsplit(url)
        return p.scheme in ("http", "https") and bool(p.hostname) and _public_host(p.hostname)
    except (ValueError, OSError):
        return False


def _fetch(url, params, timeout):
    """One GET, following redirects by hand so each hop is checked, with size and time limits."""
    for _ in range(MAX_REDIRECTS + 1):
        if not url_allowed(url):
            raise BlockedURL(url)
        resp = session.get(url, params=params, timeout=timeout, allow_redirects=False, stream=True)
        if resp.is_redirect:
            url, params = urljoin(resp.url, resp.headers["location"]), None
            resp.close()
            continue
        timer = threading.Timer(MAX_SECONDS, resp.close)   # stops a server that sends very slowly
        timer.start()
        try:
            chunks, size = [], 0
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise requests.RequestException("response too large")
                chunks.append(chunk)
        except requests.RequestException:
            resp.close()
            raise
        except Exception as e:   # the timer closed the connection mid-read
            resp.close()
            raise requests.RequestException(f"read failed: {type(e).__name__}") from None
        finally:
            timer.cancel()
        resp._content = b"".join(chunks)
        return resp
    raise requests.RequestException("too many redirects")


def get(url, params=None, tries=3, timeout=30):
    """GET with retry on 429/5xx/network errors. Returns a Response (any status) or None. Only
    public http(s) addresses are fetched."""
    resp = None
    for i in range(tries):
        try:
            resp = _fetch(url, params, timeout)
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
        except BlockedURL:
            return None
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
