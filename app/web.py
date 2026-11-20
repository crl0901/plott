"""HTTP and text helpers shared by the scrapers."""
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
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import ConnectTimeoutError, NameResolutionError, NewConnectionError
from urllib3.util.connection import allowed_gai_family

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
MAX_BYTES = 50 * 1024 * 1024   # largest response read (big company boards with descriptions are ~20 MB)
MAX_SECONDS = 120              # longest one request may take, including a slow body
MAX_REDIRECTS = 5


class BlockedURL(Exception):
    """Not http(s), or the host is not a public internet address."""


def _is_public(ip):
    return ipaddress.ip_address(ip.split("%")[0]).is_global


class _PublicOnly:
    """Job links come from scraped data, so a link must not reach this computer or the local network.
    The host is resolved once, every address is checked, and the connection goes to a checked address.
    Checking here instead of before the request avoids a second DNS lookup per host (a scrape reads
    thousands of hosts, and DNS servers such as Pi-hole limit queries per minute), and the address
    can't change between the check and the connection."""

    def _new_conn(self):
        try:
            infos = socket.getaddrinfo(self._dns_host, self.port, allowed_gai_family(), socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise NameResolutionError(self.host, self, e) from e
        ips = list(dict.fromkeys(i[4][0] for i in infos))
        if not ips or not all(_is_public(ip) for ip in ips):
            raise BlockedURL(self.host)
        name, err = self._dns_host, None
        try:
            for ip in ips:   # TLS still checks the certificate against the host name, not the address
                self._dns_host = ip
                try:
                    return super()._new_conn()
                except (NewConnectionError, ConnectTimeoutError) as e:
                    err = e
            raise err
        finally:
            self._dns_host = name


class _PublicHTTPConnection(_PublicOnly, HTTPConnection):
    pass


class _PublicHTTPSConnection(_PublicOnly, HTTPSConnection):
    pass


class _PublicHTTPPool(HTTPConnectionPool):
    ConnectionCls = _PublicHTTPConnection


class _PublicHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _PublicHTTPSConnection


class _PublicAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _PublicHTTPPool, "https": _PublicHTTPSPool}


session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
session.mount("http://", _PublicAdapter())
session.mount("https://", _PublicAdapter())


def _scheme_ok(url):
    try:
        p = urlsplit(url)
        return p.scheme in ("http", "https") and bool(p.hostname)
    except ValueError:
        return False


def _fetch(url, params, timeout):
    """One GET, following redirects by hand so each hop is checked, with size and time limits."""
    for _ in range(MAX_REDIRECTS + 1):
        if not _scheme_ok(url):
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
