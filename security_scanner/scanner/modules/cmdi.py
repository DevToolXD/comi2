"""OS command injection detection (output-based and time-based)."""

from __future__ import annotations

import re
import time

from .base import Module, Severity, Confidence
from .injection import enumerate_points


# command that echoes a recognizable token, chained with common separators.
# {tok} is a unique marker; we look for its "expanded" arithmetic form.
_MARKER_A = 73
_MARKER_B = 41  # 73*41 = 2993 ; `expr 73 \* 41` etc.  Simpler: echo a token.

# output-based: inject `;echo TOKEN;` style and look for TOKEN alone
_OUTPUT_TEMPLATES = [
    ";echo {tok};",
    "|echo {tok}",
    "||echo {tok}",
    "&echo {tok}",
    "&&echo {tok}",
    "`echo {tok}`",
    "$(echo {tok})",
    "\n echo {tok}\n",
    "; echo {tok} #",
]

# time-based: sleep and measure
_TIME_TEMPLATES = [
    ";sleep {t}",
    "|sleep {t}",
    "||sleep {t}",
    "&&sleep {t}",
    "`sleep {t}`",
    "$(sleep {t})",
    ";ping -c {t} 127.0.0.1",       # rough delay on *nix
    "& ping -n {t} 127.0.0.1 &",    # windows
]


class CommandInjectionModule(Module):
    name = "cmdi"
    description = "OS command injection (output + time based)"

    def run(self) -> None:
        points = enumerate_points(self.ctx)
        if not points:
            self.log.status("cmdi: no parameters/forms discovered to test")
            return
        self.log.status(f"cmdi: testing {len(points)} injection point(s)")
        self.parallel(self._test_point, points, workers=8)

    def _test_point(self, point):
        base = point.baseline_value()
        import secrets
        tok = "ci" + secrets.token_hex(5)

        # output-based
        for tpl in _OUTPUT_TEMPLATES:
            payload = tpl.format(tok=tok)
            resp = point.send(base + payload, mode="replace")
            if resp is None:
                continue
            # token must appear as standalone output, not merely echoed back inside
            # the reflected parameter value (so ensure the literal 'echo {tok}' text
            # is NOT what we matched).
            if tok in resp.text and ("echo " + tok) not in resp.text:
                self.report(
                    title="OS command injection (output-based)",
                    severity=Severity.CRITICAL,
                    category="Command Injection",
                    url=point.url,
                    parameter=point.param,
                    evidence=f"payload {payload!r} caused shell to echo unique token {tok!r}",
                    description="A shell metacharacter payload caused the server to execute an "
                    "injected command and return its output, indicating remote command execution.",
                    remediation="Never pass user input to a shell. Use language-native APIs with "
                    "argument arrays (no shell), strict allowlists, and least privilege.",
                    confidence=Confidence.CONFIRMED,
                    references=["https://owasp.org/www-community/attacks/Command_Injection"],
                )
                return None

        # time-based (optional / slower)
        if not self.config.get("time_based", True):
            return None
        delay = int(self.config.get("cmdi_delay", 5))
        t0 = time.monotonic()
        r0 = point.send(base, mode="replace")
        normal = (time.monotonic() - t0) if r0 is not None else 0.5
        threshold = max(delay - 1.0, normal + delay * 0.6)

        for tpl in _TIME_TEMPLATES:
            payload = tpl.format(t=delay)
            start = time.monotonic()
            resp = point.send(base + payload, mode="replace", timeout=delay + 8)
            elapsed = time.monotonic() - start
            if resp is None:
                continue
            if elapsed >= threshold:
                # confirm with larger delay
                cd = delay + 3
                cpayload = tpl.format(t=cd)
                cs = time.monotonic()
                cr = point.send(base + cpayload, mode="replace", timeout=cd + 8)
                ce = time.monotonic() - cs
                if cr is not None and ce >= cd - 1.0 and ce > elapsed:
                    self.report(
                        title="OS command injection (time-based blind)",
                        severity=Severity.CRITICAL,
                        category="Command Injection",
                        url=point.url,
                        parameter=point.param,
                        evidence=f"payload {payload!r} delayed {elapsed:.1f}s; confirm {cd}s -> {ce:.1f}s",
                        description="An injected sleep command delayed the response by the "
                        "requested time, proving blind command execution.",
                        remediation="Never pass user input to a shell; use argument-array APIs, "
                        "allowlists and least privilege.",
                        confidence=Confidence.CONFIRMED,
                        references=["https://owasp.org/www-community/attacks/Command_Injection"],
                    )
                    return None
        return None
