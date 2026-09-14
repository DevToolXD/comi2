"""Open redirect detection."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .base import Module, Severity, Confidence
from .injection import enumerate_points


_REDIRECT_HINT = re.compile(
    r"(redirect|return|returnurl|return_to|next|url|dest|destination|continue|"
    r"goto|target|redir|forward|out|link|to|r|u)$",
    re.I,
)

_EVIL = "https://scanner-evil.example.org/probe"
_EVIL_HOST = "scanner-evil.example.org"


class OpenRedirectModule(Module):
    name = "open_redirect"
    description = "Open / unvalidated redirects"

    def run(self) -> None:
        points = enumerate_points(self.ctx)
        aggressive = self.config.get("aggressive", False)
        targets = [p for p in points if aggressive or _REDIRECT_HINT.search(p.param)]
        if not targets:
            self.log.status("open_redirect: no redirect-like parameters discovered")
            return
        self.log.status(f"open_redirect: testing {len(targets)} parameter(s)")
        self.parallel(self._test_point, targets, workers=10)

    def _test_point(self, point):
        payloads = [_EVIL, "//" + _EVIL_HOST, "/\\" + _EVIL_HOST, "https:/" + _EVIL_HOST]
        for payload in payloads:
            resp = point.send(payload, mode="replace", allow_redirects=False)
            if resp is None:
                continue
            loc = resp.headers.get("Location", "")
            if not loc:
                # meta refresh / JS redirect in body
                if _EVIL_HOST in resp.text and re.search(
                    r"(http-equiv=['\"]?refresh|location\.(href|replace)|window\.location)",
                    resp.text, re.I
                ):
                    self._flag(point, payload, "client-side redirect to attacker host", Confidence.FIRM)
                    return None
                continue
            host = urlparse(loc).netloc.lower()
            if _EVIL_HOST in host or loc.startswith("//" + _EVIL_HOST):
                self._flag(point, payload, f"Location: {loc}", Confidence.CONFIRMED)
                return None
        return None

    def _flag(self, point, payload, evidence, conf):
        self.report(
            title="Open redirect",
            severity=Severity.MEDIUM,
            category="Open Redirect",
            url=point.url,
            parameter=point.param,
            evidence=f"payload {payload!r} -> {evidence}",
            description="The application redirects to an attacker-controlled external URL "
            "based on user input, enabling phishing and OAuth token theft.",
            remediation="Redirect only to a server-side allowlist of paths/hosts; reject "
            "absolute external URLs and protocol-relative ('//') values.",
            confidence=conf,
            references=["https://owasp.org/www-community/attacks/Unvalidated_Redirects_and_Forwards_Cheat_Sheet"],
        )
