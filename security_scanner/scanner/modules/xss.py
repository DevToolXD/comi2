"""
Reflected XSS detection. Injects a uniquely-marked payload and checks whether
it is reflected into the response *without* the dangerous characters being
encoded, in a context where it could execute.
"""

from __future__ import annotations

import html
import re
import secrets

from .base import Module, Severity, Confidence
from .injection import enumerate_points


class XssModule(Module):
    name = "xss"
    description = "Reflected cross-site scripting"

    def run(self) -> None:
        points = enumerate_points(self.ctx)
        if not points:
            self.log.status("xss: no parameters/forms discovered to test")
            return
        self.log.status(f"xss: testing {len(points)} injection point(s)")
        self.parallel(self._test_point, points, workers=10)

    def _test_point(self, point):
        marker = "xz" + secrets.token_hex(4)
        # First: is the parameter reflected at all?
        probe = point.send(marker, mode="replace")
        if probe is None or marker not in probe.text:
            return None

        # Payloads exercising different injection contexts.
        payloads = [
            f"<{marker}>",                                   # raw tag injection
            f"\"'><svg/onload=alert({marker})>",             # attribute break-out
            f"</script><script>{marker}=1</script>",         # script context
            f"javascript:{marker}",                          # href/js context
            f"'\"><img src=x onerror={marker}=1>",           # img onerror
        ]
        for p in payloads:
            resp = point.send(p, mode="replace")
            if resp is None:
                continue
            body = resp.text
            if p in body:
                # exact, unencoded reflection of a payload containing < > " '
                ctype = str(resp.headers.get("Content-Type", "")).lower()
                if "html" not in ctype and "xml" not in ctype:
                    # reflected but not rendered as HTML → lower severity
                    self.report(
                        title="User input reflected (non-HTML content-type)",
                        severity=Severity.LOW,
                        category="Cross-Site Scripting",
                        url=point.url,
                        parameter=point.param,
                        evidence=f"payload {p!r} reflected; Content-Type={ctype or 'unknown'}",
                        description="Input is reflected verbatim. Not HTML today, but a content-type "
                        "change or downstream sink could make it exploitable.",
                        remediation="Context-aware output encoding + a strict Content-Type.",
                        confidence=Confidence.TENTATIVE,
                    )
                    return None
                self.report(
                    title="Reflected XSS",
                    severity=Severity.HIGH,
                    category="Cross-Site Scripting",
                    url=point.url,
                    parameter=point.param,
                    evidence=f"payload {p!r} reflected unencoded into HTML response",
                    description="A payload containing HTML metacharacters is reflected into the "
                    "HTML response without encoding, allowing script execution in the victim's "
                    "browser.",
                    remediation="Apply context-aware output encoding (HTML/attribute/JS/URL). "
                    "Deploy a strict Content-Security-Policy as defense in depth.",
                    confidence=Confidence.CONFIRMED,
                    references=["https://owasp.org/www-community/attacks/xss/"],
                )
                return None

            # Partial: dangerous chars survive even if full payload mangled?
            if marker in body:
                idx = body.find(marker)
                window = body[max(0, idx - 30): idx + 30]
                if ("<" in window and ">" in window) and html.escape(window) != window:
                    self.report(
                        title="Possible reflected XSS (partial reflection)",
                        severity=Severity.MEDIUM,
                        category="Cross-Site Scripting",
                        url=point.url,
                        parameter=point.param,
                        evidence=f"marker reflected near unencoded angle brackets: {window!r}",
                        description="Input is reflected and nearby markup is unencoded; manual "
                        "verification recommended.",
                        remediation="Apply context-aware output encoding; deploy CSP.",
                        confidence=Confidence.TENTATIVE,
                    )
                    return None
        return None
