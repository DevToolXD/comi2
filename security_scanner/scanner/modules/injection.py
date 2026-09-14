"""
Shared machinery for the injection-style modules (SQLi, XSS, traversal, command
injection, open redirect). It turns the crawler's output — parameterized URLs
and HTML forms — into a uniform list of "injection points" that each accept a
payload and return a Response.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse


class InjectionPoint:
    """One place a value can be injected: a single parameter of a GET/POST request."""

    def __init__(self, http, method: str, url: str, param: str,
                 base_params: Dict[str, str], base_data: Dict[str, str]):
        self.http = http
        self.method = method.upper()
        self.url = url                 # url WITHOUT the query for GET points is fine too
        self.param = param
        self.base_params = dict(base_params)
        self.base_data = dict(base_data)

    def _mutate(self, value: str, mode: str = "replace"):
        params = dict(self.base_params)
        data = dict(self.base_data)
        if self.method == "GET":
            orig = params.get(self.param, "")
            params[self.param] = value if mode == "replace" else (orig + value)
        else:
            orig = data.get(self.param, "")
            data[self.param] = value if mode == "replace" else (orig + value)
        return params, data

    def send(self, value: str, mode: str = "replace", timeout: Optional[float] = None,
             allow_redirects: bool = True):
        params, data = self._mutate(value, mode)
        if self.method == "GET":
            return self.http.get(self.url, params=params, timeout=timeout,
                                 allow_redirects=allow_redirects)
        return self.http.post(self.url, data=data, params=params or None,
                              timeout=timeout, allow_redirects=allow_redirects)

    def baseline_value(self) -> str:
        if self.method == "GET":
            return self.base_params.get(self.param, "")
        return self.base_data.get(self.param, "")

    def label(self) -> str:
        return f"{self.method} {self.url} [{self.param}]"


def enumerate_points(ctx, include_forms: bool = True) -> List[InjectionPoint]:
    """Build injection points from crawled parameterized URLs and forms."""
    points: List[InjectionPoint] = []
    http = ctx.http

    # 1) GET query parameters
    for url in ctx.param_urls:
        parsed = urlparse(url)
        base_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", ""))
        for name in base_params:
            points.append(InjectionPoint(http, "GET", clean_url, name, base_params, {}))

    # 2) form fields
    if include_forms:
        for form in ctx.forms:
            parsed = urlparse(form.action)
            base_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
            clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", ""))
            inputs = dict(form.inputs)
            if not inputs:
                continue
            for name in inputs:
                if form.method == "GET":
                    merged = dict(base_params)
                    merged.update(inputs)
                    points.append(InjectionPoint(http, "GET", clean_url, name, merged, {}))
                else:
                    points.append(InjectionPoint(http, "POST", clean_url, name, base_params, inputs))

    # de-duplicate identical points
    seen = set()
    unique = []
    for p in points:
        key = (p.method, p.url, p.param, tuple(sorted(p.base_params)), tuple(sorted(p.base_data)))
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def similarity(a: str, b: str) -> float:
    """Cheap response-similarity ratio in [0,1] based on length + a token sample."""
    if a is None or b is None:
        return 0.0
    la, lb = len(a), len(b)
    if la == 0 and lb == 0:
        return 1.0
    length_ratio = min(la, lb) / max(la, lb) if max(la, lb) else 1.0
    return length_ratio
