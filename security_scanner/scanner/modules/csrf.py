"""
CSRF hygiene check: state-changing (POST) forms that carry no anti-CSRF token
and no SameSite-protected session cookie are flagged.
"""

from __future__ import annotations

import re

from .base import Module, Severity, Confidence


_TOKEN_HINT = re.compile(
    r"csrf|xsrf|authenticity_token|__requestverificationtoken|nonce|_token|"
    r"anti[-_]?forgery|verificationtoken",
    re.I,
)


class CsrfModule(Module):
    name = "csrf"
    description = "Forms lacking anti-CSRF tokens"

    def run(self) -> None:
        forms = [f for f in self.ctx.forms if f.method == "POST"]
        if not forms:
            self.log.status("csrf: no POST forms discovered")
            return

        # Is the session cookie SameSite-protected? If so, CSRF risk is reduced.
        samesite = self._session_samesite()

        self.log.status(f"csrf: checking {len(forms)} POST form(s)")
        for form in forms:
            has_token = any(_TOKEN_HINT.search(name) for name in form.inputs)
            # Also treat a login form specially: login CSRF is lower impact.
            looks_login = any(
                re.search(r"pass|pwd", n, re.I) for n in form.inputs
            ) and any(re.search(r"user|email|login|id", n, re.I) for n in form.inputs)

            if has_token:
                continue

            if samesite in ("strict", "lax"):
                sev = Severity.LOW
                note = f" (session cookie SameSite={samesite} mitigates some CSRF)"
            else:
                sev = Severity.MEDIUM
                note = ""

            if looks_login:
                sev = Severity.LOW

            self.report(
                title="POST form without anti-CSRF token",
                severity=sev,
                category="CSRF",
                url=form.action,
                evidence=f"fields={list(form.inputs)}{note}",
                description="A state-changing form has no detectable anti-CSRF token. An "
                "attacker page could submit it on behalf of a logged-in victim.",
                remediation="Add per-session/per-request CSRF tokens (synchronizer or "
                "double-submit) and set session cookies SameSite=Lax/Strict.",
                confidence=Confidence.TENTATIVE,
                references=["https://owasp.org/www-community/attacks/csrf"],
            )

    def _session_samesite(self):
        resp = self.ctx.baseline()
        if resp is None:
            return None
        raw = resp.headers.get("Set-Cookie", "")
        if not raw:
            return None
        m = re.search(r"samesite=(\w+)", raw, re.I)
        return m.group(1).lower() if m else None
