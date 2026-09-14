"""
Exposed sensitive files, backups, VCS metadata, admin panels and directory
listings. Uses a random-path baseline to filter out soft-404s so a site that
returns 200 for everything doesn't produce a wall of false positives.
"""

from __future__ import annotations

import re
import secrets
from urllib.parse import urljoin

from .base import Module, Severity, Confidence


# path -> (severity, signature-regex-or-None, description)
_TARGETS = [
    (".git/config", Severity.HIGH, re.compile(r"\[core\]|repositoryformatversion", re.I),
     "Exposed .git directory — full source code history can be reconstructed."),
    (".git/HEAD", Severity.HIGH, re.compile(r"ref:\s*refs/", re.I),
     "Exposed .git directory — source code disclosure."),
    (".svn/entries", Severity.HIGH, None, "Exposed .svn metadata — source disclosure."),
    (".hg/requires", Severity.MEDIUM, None, "Exposed Mercurial metadata."),
    (".env", Severity.CRITICAL, re.compile(r"(APP_KEY|DB_|SECRET|PASSWORD|API_KEY|AWS_)", re.I),
     "Exposed .env file — application secrets/credentials disclosure."),
    (".env.local", Severity.CRITICAL, re.compile(r"(APP_KEY|DB_|SECRET|PASSWORD|API_KEY)", re.I),
     "Exposed .env.local — secrets disclosure."),
    (".env.production", Severity.CRITICAL, re.compile(r"(APP_KEY|DB_|SECRET|PASSWORD|API_KEY)", re.I),
     "Exposed production .env — secrets disclosure."),
    ("config.php.bak", Severity.CRITICAL, re.compile(r"<\?php|password|db", re.I),
     "Backup of config file — source/credentials disclosure."),
    ("wp-config.php.bak", Severity.CRITICAL, re.compile(r"DB_PASSWORD|DB_NAME", re.I),
     "WordPress config backup — DB credentials disclosure."),
    ("wp-config.php~", Severity.CRITICAL, None, "WordPress config backup."),
    ("web.config", Severity.MEDIUM, re.compile(r"<configuration|connectionStrings", re.I),
     "ASP.NET web.config exposed — may leak connection strings."),
    ("phpinfo.php", Severity.HIGH, re.compile(r"phpinfo\(\)|PHP Version", re.I),
     "phpinfo() exposed — leaks full environment/config."),
    ("info.php", Severity.HIGH, re.compile(r"phpinfo\(\)|PHP Version", re.I),
     "phpinfo() exposed — environment disclosure."),
    (".DS_Store", Severity.LOW, re.compile(r"Bud1", re.I),
     "macOS .DS_Store exposed — leaks directory structure."),
    ("backup.zip", Severity.HIGH, None, "Backup archive exposed."),
    ("backup.sql", Severity.CRITICAL, re.compile(r"(INSERT INTO|CREATE TABLE|DROP TABLE)", re.I),
     "Database dump exposed — full data disclosure."),
    ("db.sql", Severity.CRITICAL, re.compile(r"(INSERT INTO|CREATE TABLE)", re.I),
     "Database dump exposed."),
    ("dump.sql", Severity.CRITICAL, re.compile(r"(INSERT INTO|CREATE TABLE)", re.I),
     "Database dump exposed."),
    ("id_rsa", Severity.CRITICAL, re.compile(r"BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY"),
     "Private SSH key exposed."),
    (".htpasswd", Severity.HIGH, re.compile(r":\$(apr1|1|2y|6)\$|:\{SHA\}"),
     ".htpasswd exposed — password hashes disclosure."),
    (".htaccess", Severity.LOW, re.compile(r"RewriteRule|AuthType|Require", re.I),
     ".htaccess exposed — reveals server config."),
    ("composer.json", Severity.LOW, re.compile(r"\"require\"", re.I),
     "composer.json exposed — dependency inventory."),
    ("package.json", Severity.LOW, re.compile(r"\"dependencies\"", re.I),
     "package.json exposed — dependency inventory."),
    ("Dockerfile", Severity.LOW, re.compile(r"^FROM\s", re.I | re.M),
     "Dockerfile exposed — build/config disclosure."),
    ("docker-compose.yml", Severity.MEDIUM, re.compile(r"services:|image:", re.I),
     "docker-compose.yml exposed — infra/secrets disclosure."),
    ("server-status", Severity.MEDIUM, re.compile(r"Apache Server Status", re.I),
     "Apache server-status exposed — request/traffic disclosure."),
    ("actuator/health", Severity.MEDIUM, re.compile(r"\"status\"\s*:", re.I),
     "Spring Boot actuator exposed — may leak env/heapdump."),
    ("actuator/env", Severity.HIGH, re.compile(r"propertySources|systemProperties", re.I),
     "Spring Boot actuator /env exposed — environment/secret disclosure."),
    (".aws/credentials", Severity.CRITICAL, re.compile(r"aws_access_key_id", re.I),
     "AWS credentials exposed."),
    ("swagger.json", Severity.LOW, re.compile(r"\"swagger\"|\"openapi\"", re.I),
     "API schema exposed — enumerates endpoints."),
    ("api-docs", Severity.LOW, re.compile(r"\"swagger\"|\"openapi\"", re.I),
     "API docs exposed — enumerates endpoints."),
]

# admin/login panels — presence is INFO/LOW, just worth knowing they're reachable
_ADMIN_PATHS = [
    "admin", "admin/", "administrator", "wp-admin/", "wp-login.php",
    "login", "user/login", "admin/login", "phpmyadmin/", "pma/",
    "manager/html", "console", ".git/", "cpanel", "adminer.php",
]


class SensitiveFilesModule(Module):
    name = "sensitive_files"
    description = "Exposed files, backups, VCS metadata, admin panels, dir listing"

    def run(self) -> None:
        base = self.ctx.base_url.rstrip("/") + "/"
        self._soft404 = self._baseline_404(base)

        self.log.status(f"sensitive_files: probing {len(_TARGETS)} known-sensitive paths")
        self.parallel(lambda t: self._check_file(base, t), _TARGETS, workers=16)

        self.log.status(f"sensitive_files: probing {len(_ADMIN_PATHS)} admin/login paths")
        self.parallel(lambda p: self._check_admin(base, p), _ADMIN_PATHS, workers=16)

        self._check_dir_listing(base)

    def _baseline_404(self, base):
        rand = "zz_" + secrets.token_hex(8) + "_notfound"
        r = self.http.get(urljoin(base, rand))
        if r is None:
            return {"status": 404, "len": 0}
        return {"status": r.status_code, "len": len(r.text)}

    def _looks_like_404(self, resp) -> bool:
        if resp.status_code in (404, 400, 410):
            return True
        # soft-404: server returns 200 for everything → compare to baseline
        if self._soft404["status"] == 200 and resp.status_code == 200:
            if abs(len(resp.text) - self._soft404["len"]) < 32:
                return True
        return False

    def _check_file(self, base, target):
        path, sev, sig, desc = target
        url = urljoin(base, path)
        r = self.http.get(url)
        if r is None or self._looks_like_404(r):
            return None
        if r.status_code in (401, 403):
            # exists but protected — informational
            self.report(
                title=f"Sensitive path present but protected: {path}",
                severity=Severity.INFO,
                category="Sensitive Exposure",
                url=url,
                evidence=f"HTTP {r.status_code}",
                confidence=Confidence.FIRM,
                description="The path exists (access denied). Ensure it truly cannot be reached.",
            )
            return None
        if r.status_code != 200:
            return None
        # If we have a signature, require it to match to confirm real content.
        if sig is not None and not sig.search(r.text):
            return None
        self.report(
            title=f"Exposed sensitive file: {path}",
            severity=sev,
            category="Sensitive Exposure",
            url=url,
            evidence=f"HTTP 200, {len(r.text)} bytes"
            + (f", matched signature /{sig.pattern}/" if sig else ""),
            description=desc,
            remediation="Remove the file from the web root or block access at the server; "
            "rotate any leaked secrets immediately.",
            confidence=Confidence.CONFIRMED if sig else Confidence.FIRM,
        )
        return None

    def _check_admin(self, base, path):
        url = urljoin(base, path)
        r = self.http.get(url, allow_redirects=False)
        if r is None:
            return None
        if r.status_code in (200, 301, 302, 401, 403):
            if r.status_code == 200 and self._looks_like_404(r):
                return None
            body = r.text.lower()
            is_login = any(k in body for k in ("password", "login", "sign in", "username", "log in"))
            sev = Severity.LOW if (is_login or r.status_code in (401, 403)) else Severity.INFO
            self.report(
                title=f"Admin/login interface reachable: {path}",
                severity=sev,
                category="Sensitive Exposure",
                url=url,
                evidence=f"HTTP {r.status_code}",
                description="An administrative or login interface is reachable. Ensure it is "
                "restricted (IP allowlist / VPN), rate-limited and uses strong auth + MFA.",
                remediation="Restrict admin panels by network, enforce MFA and lockouts, and do "
                "not expose them publicly if avoidable.",
                confidence=Confidence.FIRM,
            )
        return None

    def _check_dir_listing(self, base):
        # Ask for a few common directories and look for autoindex output.
        for d in ("", "images/", "uploads/", "backup/", "files/", "assets/", "static/"):
            url = urljoin(base, d)
            r = self.http.get(url)
            if r is None or r.status_code != 200:
                continue
            if re.search(r"<title>\s*Index of /|Directory listing for|\[To Parent Directory\]",
                         r.text, re.I):
                self.report(
                    title=f"Directory listing enabled: /{d}",
                    severity=Severity.MEDIUM,
                    category="Sensitive Exposure",
                    url=url,
                    evidence="Autoindex / directory listing page returned",
                    description="The server lists directory contents, exposing files that were "
                    "not meant to be discoverable.",
                    remediation="Disable automatic directory indexing (Options -Indexes / "
                    "autoindex off).",
                    confidence=Confidence.CONFIRMED,
                )
