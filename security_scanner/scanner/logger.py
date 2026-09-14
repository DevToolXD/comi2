"""
Findings logger for the security scanner.

Every check in every module funnels its results through here so that
*nothing* is treated as "separate" or hidden: one unified stream of findings,
printed live to the console and persisted to disk (a human-readable log and a
machine-readable JSON report).
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import sys
import threading
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(Enum):
    """Ordered severity levels. Higher value == more serious."""

    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        return self.name

    def __ge__(self, other: "Severity") -> bool:  # for filtering / sorting
        if isinstance(other, Severity):
            return self.value >= other.value
        return NotImplemented

    def __gt__(self, other: "Severity") -> bool:
        if isinstance(other, Severity):
            return self.value > other.value
        return NotImplemented

    def __lt__(self, other: "Severity") -> bool:
        if isinstance(other, Severity):
            return self.value < other.value
        return NotImplemented

    def __le__(self, other: "Severity") -> bool:
        if isinstance(other, Severity):
            return self.value <= other.value
        return NotImplemented


class Confidence(Enum):
    """How sure the scanner is that the finding is real."""

    CONFIRMED = "confirmed"   # actively demonstrated (e.g. time-based SQLi delay observed)
    FIRM = "firm"             # strong indicator (e.g. DB error signature reflected)
    TENTATIVE = "tentative"   # heuristic / worth manual review


# ANSI colors keyed by severity. Disabled automatically when not a TTY.
_COLORS = {
    Severity.CRITICAL: "\033[1;97;41m",  # white on red, bold
    Severity.HIGH: "\033[1;31m",         # bold red
    Severity.MEDIUM: "\033[1;33m",       # bold yellow
    Severity.LOW: "\033[1;36m",          # bold cyan
    Severity.INFO: "\033[0;37m",         # grey
}
_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"


@dataclasses.dataclass
class Finding:
    """A single security observation."""

    title: str
    severity: Severity
    category: str
    description: str = ""
    url: str = ""
    parameter: str = ""
    evidence: str = ""
    remediation: str = ""
    confidence: Confidence = Confidence.FIRM
    references: List[str] = dataclasses.field(default_factory=list)
    module: str = ""
    timestamp: str = dataclasses.field(
        default_factory=lambda: _dt.datetime.now().isoformat(timespec="seconds")
    )

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["severity"] = self.severity.label
        d["confidence"] = self.confidence.value
        return d

    def dedup_key(self) -> tuple:
        # Two findings are "the same" if they hit the same class of issue in the
        # same place. Evidence may differ (different payloads) but we only need
        # to report the location once per issue type.
        return (self.category, self.title, self.url, self.parameter)


class ScanLogger:
    """Thread-safe collector + live printer for findings and progress."""

    def __init__(
        self,
        target: str,
        output_dir: str = "scan_reports",
        min_severity: Severity = Severity.INFO,
        color: Optional[bool] = None,
        quiet: bool = False,
    ) -> None:
        self.target = target
        self.min_severity = min_severity
        self.quiet = quiet
        self._lock = threading.RLock()
        self._findings: List[Finding] = []
        self._seen: set = set()
        self.started_at = _dt.datetime.now()

        if color is None:
            color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
        self.color = color

        os.makedirs(output_dir, exist_ok=True)
        stamp = self.started_at.strftime("%Y%m%d_%H%M%S")
        safe_target = _slugify(target)
        self.log_path = os.path.join(output_dir, f"scan_{safe_target}_{stamp}.log")
        self.json_path = os.path.join(output_dir, f"scan_{safe_target}_{stamp}.json")
        self._logfile = open(self.log_path, "a", encoding="utf-8")
        self._write_raw(f"# Security scan of {target}")
        self._write_raw(f"# Started: {self.started_at.isoformat(timespec='seconds')}")
        self._write_raw("")

    # ---- public API used by modules -------------------------------------

    def add(self, finding: Finding) -> bool:
        """Record a finding. Returns True if it was new (not a duplicate)."""
        with self._lock:
            key = finding.dedup_key()
            if key in self._seen:
                return False
            self._seen.add(key)
            self._findings.append(finding)
            self._emit(finding)
            return True

    def finding(self, **kwargs: Any) -> bool:
        return self.add(Finding(**kwargs))

    def status(self, message: str) -> None:
        """Progress line — not a finding, just tells the user what's happening."""
        with self._lock:
            line = f"[*] {message}"
            self._write_raw(line)
            if not self.quiet:
                if self.color:
                    sys.stdout.write(f"{_DIM}[*]{_RESET} {message}\n")
                else:
                    sys.stdout.write(line + "\n")
                sys.stdout.flush()

    def error(self, message: str) -> None:
        with self._lock:
            line = f"[!] ERROR: {message}"
            self._write_raw(line)
            if not self.quiet:
                if self.color:
                    sys.stderr.write(f"\033[1;31m[!]\033[0m {message}\n")
                else:
                    sys.stderr.write(line + "\n")
                sys.stderr.flush()

    # ---- rendering -------------------------------------------------------

    def _emit(self, f: Finding) -> None:
        tag = f"[{f.severity.label}]"
        parts = [f"{tag} {f.category}: {f.title}"]
        if f.url:
            parts.append(f"    url:       {f.url}")
        if f.parameter:
            parts.append(f"    parameter: {f.parameter}")
        if f.evidence:
            parts.append(f"    evidence:  {_truncate(f.evidence, 500)}")
        parts.append(f"    confidence:{f.confidence.value}  module:{f.module}")
        block = "\n".join(parts)
        self._write_raw(block)
        self._write_raw("")

        if self.quiet or f.severity < self.min_severity:
            return

        if self.color:
            col = _COLORS.get(f.severity, "")
            head = f"{col}{tag}{_RESET} {_BOLD}{f.category}{_RESET}: {f.title}"
            sys.stdout.write(head + "\n")
            if f.url:
                sys.stdout.write(f"    {_DIM}url:{_RESET}       {f.url}\n")
            if f.parameter:
                sys.stdout.write(f"    {_DIM}parameter:{_RESET} {f.parameter}\n")
            if f.evidence:
                sys.stdout.write(f"    {_DIM}evidence:{_RESET}  {_truncate(f.evidence, 300)}\n")
            sys.stdout.write(
                f"    {_DIM}confidence: {f.confidence.value}{_RESET}\n"
            )
        else:
            sys.stdout.write(block + "\n")
        sys.stdout.flush()

    def _write_raw(self, line: str) -> None:
        try:
            self._logfile.write(line + "\n")
            self._logfile.flush()
        except Exception:
            pass

    # ---- summary / persistence ------------------------------------------

    @property
    def findings(self) -> List[Finding]:
        with self._lock:
            return list(self._findings)

    def counts(self) -> Dict[str, int]:
        c = {s.label: 0 for s in Severity}
        for f in self._findings:
            c[f.severity.label] += 1
        return c

    def finalize(self) -> Dict[str, Any]:
        """Write the JSON report and print the final summary. Returns metadata."""
        with self._lock:
            finished = _dt.datetime.now()
            duration = (finished - self.started_at).total_seconds()
            ordered = sorted(
                self._findings, key=lambda f: f.severity.value, reverse=True
            )
            report = {
                "target": self.target,
                "started_at": self.started_at.isoformat(timespec="seconds"),
                "finished_at": finished.isoformat(timespec="seconds"),
                "duration_seconds": round(duration, 2),
                "summary": self.counts(),
                "total_findings": len(ordered),
                "findings": [f.to_dict() for f in ordered],
            }
            try:
                with open(self.json_path, "w", encoding="utf-8") as fh:
                    json.dump(report, fh, indent=2, ensure_ascii=False)
            except Exception as exc:  # pragma: no cover
                self.error(f"could not write JSON report: {exc}")

            self._print_summary(report, duration)
            try:
                self._logfile.close()
            except Exception:
                pass
            return report

    def _print_summary(self, report: Dict[str, Any], duration: float) -> None:
        c = report["summary"]
        line = "=" * 64
        out = [
            "",
            line,
            f" SCAN COMPLETE  ->  {self.target}",
            f" duration: {duration:.1f}s    findings: {report['total_findings']}",
            line,
        ]
        order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
        for sev in order:
            n = c[sev.label]
            marker = "!" if sev.value >= Severity.HIGH.value and n else " "
            out.append(f"  [{marker}] {sev.label:<9} {n}")
        out.append(line)
        out.append(f" full log:    {self.log_path}")
        out.append(f" json report: {self.json_path}")
        out.append(line)
        text = "\n".join(out)
        self._write_raw(text)
        if self.quiet:
            return
        if self.color:
            colored = []
            for sev in order:
                n = c[sev.label]
                col = _COLORS.get(sev, "")
                mark = "!" if sev.value >= Severity.HIGH.value and n else " "
                colored.append(f"  {col}[{mark}] {sev.label:<9}{_RESET} {n}")
            print("")
            print(_BOLD + line + _RESET)
            print(f" {_BOLD}SCAN COMPLETE{_RESET}  ->  {self.target}")
            print(f" duration: {duration:.1f}s    findings: {report['total_findings']}")
            print(_BOLD + line + _RESET)
            print("\n".join(colored))
            print(_BOLD + line + _RESET)
            print(f" full log:    {self.log_path}")
            print(f" json report: {self.json_path}")
            print(_BOLD + line + _RESET)
        else:
            print(text)


def _truncate(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _slugify(target: str) -> str:
    keep = []
    for ch in target:
        if ch.isalnum():
            keep.append(ch)
        elif ch in ".-_":
            keep.append(ch)
        else:
            keep.append("_")
    slug = "".join(keep).strip("._-")
    return (slug or "target")[:60]
