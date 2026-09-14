"""HTTP security headers and cookie flag analysis."""

from __future__ import annotations

import re

from .base import Module, Severity, Confidence


class SecurityHeadersModule(Module):
    name = "headers"
    description = "Missing/weak security headers and insecure cookies"

    def run(self) -> None:
        resp = self.ctx.baseline()
        if resp is None:
            self.log.status("headers: base URL unreachable, skipping")
            return
        h = {k.lower(): v for k, v in resp.headers.items()}
        is_https = self.ctx.scheme == "https"

        self._check_missing(h, is_https, resp)
        self._check_cookies(resp)

    def _check_missing(self, h, is_https, resp) -> None:
        checks = [
            (
                "content-security-policy",
                Severity.MEDIUM,
                "Content-Security-Policy is absent. CSP is the strongest defense-in-depth "
                "against XSS and data injection.",
                "Define a restrictive CSP (default-src 'self'; avoid 'unsafe-inline').",
            ),
            (
                "x-frame-options",
                Severity.MEDIUM,
                "X-Frame-Options / frame-ancestors missing: the page can be framed, "
                "enabling clickjacking.",
                "Set X-Frame-Options: DENY (or SAMEORIGIN) or a CSP frame-ancestors directive.",
            ),
            (
                "x-content-type-options",
                Severity.LOW,
                "X-Content-Type-Options missing: browsers may MIME-sniff responses.",
                "Set X-Content-Type-Options: nosniff.",
            ),
            (
                "referrer-policy",
                Severity.LOW,
                "Referrer-Policy missing: full URLs may leak to third parties via Referer.",
                "Set Referrer-Policy: no-referrer or strict-origin-when-cross-origin.",
            ),
            (
                "permissions-policy",
                Severity.INFO,
                "Permissions-Policy missing: powerful browser features are not restricted.",
                "Set Permissions-Policy to disable unused features (camera, geolocation...).",
            ),
        ]
        for header, sev, desc, fix in checks:
            if header == "x-frame-options":
                csp = h.get("content-security-policy", "")
                if "frame-ancestors" in csp.lower():
                    continue
            if header not in h:
                self.report(
                    title=f"Missing security header: {header}",
                    severity=sev,
                    category="Security Headers",
                    description=desc,
                    remediation=fix,
                    confidence=Confidence.CONFIRMED,
                    evidence="header not present in response",
                )

        # HSTS only meaningful over HTTPS
        if is_https:
            hsts = h.get("strict-transport-security", "")
            if not hsts:
                self.report(
                    title="Missing HSTS header",
                    severity=Severity.MEDIUM,
                    category="Security Headers",
                    description="Strict-Transport-Security is absent; users are exposed to "
                    "SSL-strip / downgrade attacks on first or subsequent visits.",
                    remediation="Add Strict-Transport-Security: max-age=31536000; includeSubDomains",
                    confidence=Confidence.CONFIRMED,
                )
            else:
                m = re.search(r"max-age=(\d+)", hsts)
                if m and int(m.group(1)) < 15552000:
                    self.report(
                        title="Weak HSTS max-age",
                        severity=Severity.LOW,
                        category="Security Headers",
                        evidence=hsts,
                        description="HSTS max-age is short (< ~180 days), weakening protection.",
                        remediation="Use max-age of at least 31536000 (1 year).",
                        confidence=Confidence.CONFIRMED,
                    )

        # legacy / discouraged
        if h.get("x-xss-protection", "").startswith("1"):
            self.report(
                title="Legacy X-XSS-Protection enabled",
                severity=Severity.INFO,
                category="Security Headers",
                evidence=h.get("x-xss-protection", ""),
                description="The legacy XSS auditor can itself introduce vulnerabilities and "
                "is ignored by modern browsers.",
                remediation="Set X-XSS-Protection: 0 and rely on CSP instead.",
                confidence=Confidence.FIRM,
            )

    def _check_cookies(self, resp) -> None:
        # Requests folds duplicate Set-Cookie headers; parse what we can.
        raw = resp.headers.get("Set-Cookie", "")
        if not raw:
            return
        # split on cookie boundaries heuristically (", name=" pattern)
        chunks = re.split(r",(?=[^;]+?=)", raw)
        is_https = self.ctx.scheme == "https"
        for chunk in chunks:
            chunk = chunk.strip()
            if "=" not in chunk:
                continue
            name = chunk.split("=", 1)[0].strip()
            low = chunk.lower()
            problems = []
            if "httponly" not in low:
                problems.append("HttpOnly missing (readable by JavaScript → XSS cookie theft)")
            if is_https and "secure" not in low:
                problems.append("Secure missing (may be sent over plain HTTP)")
            if "samesite" not in low:
                problems.append("SameSite missing (CSRF exposure)")
            if problems:
                sev = Severity.MEDIUM if len(problems) >= 2 else Severity.LOW
                # session-looking cookies are more sensitive
                if re.search(r"sess|token|auth|jwt|sid", name, re.I):
                    sev = Severity.MEDIUM
                self.report(
                    title=f"Insecure cookie flags: {name}",
                    severity=sev,
                    category="Session Management",
                    evidence="; ".join(problems),
                    description=f"Cookie '{name}' is set without one or more protective flags.",
                    remediation="Set HttpOnly, Secure (on HTTPS) and SameSite=Lax/Strict on cookies.",
                    confidence=Confidence.CONFIRMED,
                )
