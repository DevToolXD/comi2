"""CORS misconfiguration checks."""

from __future__ import annotations

from .base import Module, Severity, Confidence


class CorsModule(Module):
    name = "cors"
    description = "Cross-Origin Resource Sharing misconfiguration"

    def run(self) -> None:
        base = self.ctx.base_url
        evil = "https://evil.example.com"
        r = self.http.get(base, headers={"Origin": evil}, allow_redirects=False)
        if r is None:
            self.log.status("cors: base URL unreachable")
            return
        acao = _get(r.headers, "access-control-allow-origin")
        acac = _get(r.headers, "access-control-allow-credentials")

        if acao is None:
            return  # no CORS in play

        creds = (acac or "").strip().lower() == "true"

        if acao == "*" and creds:
            # spec-invalid but some stacks reflect it; very dangerous if honored
            self.report(
                title="CORS: wildcard origin with credentials",
                severity=Severity.HIGH,
                category="CORS Misconfiguration",
                evidence=f"Access-Control-Allow-Origin: * ; Access-Control-Allow-Credentials: true",
                description="Wildcard ACAO combined with credentials would let any site read "
                "authenticated responses.",
                remediation="Never combine ACAO:* with credentials. Reflect only vetted origins.",
                confidence=Confidence.FIRM,
            )
        elif acao == evil:
            sev = Severity.HIGH if creds else Severity.MEDIUM
            self.report(
                title="CORS: arbitrary origin reflected",
                severity=sev,
                category="CORS Misconfiguration",
                evidence=f"Origin {evil} reflected in Access-Control-Allow-Origin"
                + (" with credentials=true" if creds else ""),
                description="The server reflects any supplied Origin. With credentials this lets "
                "a malicious site read authenticated responses; without, it still trusts any origin.",
                remediation="Validate Origin against a strict server-side allowlist; do not reflect "
                "arbitrary origins; only send ACAC:true for trusted origins.",
                confidence=Confidence.CONFIRMED,
                references=["https://portswigger.net/web-security/cors"],
            )
        elif acao == "*":
            self.report(
                title="CORS: wildcard Access-Control-Allow-Origin",
                severity=Severity.LOW,
                category="CORS Misconfiguration",
                evidence="Access-Control-Allow-Origin: *",
                description="Any origin may read responses. Acceptable only for truly public, "
                "non-authenticated data.",
                remediation="Restrict ACAO to specific origins unless the data is fully public.",
                confidence=Confidence.CONFIRMED,
            )
        # null origin trust
        r2 = self.http.get(base, headers={"Origin": "null"}, allow_redirects=False)
        if r2 is not None and _get(r2.headers, "access-control-allow-origin") == "null":
            self.report(
                title="CORS: 'null' origin trusted",
                severity=Severity.MEDIUM,
                category="CORS Misconfiguration",
                evidence="Access-Control-Allow-Origin: null",
                description="Trusting the 'null' origin exposes the API to sandboxed iframe / "
                "data-URL attacks.",
                remediation="Never allow the 'null' origin.",
                confidence=Confidence.CONFIRMED,
            )


def _get(headers, key):
    for k, v in headers.items():
        if k.lower() == key:
            return v
    return None
