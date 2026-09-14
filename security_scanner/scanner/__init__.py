"""
Self Security Scanner — a single-command web vulnerability scanner.

Run one console command against your own site and it enumerates the attack
surface and runs every check (recon, TLS, headers, open ports, SQLi, XSS,
path traversal, command injection, open redirect, CORS, HTTP methods, CSRF,
sensitive file exposure), logging every finding to one stream.

AUTHORIZED USE ONLY. Only scan systems you own or have explicit written
permission to test.
"""

from .core import Scanner, ScanContext
from .logger import ScanLogger, Severity, Confidence, Finding

__version__ = "1.0.0"
__all__ = [
    "Scanner",
    "ScanContext",
    "ScanLogger",
    "Severity",
    "Confidence",
    "Finding",
    "__version__",
]
