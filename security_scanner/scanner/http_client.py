"""
Thin HTTP client wrapper around `requests` with the knobs a scanner needs:
timeouts, retries, a rate limiter (be gentle even to your own box), optional
proxy, custom headers/cookies, and a `raw` low-level request that does *not*
follow redirects (needed for open-redirect and header checks).

If `requests` is not installed we fall back to a small urllib-based shim so the
tool still runs, just with fewer features.
"""

from __future__ import annotations

import time
import threading
from typing import Dict, Optional, Tuple

try:
    import requests
    from requests.adapters import HTTPAdapter

    try:  # urllib3 lives under requests in practice
        from urllib3.util.retry import Retry
        from urllib3.exceptions import InsecureRequestWarning
        import urllib3

        urllib3.disable_warnings(InsecureRequestWarning)
    except Exception:  # pragma: no cover
        Retry = None
    _HAVE_REQUESTS = True
except Exception:  # pragma: no cover
    _HAVE_REQUESTS = False


DEFAULT_UA = (
    "Mozilla/5.0 (compatible; SelfSecurityScanner/1.0; "
    "+authorized-self-assessment)"
)


class Response:
    """Uniform response object regardless of backend."""

    __slots__ = ("status_code", "headers", "text", "url", "elapsed", "history_len", "reason")

    def __init__(self, status_code, headers, text, url, elapsed, history_len=0, reason=""):
        self.status_code = status_code
        self.headers = headers  # case-insensitive-ish dict
        self.text = text
        self.url = url
        self.elapsed = elapsed  # seconds (float)
        self.history_len = history_len
        self.reason = reason


class _RateLimiter:
    def __init__(self, per_second: float):
        self.min_interval = (1.0 / per_second) if per_second and per_second > 0 else 0.0
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


class HttpClient:
    def __init__(
        self,
        timeout: float = 12.0,
        verify_tls: bool = False,
        proxy: Optional[str] = None,
        rate_per_second: float = 20.0,
        user_agent: str = DEFAULT_UA,
        extra_headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        max_retries: int = 2,
    ):
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.limiter = _RateLimiter(rate_per_second)
        self.user_agent = user_agent
        self.extra_headers = extra_headers or {}
        self.request_count = 0
        self._count_lock = threading.Lock()

        if _HAVE_REQUESTS:
            self.session = requests.Session()
            self.session.headers.update({"User-Agent": user_agent})
            self.session.headers.update(self.extra_headers)
            if cookies:
                self.session.cookies.update(cookies)
            if proxy:
                self.session.proxies.update({"http": proxy, "https": proxy})
            if Retry is not None:
                retry = Retry(
                    total=max_retries,
                    backoff_factor=0.4,
                    status_forcelist=(502, 503, 504),
                    allowed_methods=None,
                )
                adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
                self.session.mount("http://", adapter)
                self.session.mount("https://", adapter)
        else:  # pragma: no cover
            self.session = None
            self._cookies = cookies or {}
            self._proxy = proxy

    def _tick(self) -> None:
        with self._count_lock:
            self.request_count += 1

    def request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
        allow_redirects: bool = True,
        timeout: Optional[float] = None,
    ) -> Optional[Response]:
        """Perform a request. Returns None on network failure (logged by caller)."""
        self.limiter.wait()
        self._tick()
        to = timeout if timeout is not None else self.timeout
        if _HAVE_REQUESTS:
            try:
                r = self.session.request(
                    method.upper(),
                    url,
                    params=params,
                    data=data,
                    headers=headers,
                    timeout=to,
                    allow_redirects=allow_redirects,
                    verify=self.verify_tls,
                )
                return Response(
                    status_code=r.status_code,
                    headers=dict(r.headers),
                    text=r.text or "",
                    url=r.url,
                    elapsed=r.elapsed.total_seconds(),
                    history_len=len(r.history),
                    reason=getattr(r, "reason", ""),
                )
            except requests.exceptions.RequestException:
                return None
            except Exception:
                return None
        else:  # pragma: no cover
            return self._urllib_request(method, url, params, data, headers, allow_redirects, to)

    # convenience wrappers
    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def head(self, url, **kw):
        kw.setdefault("allow_redirects", False)
        return self.request("HEAD", url, **kw)

    # ---- urllib fallback -------------------------------------------------
    def _urllib_request(self, method, url, params, data, headers, allow_redirects, to):  # pragma: no cover
        import urllib.request
        import urllib.parse
        import urllib.error
        import ssl

        if params:
            sep = "&" if ("?" in url) else "?"
            url = url + sep + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode() if data else None
        hdrs = {"User-Agent": self.user_agent}
        hdrs.update(self.extra_headers)
        if headers:
            hdrs.update(headers)

        ctx = ssl.create_default_context()
        if not self.verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        handlers = [urllib.request.HTTPSHandler(context=ctx)]
        if not allow_redirects:
            handlers.append(_NoRedirect())
        opener = urllib.request.build_opener(*handlers)

        req = urllib.request.Request(url, data=body, headers=hdrs, method=method.upper())
        start = time.monotonic()
        try:
            resp = opener.open(req, timeout=to)
            text = resp.read().decode("utf-8", errors="replace")
            return Response(
                status_code=resp.status,
                headers=dict(resp.headers),
                text=text,
                url=resp.geturl(),
                elapsed=time.monotonic() - start,
            )
        except urllib.error.HTTPError as e:
            text = ""
            try:
                text = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return Response(
                status_code=e.code,
                headers=dict(getattr(e, "headers", {}) or {}),
                text=text,
                url=url,
                elapsed=time.monotonic() - start,
            )
        except Exception:
            return None


def have_requests() -> bool:
    return _HAVE_REQUESTS
