"""Base class shared by every check module."""

from __future__ import annotations

import concurrent.futures
from typing import Callable, Iterable, List

from ..logger import Severity, Confidence, Finding


class Module:
    #: unique short name used for --only / --skip
    name = "base"
    #: human description shown nowhere critical, but nice for docs
    description = ""

    def __init__(self, ctx):
        self.ctx = ctx
        self.http = ctx.http
        self.log = ctx.log
        self.config = ctx.config

    def run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # convenience --------------------------------------------------------

    def report(
        self,
        title: str,
        severity: Severity,
        category: str,
        **kw,
    ) -> None:
        kw.setdefault("url", self.ctx.base_url)
        kw.setdefault("module", self.name)
        self.log.add(Finding(title=title, severity=severity, category=category, **kw))

    def parallel(self, func: Callable, items: Iterable, workers: int = 12) -> List:
        """Run `func(item)` across a thread pool; collect non-None results."""
        items = list(items)
        if not items:
            return []
        workers = min(workers, max(1, len(items)), self.config.get("max_workers", 16))
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(func, it) for it in items]
            for fut in concurrent.futures.as_completed(futures):
                try:
                    r = fut.result()
                except Exception:
                    r = None
                if r is not None:
                    results.append(r)
        return results


# re-export for module authors
__all__ = ["Module", "Severity", "Confidence", "Finding"]
