#!/usr/bin/env python3
"""
Self Security Scanner — command-line entry point.

Usage (the whole point: one line in the console):

    python3 scan.py https://your-site.example.com

It crawls the target, runs every vulnerability check, prints findings live and
writes a full log + JSON report to ./scan_reports/.

    python3 scan.py https://site.com --aggressive
    python3 scan.py https://site.com --only sqli,xss,headers
    python3 scan.py https://site.com --skip ports --rate 5
    python3 scan.py https://site.com --cookie "session=abc123" --header "X-Api-Key: k"

AUTHORIZED USE ONLY — scan only systems you own or are explicitly permitted
to test. Active injection payloads are sent to the target.
"""

from __future__ import annotations

import argparse
import sys

# Allow running both as `python3 scan.py` and `python3 -m scanner`.
try:
    from scanner.core import Scanner
    from scanner.logger import Severity
    from scanner.modules import MODULE_NAMES
except ModuleNotFoundError:  # pragma: no cover
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scanner.core import Scanner
    from scanner.logger import Severity
    from scanner.modules import MODULE_NAMES


_SEV_MAP = {s.name.lower(): s for s in Severity}


def _parse_kv_list(values, sep=":"):
    out = {}
    for item in values or []:
        if sep in item:
            k, v = item.split(sep, 1)
            out[k.strip()] = v.strip()
    return out


def _parse_cookies(values):
    out = {}
    for item in values or []:
        for pair in item.split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scan.py",
        description="Self Security Scanner — one-command web vulnerability scan "
        "(AUTHORIZED USE ONLY).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("target", help="Target URL or host, e.g. https://your-site.com")
    p.add_argument("--only", help="Comma-separated modules to run exclusively "
                   f"(choices: {', '.join(MODULE_NAMES)})")
    p.add_argument("--skip", help="Comma-separated modules to skip")
    p.add_argument("--aggressive", action="store_true",
                   help="Test all parameters for every class (traversal/redirect on all params)")
    p.add_argument("--no-time-based", action="store_true",
                   help="Disable slow time-based blind SQLi/command-injection probes")
    p.add_argument("--no-ports", action="store_true", help="Skip the TCP port scan")
    p.add_argument("--rate", type=float, default=20.0,
                   help="Max requests/second (default 20; lower to be gentler)")
    p.add_argument("--timeout", type=float, default=12.0, help="Per-request timeout seconds")
    p.add_argument("--max-pages", type=int, default=60, help="Max pages to crawl")
    p.add_argument("--max-depth", type=int, default=3, help="Max crawl depth")
    p.add_argument("--proxy", help="Proxy URL, e.g. http://127.0.0.1:8080 (route via Burp/ZAP)")
    p.add_argument("--verify-tls", action="store_true",
                   help="Verify TLS certs (default: off, so self-signed test sites still scan)")
    p.add_argument("--header", action="append", metavar="H:V",
                   help="Extra request header (repeatable), e.g. --header 'X-Api-Key: abc'")
    p.add_argument("--cookie", action="append", metavar="COOKIES",
                   help="Cookies to send (repeatable), e.g. --cookie 'session=abc; theme=x'")
    p.add_argument("--seed", action="append", metavar="URL",
                   help="Extra seed URL(s) to add to the crawl")
    p.add_argument("--min-severity", choices=list(_SEV_MAP), default="info",
                   help="Only print findings at/above this severity (log/JSON keep everything)")
    p.add_argument("--output-dir", default="scan_reports", help="Where to write reports")
    p.add_argument("--no-color", action="store_true", help="Disable colored output")
    p.add_argument("--quiet", action="store_true", help="Suppress live output (still writes files)")
    p.add_argument("--force", action="store_true",
                   help="Scan even if the initial HTTP request fails")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip the authorization confirmation prompt")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # Authorization gate — active payloads are sent, so make intent explicit.
    if not args.yes:
        sys.stderr.write(
            "\n" + "=" * 68 + "\n"
            " AUTHORIZATION REQUIRED\n"
            " This tool sends active security-test traffic (SQLi/XSS/etc.)\n"
            f" to: {args.target}\n"
            " Only continue if you OWN this system or have WRITTEN permission\n"
            " to test it. Unauthorized scanning may be illegal.\n"
            + "=" * 68 + "\n"
        )
        try:
            answer = input(" Type 'yes' to confirm authorized testing: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer not in ("yes", "y"):
            sys.stderr.write(" Aborted (no confirmation).\n")
            return 2

    config = {
        "only": [m.strip() for m in args.only.split(",")] if args.only else None,
        "skip": [m.strip() for m in args.skip.split(",")] if args.skip else None,
        "aggressive": args.aggressive,
        "time_based": not args.no_time_based,
        "port_scan": not args.no_ports,
        "rate": args.rate,
        "timeout": args.timeout,
        "max_pages": args.max_pages,
        "max_depth": args.max_depth,
        "proxy": args.proxy,
        "verify_tls": args.verify_tls,
        "headers": _parse_kv_list(args.header),
        "cookies": _parse_cookies(args.cookie),
        "seed_urls": args.seed or [],
        "min_severity": _SEV_MAP[args.min_severity],
        "output_dir": args.output_dir,
        "color": (False if args.no_color else None),
        "quiet": args.quiet,
        "force": args.force,
    }

    scanner = Scanner(args.target, config)
    try:
        report = scanner.run()
    except KeyboardInterrupt:
        sys.stderr.write("\n[!] Interrupted — writing partial report...\n")
        report = scanner.log.finalize()
        return 130

    # Exit code reflects worst finding: 0 clean, 1 low/medium, 2 high/critical.
    summary = report.get("summary", {})
    if summary.get("CRITICAL", 0) or summary.get("HIGH", 0):
        return 2
    if summary.get("MEDIUM", 0) or summary.get("LOW", 0):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
