"""Dangerous / unexpected HTTP methods."""

from __future__ import annotations

from .base import Module, Severity, Confidence


class HttpMethodsModule(Module):
    name = "http_methods"
    description = "Dangerous HTTP methods (PUT/DELETE/TRACE/CONNECT)"

    def run(self) -> None:
        base = self.ctx.base_url

        # OPTIONS advertises the allowed set
        r = self.http.request("OPTIONS", base, allow_redirects=False)
        allow = ""
        if r is not None:
            allow = (_get(r.headers, "allow") or "") + " " + (_get(r.headers, "access-control-allow-methods") or "")
            if allow.strip():
                self.report(
                    title="HTTP methods advertised via OPTIONS",
                    severity=Severity.INFO,
                    category="HTTP Methods",
                    evidence=f"Allow: {allow.strip()}",
                    confidence=Confidence.CONFIRMED,
                    description="The server lists the HTTP methods it accepts.",
                )

        # Actively probe risky methods (non-destructive: we don't send a body/target).
        for method, sev, desc, fix in [
            ("TRACE", Severity.MEDIUM,
             "TRACE is enabled, enabling Cross-Site Tracing (XST) to steal headers/cookies.",
             "Disable the TRACE method at the web server."),
            ("PUT", Severity.HIGH,
             "PUT appears accepted — may allow uploading/overwriting files on the server.",
             "Disable PUT unless required; enforce authentication and path restrictions."),
            ("DELETE", Severity.HIGH,
             "DELETE appears accepted — may allow removing server resources.",
             "Disable DELETE unless required; enforce authorization."),
            ("CONNECT", Severity.MEDIUM,
             "CONNECT appears accepted — server may be usable as a proxy.",
             "Disable CONNECT on the web server."),
        ]:
            resp = self.http.request(method, base, allow_redirects=False, timeout=8)
            if resp is None:
                continue
            code = resp.status_code
            # 405/501/403/400 == method rejected (good). 2xx == accepted (bad).
            if code < 400:
                self.report(
                    title=f"Dangerous HTTP method enabled: {method}",
                    severity=sev,
                    category="HTTP Methods",
                    evidence=f"{method} {base} -> HTTP {code}",
                    description=desc,
                    remediation=fix,
                    confidence=Confidence.FIRM,
                )
            elif method == "TRACE" and code == 200:
                pass


def _get(headers, key):
    for k, v in headers.items():
        if k.lower() == key:
            return v
    return None
