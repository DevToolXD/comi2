"""Execution-guided candidate selection.

The strongest test-time technique available to a coding agent is not a better
prompt and not more thinking -- it is generating several independent patches and
letting the **test suite** pick the winner.  A model asked to review its own
patch grades its intent; a test run grades the code.

The distinction matters because the two failure modes are different.  Self-review
misses exactly the bugs the model's framing made invisible, which are the ones it
just wrote.  A test run has no framing.

Cost scales linearly with candidate count, so this is opt-in.  It pays where a
verification command exists and the task is hard enough that one attempt is a
coin flip; it is pure waste on a task a single attempt gets right.

**The ceiling is the test, not the technique.**  Selection can only reject what
the verification command actually catches.  Given ``assert add(2, 2) == 4``, a
patch returning ``a * b`` passes and may well be selected -- the oracle cannot
tell multiplication from addition on that input.  A weak suite does not make
this useless, but it does cap it: the gain is proportional to how
discriminating the command is, so point it at the sharpest test set available
rather than the fastest one.
"""
from __future__ import annotations

import concurrent.futures as cf
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .patch import PatchReport, apply_edits, parse_edits
from .repomap import IGNORE_DIRS
from .tools import CommandResult, exec_command


@dataclass
class Candidate:
    index: int
    response: str = ""
    report: PatchReport | None = None
    verification: CommandResult | None = None
    workspace: Path | None = None
    error: str = ""

    @property
    def applied(self) -> bool:
        return bool(self.report and self.report.applied)

    @property
    def passed(self) -> bool:
        return bool(self.verification and self.verification.ok)

    @property
    def diff_size(self) -> int:
        """Total replaced characters -- a proxy for collateral change."""
        if not self.report:
            return 1 << 30
        return sum(len(o.edit.replace) + len(o.edit.search) for o in self.report.applied)

    def rank_key(self) -> tuple:
        # Passing beats applying; applying beats nothing; smaller diff breaks ties.
        return (not self.passed, not self.applied, self.diff_size)

    def summary(self) -> str:
        state = ("passed" if self.passed else
                 "applied but failed verification" if self.applied else
                 f"no edits applied ({self.error or 'none parsed'})")
        return f"candidate {self.index}: {state}, {len(self.report.applied) if self.report else 0} edit(s)"


@dataclass
class SelectionResult:
    winner: Candidate | None
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def any_passed(self) -> bool:
        return any(c.passed for c in self.candidates)

    def render(self) -> str:
        lines = [c.summary() for c in self.candidates]
        if self.winner:
            lines.append(f"-> selected candidate {self.winner.index}")
        return "\n".join(lines)


def _copy_workspace(root: Path, dest: Path, max_bytes: int) -> None:
    """Copy the tree, skipping build/VCS noise, refusing an unreasonable size."""
    total = 0
    for src in root.rglob("*"):
        if any(part in IGNORE_DIRS for part in src.relative_to(root).parts):
            continue
        if not src.is_file():
            continue
        try:
            total += src.stat().st_size
        except OSError:
            continue
        if total > max_bytes:
            raise ValueError(
                f"workspace exceeds {max_bytes} bytes; raise max_workspace_bytes "
                f"or disable candidate selection for this repo"
            )
        target = dest / src.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, target)
        except OSError:
            continue


def select(
    root: str | Path,
    generate: Callable[[int], str],
    verify_command: str | None,
    *,
    n: int = 3,
    max_workers: int = 4,
    max_workspace_bytes: int = 256 * 1024 * 1024,
    verify_timeout: int = 600,
) -> SelectionResult:
    """Generate ``n`` patches independently, test each in isolation, pick a winner.

    ``generate(i)`` must return a model response containing SEARCH/REPLACE blocks.
    It is called concurrently, so it must not share mutable state across calls.
    The winner's files are **not** written back to ``root``; the caller decides,
    using :func:`adopt`.
    """
    root = Path(root).resolve()
    tmp = Path(tempfile.mkdtemp(prefix="dsh-candidates-"))
    candidates: list[Candidate] = []

    try:
        # Generation is API-bound; run it concurrently.
        with cf.ThreadPoolExecutor(max_workers=min(max_workers, n)) as pool:
            responses = list(pool.map(_safe(generate), range(n)))

        for i, (text, err) in enumerate(responses):
            cand = Candidate(index=i, response=text, error=err)
            if err:
                candidates.append(cand)
                continue
            edits = parse_edits(text)
            if not edits:
                cand.error = "no SEARCH/REPLACE blocks in response"
                candidates.append(cand)
                continue
            workspace = tmp / f"c{i}"
            try:
                _copy_workspace(root, workspace, max_workspace_bytes)
            except ValueError as exc:
                cand.error = str(exc)
                candidates.append(cand)
                continue
            cand.workspace = workspace
            cand.report = apply_edits(edits, workspace)
            candidates.append(cand)

        # Verification is subprocess-bound; also concurrent, in isolated copies.
        runnable = [c for c in candidates if c.applied and verify_command]
        if runnable:
            with cf.ThreadPoolExecutor(max_workers=min(max_workers, len(runnable))) as pool:
                futures = {
                    pool.submit(exec_command, verify_command, c.workspace, verify_timeout): c
                    for c in runnable
                }
                for fut in cf.as_completed(futures):
                    futures[fut].verification = fut.result()

        ranked = sorted(candidates, key=lambda c: c.rank_key())
        winner = ranked[0] if ranked and ranked[0].applied else None
        return SelectionResult(winner, candidates)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def adopt(winner: Candidate, root: str | Path) -> list[str]:
    """Copy a winning candidate's changed files back into the real workspace."""
    if not winner.workspace or not winner.report:
        return []
    root = Path(root).resolve()
    changed: list[str] = []
    for rel in winner.report.files_changed:
        src = winner.workspace / rel
        if not src.is_file():
            continue
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        _invalidate(dst)
        changed.append(rel)
    return changed


def cleanup(result: SelectionResult) -> None:
    """Remove every candidate workspace.  Safe to call twice."""
    roots = {c.workspace.parent for c in result.candidates if c.workspace}
    for r in roots:
        shutil.rmtree(r, ignore_errors=True)


def _invalidate(path: Path) -> None:
    from .patch import _invalidate_bytecode
    _invalidate_bytecode(path)


def _safe(fn: Callable[[int], str]) -> Callable[[int], tuple[str, str]]:
    def wrapped(i: int) -> tuple[str, str]:
        try:
            return fn(i), ""
        except Exception as exc:  # one bad candidate must not sink the batch
            return "", f"{type(exc).__name__}: {exc}"
    return wrapped
