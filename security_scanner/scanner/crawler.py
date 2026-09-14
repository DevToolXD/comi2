"""
A small, polite crawler that discovers the attack surface: reachable URLs,
their query parameters, and HTML forms (with their inputs). Injection modules
consume this so they know *what* to test.

Only same-host links are followed. Depth and page count are bounded.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Dict, List, Set, Tuple
from urllib.parse import urljoin, urlparse, urldefrag, parse_qsl


class Form:
    __slots__ = ("action", "method", "inputs")

    def __init__(self, action: str, method: str, inputs: Dict[str, str]):
        self.action = action
        self.method = method.upper() or "GET"
        self.inputs = inputs  # name -> default value

    def key(self) -> tuple:
        return (self.method, self.action, tuple(sorted(self.inputs)))

    def __repr__(self):
        return f"<Form {self.method} {self.action} fields={list(self.inputs)}>"


class _LinkFormParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: Set[str] = set()
        self.forms: List[Form] = []
        self.comments: List[str] = []
        self._cur_action = None
        self._cur_method = None
        self._cur_inputs: Dict[str, str] = {}
        self._in_form = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self._add_link(a["href"])
        elif tag in ("script", "link") and a.get("src"):
            self._add_link(a["src"])
        elif tag == "link" and a.get("href"):
            self._add_link(a["href"])
        elif tag == "form":
            self._in_form = True
            self._cur_action = urljoin(self.base_url, a.get("action") or self.base_url)
            self._cur_method = (a.get("method") or "GET").upper()
            self._cur_inputs = {}
        elif tag in ("input", "textarea", "select") and self._in_form:
            name = a.get("name")
            if name:
                self._cur_inputs[name] = a.get("value", "")
        elif tag == "input" and not self._in_form:
            pass

    def handle_endtag(self, tag):
        if tag == "form" and self._in_form:
            self.forms.append(Form(self._cur_action, self._cur_method or "GET", dict(self._cur_inputs)))
            self._in_form = False

    def handle_comment(self, data):
        if data and data.strip():
            self.comments.append(data.strip())

    def _add_link(self, href: str):
        href = (href or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
            return
        absolute = urljoin(self.base_url, href)
        absolute, _ = urldefrag(absolute)
        self.links.add(absolute)


# secrets / interesting patterns to flag if seen inline in HTML/JS/comments
_INTERESTING_COMMENT = re.compile(
    r"(password|passwd|secret|api[_-]?key|todo|fixme|debug|backdoor|username|"
    r"admin|internal|deprecated|hack|temporary|test\s*only)",
    re.IGNORECASE,
)


class Crawler:
    def __init__(self, ctx):
        self.ctx = ctx
        self.http = ctx.http
        self.log = ctx.log
        self.base = ctx.base_url
        host = urlparse(self.base).netloc
        self.host = host
        self.visited: Set[str] = set()
        self.urls_with_params: List[str] = []
        self.forms: List[Form] = []
        self._form_keys: Set[tuple] = set()
        self.param_urls_seen: Set[str] = set()
        self.max_pages = ctx.config.get("max_pages", 60)
        self.max_depth = ctx.config.get("max_depth", 3)

    def _same_host(self, url: str) -> bool:
        try:
            return urlparse(url).netloc == self.host
        except Exception:
            return False

    def crawl(self) -> Tuple[List[str], List[Form]]:
        self.log.status(f"Crawling {self.base} (max {self.max_pages} pages, depth {self.max_depth})")
        queue: List[Tuple[str, int]] = [(self.base, 0)]
        seed = self.ctx.config.get("seed_urls") or []
        for s in seed:
            queue.append((s, 0))

        while queue and len(self.visited) < self.max_pages:
            url, depth = queue.pop(0)
            norm, _ = urldefrag(url)
            if norm in self.visited or depth > self.max_depth:
                continue
            if not self._same_host(norm):
                continue
            self.visited.add(norm)

            resp = self.http.get(norm)
            if resp is None:
                continue

            self._record_params(norm)

            ctype = str(resp.headers.get("Content-Type", "")).lower()
            if "html" not in ctype and not resp.text.lstrip().startswith("<"):
                continue

            parser = _LinkFormParser(norm)
            try:
                parser.feed(resp.text)
            except Exception:
                continue

            for c in parser.comments:
                if _INTERESTING_COMMENT.search(c):
                    self.log.finding(
                        title="Sensitive information in HTML comment",
                        severity=_Sev().LOW,
                        category="Information Disclosure",
                        url=norm,
                        evidence=c[:200],
                        confidence=_Conf().TENTATIVE,
                        module="crawler",
                        description="An HTML comment contains a keyword that often "
                        "indicates leaked internal notes, credentials or debug info.",
                        remediation="Strip developer comments from production HTML.",
                    )

            for form in parser.forms:
                if form.key() not in self._form_keys:
                    self._form_keys.add(form.key())
                    self.forms.append(form)

            for link in parser.links:
                if self._same_host(link) and link not in self.visited:
                    queue.append((link, depth + 1))
                    self._record_params(link)

        self.log.status(
            f"Crawl done: {len(self.visited)} pages, "
            f"{len(self.urls_with_params)} parameterized URLs, {len(self.forms)} forms"
        )
        return self.urls_with_params, self.forms

    def _record_params(self, url: str) -> None:
        parsed = urlparse(url)
        if not parsed.query:
            return
        if url in self.param_urls_seen:
            return
        # dedup by (path, sorted param names) so ?id=1 and ?id=2 count once
        params = [k for k, _ in parse_qsl(parsed.query, keep_blank_values=True)]
        sig = (parsed.path, tuple(sorted(params)))
        if sig in self.param_urls_seen:
            return
        self.param_urls_seen.add(sig)
        self.param_urls_seen.add(url)
        self.urls_with_params.append(url)


# late imports to avoid a hard dependency cycle at module import time
def _Sev():
    from .logger import Severity
    return Severity


def _Conf():
    from .logger import Confidence
    return Confidence
