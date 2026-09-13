"""SEARCH/REPLACE edit blocks: parsing, validation and application.

Whole-file rewrites waste output tokens and quietly drop code the model did not
bother to reproduce; unified diffs need exact line numbers and hunk headers that
models get wrong.  Anchored search/replace is the reliable middle: the model
quotes the code it is replacing, so a mismatch is *detectable* instead of
silently corrupting a file.

Failure modes are handled rather than raised at the caller:

* whitespace-only drift -> retried with normalised indentation
* a block matching more than once -> rejected as ambiguous, never applied to the
  first hit
* a block matching nowhere -> reported with the closest lines in the file, which
  is what lets the model repair it on the next turn
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .errors import PatchError

FENCE = re.compile(
    r"^(?P<path>[^\n`]+?)\n"
    r"<{5,9} SEARCH\s*\n"
    r"(?P<search>.*?)"
    r"^={5,9}\s*\n"
    r"(?P<replace>.*?)"
    r"^>{5,9} REPLACE\s*$",
    re.M | re.S,
)

EDIT_FORMAT_SPEC = """\
Return every edit as a SEARCH/REPLACE block in exactly this format:

path/to/file.py
<<<<<<< SEARCH
    the exact existing lines, copied character for character
=======
    the replacement lines
>>>>>>> REPLACE

Rules that make the difference between an edit that applies and one that fails:
- The SEARCH text must match the file EXACTLY -- same indentation, same spelling, \
same comments. Copy it, do not retype it from memory.
- Include just enough surrounding lines to make the match UNIQUE in that file. \
A block that matches twice is rejected.
- To create a new file, use the file path with an empty SEARCH section.
- To delete code, leave the REPLACE section empty.
- One logical change per block. Many small blocks beat one large one.
- Do not put the blocks inside markdown code fences.
"""


@dataclass
class Edit:
    path: str
    search: str
    replace: str

    @property
    def is_creation(self) -> bool:
        return not self.search.strip()


@dataclass
class EditOutcome:
    edit: Edit
    ok: bool
    detail: str = ""


@dataclass
class PatchReport:
    applied: list[EditOutcome] = field(default_factory=list)
    failed: list[EditOutcome] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def render(self) -> str:
        lines = []
        if self.applied:
            lines.append(f"applied {len(self.applied)} edit(s) across "
                         f"{len(self.files_changed)} file(s): {', '.join(self.files_changed)}")
        if self.failed:
            lines.append(f"FAILED {len(self.failed)} edit(s) -- fix and resend these blocks:")
            for f in self.failed:
                lines.append(f"\n  file: {f.edit.path}\n  reason: {f.detail}")
        return "\n".join(lines) or "no edits found in the response"


def parse_edits(text: str) -> list[Edit]:
    """Extract SEARCH/REPLACE blocks, tolerating surrounding prose and fences."""
    cleaned = re.sub(r"^```[a-zA-Z0-9_+-]*\s*$", "", text, flags=re.M)
    edits: list[Edit] = []
    for m in FENCE.finditer(cleaned):
        path = m.group("path").strip().strip("`").lstrip("#").strip()
        # Models sometimes prefix the path with prose on the same line.
        path = path.split()[-1] if " " in path and "/" in path.split()[-1] else path
        edits.append(Edit(path, m.group("search"), m.group("replace")))
    return edits


def _find_unique(haystack: str, needle: str) -> tuple[int, str] | None:
    """Locate ``needle`` exactly once, retrying with normalised indentation."""
    count = haystack.count(needle)
    if count == 1:
        return haystack.index(needle), needle
    if count > 1:
        return None

    # Whitespace drift: compare line-by-line with trailing space stripped.
    hay_lines = haystack.splitlines(keepends=True)
    needle_lines = [l.rstrip() for l in needle.rstrip("\n").splitlines()]
    if not needle_lines:
        return None
    window = len(needle_lines)
    matches = []
    for i in range(len(hay_lines) - window + 1):
        chunk = [l.rstrip() for l in hay_lines[i:i + window]]
        if chunk == needle_lines:
            matches.append(i)
    if len(matches) != 1:
        return None
    start_line = matches[0]
    offset = sum(len(l) for l in hay_lines[:start_line])
    actual = "".join(hay_lines[start_line:start_line + window])
    return offset, actual


def _near_miss(haystack: str, needle: str, n: int = 3) -> str:
    """Show the closest existing lines so the model can correct its block."""
    first = (needle.strip().splitlines() or [""])[0].strip()
    if not first:
        return ""
    lines = haystack.splitlines()
    best = difflib.get_close_matches(first, [l.strip() for l in lines], n=n, cutoff=0.5)
    if not best:
        return ""
    hints = []
    for b in best:
        for i, l in enumerate(lines, 1):
            if l.strip() == b:
                hints.append(f"    line {i}: {l}")
                break
    return "\n  closest lines actually in the file:\n" + "\n".join(hints)


def apply_edits(edits: list[Edit], root: str | Path = ".", *, dry_run: bool = False) -> PatchReport:
    report = PatchReport()
    root = Path(root).resolve()
    # Buffer per file so several edits to one file see each other's results.
    buffers: dict[Path, str] = {}

    for edit in edits:
        try:
            target = (root / edit.path).resolve()
            if not str(target).startswith(str(root)):
                raise PatchError(f"path escapes workspace root: {edit.path}")
        except (PatchError, OSError) as exc:
            report.failed.append(EditOutcome(edit, False, str(exc)))
            continue

        if edit.is_creation:
            if target.exists() and target.read_text(encoding="utf-8", errors="replace").strip():
                report.failed.append(EditOutcome(
                    edit, False,
                    f"{edit.path} already exists and is non-empty; use a SEARCH block "
                    f"to modify it instead of an empty SEARCH"))
                continue
            buffers[target] = edit.replace
            report.applied.append(EditOutcome(edit, True, f"created {edit.path}"))
            continue

        if target not in buffers:
            if not target.is_file():
                report.failed.append(EditOutcome(edit, False, f"no such file: {edit.path}"))
                continue
            buffers[target] = target.read_text(encoding="utf-8", errors="replace")

        content = buffers[target]
        found = _find_unique(content, edit.search)
        if found is None:
            occurrences = content.count(edit.search)
            if occurrences > 1:
                detail = (f"SEARCH block matches {occurrences} places in {edit.path}; "
                          f"include more surrounding context to make it unique")
            else:
                detail = (f"SEARCH block not found in {edit.path}"
                          + _near_miss(content, edit.search))
            report.failed.append(EditOutcome(edit, False, detail))
            continue

        offset, actual = found
        buffers[target] = content[:offset] + edit.replace + content[offset + len(actual):]
        report.applied.append(EditOutcome(edit, True, f"patched {edit.path}"))

    if not dry_run:
        for path, content in buffers.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            _invalidate_bytecode(path)
    report.files_changed = sorted(str(p.relative_to(root)) for p in buffers)
    return report


def _invalidate_bytecode(path: Path) -> None:
    """Drop cached bytecode for a source file we just rewrote.

    CPython validates a ``.pyc`` against the source's (mtime, size).  An edit
    that leaves the file the same size within one mtime tick -- ``VALUE = 1``
    becoming ``VALUE = 2`` -- leaves the stale cache looking valid, so the next
    subprocess imports the *old* code.  The agent then sees its own fix fail and
    burns repair rounds chasing a bug it already fixed.
    """
    if path.suffix != ".py":
        return
    cache = path.parent / "__pycache__"
    if not cache.is_dir():
        return
    for pyc in cache.glob(f"{path.stem}.*.pyc"):
        try:
            pyc.unlink()
        except OSError:
            pass
