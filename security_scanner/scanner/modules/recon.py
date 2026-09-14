"""Reconnaissance: server identity, technology fingerprint, info disclosure."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from .base import Module, Severity, Confidence


# header -> what it leaks
_VERSION_HEADERS = {
    "server": "Web server software/version",
    "x-powered-by": "Backend technology/version",
    "x-aspnet-version": "ASP.NET version",
    "x-aspnetmvc-version": "ASP.NET MVC version",
    "x-generator": "CMS/generator",
    "x-drupal-cache": "Drupal",
    "x-runtime": "Rails runtime",
    "x-version": "Application version",
}

# quick technology fingerprints (header/cookie/body signals)
_TECH_SIGNATURES = [
    ("WordPress", re.compile(r"/wp-content/|/wp-includes/|wp-json", re.I)),
    ("Drupal", re.compile(r"Drupal|/sites/default/|drupal-settings-json", re.I)),
    ("Joomla", re.compile(r"/media/jui/|com_content|Joomla", re.I)),
    ("Laravel", re.compile(r"laravel_session|XSRF-TOKEN", re.I)),
    ("Django", re.compile(r"csrftoken|__admin__|djdt", re.I)),
    ("Express", re.compile(r"connect.sid", re.I)),
    ("PHP", re.compile(r"PHPSESSID", re.I)),
    ("ASP.NET", re.compile(r"ASP\.NET_SessionId|__VIEWSTATE|\.aspx", re.I)),
    ("React", re.compile(r"data-reactroot|__NEXT_DATA__|react", re.I)),
    ("jQuery", re.compile(r"jquery(\.min)?\.js", re.I)),
    ("Nginx", re.compile(r"nginx", re.I)),
    ("Apache", re.compile(r"apache", re.I)),
]


class ReconModule(Module):
    name = "recon"
    description = "Server identity, technology fingerprinting, information disclosure"

    def run(self) -> None:
        resp = self.ctx.baseline()
        if resp is None:
            self.log.status("recon: base URL unreachable, skipping fingerprint")
        else:
            self._server_headers(resp)
            self._fingerprint(resp)

        self._robots_and_meta()

    def _server_headers(self, resp) -> None:
        lower = {k.lower(): v for k, v in resp.headers.items()}
        for h, meaning in _VERSION_HEADERS.items():
            if h in lower:
                val = lower[h]
                has_version = bool(re.search(r"\d+\.\d+", val))
                self.report(
                    title=f"Server discloses '{h}' header",
                    severity=Severity.LOW if has_version else Severity.INFO,
                    category="Information Disclosure",
                    description=f"The response advertises {meaning}: '{val}'. "
                    "Version banners help attackers match known CVEs.",
                    evidence=f"{h}: {val}",
                    remediation="Suppress or genericize version banners "
                    "(e.g. Nginx server_tokens off, Apache ServerTokens Prod, "
                    "remove X-Powered-By).",
                    confidence=Confidence.CONFIRMED,
                    references=["https://owasp.org/www-project-web-security-testing-guide/"],
                )

    def _fingerprint(self, resp) -> None:
        haystack = resp.text + "\n" + "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
        # include Set-Cookie names
        cookie = resp.headers.get("Set-Cookie", "")
        haystack += "\n" + cookie
        detected = []
        for name, rx in _TECH_SIGNATURES:
            if rx.search(haystack):
                detected.append(name)
        if detected:
            self.report(
                title="Technology stack fingerprinted",
                severity=Severity.INFO,
                category="Reconnaissance",
                description="Passive fingerprinting identified these technologies. "
                "Verify each is patched to a current version.",
                evidence=", ".join(sorted(set(detected))),
                confidence=Confidence.FIRM,
                remediation="Keep all identified components updated; remove unused ones.",
            )

    def _robots_and_meta(self) -> None:
        for path in ("robots.txt", "sitemap.xml", ".well-known/security.txt"):
            url = urljoin(self.ctx.base_url + "/", path)
            r = self.http.get(url)
            if r is None or r.status_code != 200 or not r.text.strip():
                continue
            body = r.text
            if path == "robots.txt":
                disallowed = re.findall(r"(?im)^\s*Disallow:\s*(\S+)", body)
                interesting = [
                    d for d in disallowed
                    if re.search(r"admin|backup|private|config|db|sql|secret|test|api|internal", d, re.I)
                ]
                sev = Severity.LOW if interesting else Severity.INFO
                self.report(
                    title="robots.txt reachable",
                    severity=sev,
                    category="Information Disclosure",
                    url=url,
                    description="robots.txt often lists paths the owner wants hidden — "
                    "which is exactly where an attacker looks first.",
                    evidence=("Interesting Disallow entries: " + ", ".join(interesting[:10]))
                    if interesting else f"{len(disallowed)} Disallow entries",
                    confidence=Confidence.CONFIRMED,
                    remediation="Do not rely on robots.txt for security; protect sensitive "
                    "paths with authentication/authorization.",
                )
            else:
                self.report(
                    title=f"{path} reachable",
                    severity=Severity.INFO,
                    category="Reconnaissance",
                    url=url,
                    evidence=f"HTTP 200, {len(body)} bytes",
                    confidence=Confidence.CONFIRMED,
                )
