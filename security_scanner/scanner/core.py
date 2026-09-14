"""
Scan orchestrator.

`ScanContext` carries shared state (HTTP client, logger, target info, crawl
results, config) to every module. `Scanner` wires up the client, runs recon,
crawls the site, then runs every check module. The whole point of the design:
one console command runs *all* the checks and every result lands in one log —
nothing is "separate".
"""

from __future__ import annotations

import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .http_client import HttpClient, have_requests
from .logger import ScanLogger, Severity, Finding, Confidence
from .crawler import Crawler, Form


class ScanContext:
    def __init__(self, target: str, http: HttpClient, log: ScanLogger, config: Dict[str, Any]):
        self.raw_target = target
        self.http = http
        self.log = log
        self.config = config

        parsed = urlparse(target)
        self.scheme = parsed.scheme or "http"
        self.host = parsed.hostname or ""
        self.port = parsed.port or (443 if self.scheme == "https" else 80)
        self.base_url = f"{self.scheme}://{parsed.netloc}"
        self.netloc = parsed.netloc

        self.ip: Optional[str] = None
        try:
            self.ip = socket.gethostbyname(self.host) if self.host else None
        except Exception:
            self.ip = None

        # populated by the crawler
        self.param_urls: List[str] = []
        self.forms: List[Form] = []

        # shared cache so modules don't refetch the homepage repeatedly
        self._baseline = None

    def report(self, **kwargs: Any) -> None:
        kwargs.setdefault("url", self.base_url)
        self.log.add(Finding(**kwargs))

    def baseline(self):
        """Cached GET of the base URL, used as a comparison reference."""
        if self._baseline is None:
            self._baseline = self.http.get(self.base_url)
        return self._baseline


class Scanner:
    def __init__(self, target: str, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.target = _normalize_target(target)

        self.log = ScanLogger(
            target=self.target,
            output_dir=self.config.get("output_dir", "scan_reports"),
            min_severity=self.config.get("min_severity", Severity.INFO),
            color=self.config.get("color"),
            quiet=self.config.get("quiet", False),
        )
        self.http = HttpClient(
            timeout=self.config.get("timeout", 12.0),
            verify_tls=self.config.get("verify_tls", False),
            proxy=self.config.get("proxy"),
            rate_per_second=self.config.get("rate", 20.0),
            extra_headers=self.config.get("headers"),
            cookies=self.config.get("cookies"),
        )
        self.ctx = ScanContext(self.target, self.http, self.log, self.config)

    # ---- module registry -------------------------------------------------

    def _load_modules(self):
        from .modules import ALL_MODULES

        only = set(self.config.get("only") or [])
        skip = set(self.config.get("skip") or [])
        selected = []
        for mod_cls in ALL_MODULES:
            name = mod_cls.name
            if only and name not in only:
                continue
            if name in skip:
                continue
            selected.append(mod_cls)
        return selected

    # ---- run -------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        self._banner()
        if not self.ctx.host:
            self.log.error(f"Could not parse a host from target: {self.target}")
            return self.log.finalize()

        reachable = self._preflight()
        if not reachable and not self.config.get("force"):
            self.log.error(
                "Target did not respond to an initial HTTP request. "
                "Use --force to scan anyway (port/TLS checks will still run)."
            )

        modules = self._load_modules()
        module_names = [m.name for m in modules]

        # recon + crawl first so injection modules have an attack surface.
        if "crawl" in module_names or any(
            n in module_names for n in ("sqli", "xss", "traversal", "cmdi", "open_redirect", "csrf")
        ):
            try:
                crawler = Crawler(self.ctx)
                self.ctx.param_urls, self.ctx.forms = crawler.crawl()
            except Exception as exc:
                self.log.error(f"crawler failed: {exc}")

        for mod_cls in modules:
            name = mod_cls.name
            self.log.status(f"Running module: {name}")
            try:
                mod_cls(self.ctx).run()
            except Exception as exc:  # a broken module must never abort the scan
                self.log.error(f"module '{name}' crashed: {exc!r}")

        return self.log.finalize()

    # ---- helpers ---------------------------------------------------------

    def _preflight(self) -> bool:
        self.log.status(f"Resolving {self.ctx.host} ...")
        if self.ctx.ip:
            self.log.status(f"{self.ctx.host} -> {self.ctx.ip}")
        else:
            self.log.error(f"DNS resolution failed for {self.ctx.host}")
        resp = self.ctx.baseline()
        if resp is not None:
            self.log.status(
                f"Target reachable: HTTP {resp.status_code} "
                f"({len(resp.text)} bytes, {resp.elapsed:.2f}s)"
            )
            return True
        return False

    def _banner(self) -> None:
        self.log.status("=" * 60)
        self.log.status(f"Self Security Scanner  ->  target: {self.target}")
        if not have_requests():
            self.log.status(
                "note: 'requests' not installed — using urllib fallback "
                "(pip install requests for best results)"
            )
        self.log.status(
            "AUTHORIZED USE ONLY: scan systems you own or have written "
            "permission to test."
        )
        self.log.status("=" * 60)


def _normalize_target(target: str) -> str:
    target = target.strip()
    if not target:
        return target
    if "://" not in target:
        target = "http://" + target
    return target.rstrip("/")
