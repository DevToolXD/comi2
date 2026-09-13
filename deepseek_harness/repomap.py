"""Symbol-level repository map.

Handing a model a flat list of file paths makes it guess which files matter; it
reads the wrong ones and plans against code it never saw.  A map of *symbols*
-- every definition with its signature and line -- lets it pick correctly and
often removes the need to read a file at all.

Ranking matters as much as extraction.  Two signals, cheaply combined:

* **Task overlap** -- identifiers in the request that appear in a file's symbols
  or path.  Direct, and usually right when the task names something concrete.
* **Inbound references** -- how many *other* files use symbols this file
  defines.  A module everything imports is worth showing even when the task
  never names it, and this is what keeps a rename task from being planned
  against one call site out of thirty.

Python is parsed with ``ast``; other languages fall back to signature regexes.
Everything degrades to "no map" rather than raising.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
               "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", "target",
               ".next", "vendor", ".tox", "coverage", ".idea"}

# Languages we extract signatures from, with the regexes used when there is no
# real parser. Each pattern's first group is the symbol name.
_SIGNATURE_PATTERNS: dict[str, tuple[tuple[str, str], ...]] = {
    ".js":   ((r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
              (r"^\s*(?:export\s+)?class\s+(\w+)", "class"),
              (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", "function")),
    ".jsx":  ((r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
              (r"^\s*(?:export\s+)?class\s+(\w+)", "class")),
    ".ts":   ((r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
              (r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", "class"),
              (r"^\s*(?:export\s+)?interface\s+(\w+)", "interface"),
              (r"^\s*(?:export\s+)?type\s+(\w+)", "type")),
    ".go":   ((r"^func\s+(?:\([^)]*\)\s*)?(\w+)", "func"),
              (r"^type\s+(\w+)", "type")),
    ".rs":   ((r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", "fn"),
              (r"^\s*(?:pub\s+)?struct\s+(\w+)", "struct"),
              (r"^\s*(?:pub\s+)?enum\s+(\w+)", "enum"),
              (r"^\s*(?:pub\s+)?trait\s+(\w+)", "trait")),
    ".java": ((r"^\s*(?:public|private|protected).*\sclass\s+(\w+)", "class"),
              (r"^\s*(?:public|private|protected).*\s(\w+)\s*\([^)]*\)\s*\{", "method")),
    ".rb":   ((r"^\s*def\s+(\w+)", "def"), (r"^\s*class\s+(\w+)", "class")),
    ".php":  ((r"^\s*(?:public|private|protected)?\s*function\s+(\w+)", "function"),
              (r"^\s*class\s+(\w+)", "class")),
}
_SIGNATURE_PATTERNS[".tsx"] = _SIGNATURE_PATTERNS[".ts"]
_SIGNATURE_PATTERNS[".mjs"] = _SIGNATURE_PATTERNS[".js"]

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass
class Symbol:
    kind: str
    name: str
    line: int
    signature: str = ""

    def render(self) -> str:
        return f"    {self.line:>5}: {self.signature or f'{self.kind} {self.name}'}"


@dataclass
class FileEntry:
    path: str
    symbols: list[Symbol] = field(default_factory=list)
    lines: int = 0
    score: float = 0.0

    @property
    def names(self) -> set[str]:
        return {s.name for s in self.symbols}


@dataclass
class RepoMap:
    files: list[FileEntry] = field(default_factory=list)

    def render(self, max_chars: int = 24_000, max_symbols_per_file: int = 40) -> str:
        """Render highest-ranked files first, fitting the character budget.

        A file that will not fit whole is trimmed to fewer symbols rather than
        dropped, so a tight budget still yields the most relevant signatures
        instead of nothing at all.
        """
        out: list[str] = []
        used = 0
        shown = 0

        for entry in self.files:
            if not entry.symbols:
                continue
            head = f"{entry.path}  ({entry.lines} lines)"
            remaining = max_chars - used
            if remaining < len(head) + 8:
                break

            rendered: list[str] = []
            size = len(head)
            for sym in entry.symbols[:max_symbols_per_file]:
                line = sym.render()
                if size + len(line) + 1 > remaining:
                    break
                rendered.append(line)
                size += len(line) + 1

            hidden = len(entry.symbols) - len(rendered)
            if hidden > 0:
                note = f"    ... {hidden} more symbol(s)"
                if size + len(note) + 1 <= remaining:
                    rendered.append(note)
                    size += len(note) + 1

            out.append(head + ("\n" + "\n".join(rendered) if rendered else ""))
            used += size + 1
            shown += 1

        skipped = len([e for e in self.files if e.symbols]) - shown
        if skipped > 0:
            out.append(f"... {skipped} lower-ranked file(s) not shown")
        return "\n".join(out)

    def top_paths(self, n: int) -> list[str]:
        return [e.path for e in self.files[:n]]


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def _python_symbols(source: str) -> list[Symbol]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    symbols: list[Symbol] = []

    def sig_of(node: ast.AST) -> str:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = []
            a = node.args
            positional = [*a.posonlyargs, *a.args]
            # Defaults bind to the tail of the positional list.
            pad = len(positional) - len(a.defaults)
            for i, arg in enumerate(positional):
                text = arg.arg + (f": {ast.unparse(arg.annotation)}"
                                  if arg.annotation else "")
                if i >= pad:
                    text += f" = {ast.unparse(a.defaults[i - pad])}"
                args.append(text)
            if a.vararg:
                args.append("*" + a.vararg.arg)
            for arg, default in zip(a.kwonlyargs, a.kw_defaults):
                text = arg.arg + (f": {ast.unparse(arg.annotation)}"
                                  if arg.annotation else "")
                if default is not None:
                    text += f" = {ast.unparse(default)}"
                args.append(text)
            if a.kwarg:
                args.append("**" + a.kwarg.arg)
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            return f"{prefix} {node.name}({', '.join(args)}){ret}"
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            return f"class {node.name}({bases})" if bases else f"class {node.name}"
        return ""

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            symbols.append(Symbol(kind, node.name, node.lineno, sig_of(node)))
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        symbols.append(Symbol(
                            "method", child.name, child.lineno,
                            "  " + sig_of(child)))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    symbols.append(Symbol("const", t.id, node.lineno, f"{t.id} = ..."))
    return symbols


def _regex_symbols(source: str, suffix: str) -> list[Symbol]:
    patterns = _SIGNATURE_PATTERNS.get(suffix)
    if not patterns:
        return []
    compiled = [(re.compile(p), kind) for p, kind in patterns]
    symbols: list[Symbol] = []
    for i, line in enumerate(source.splitlines(), 1):
        if len(line) > 400:
            continue
        for rx, kind in compiled:
            m = rx.match(line)
            if m:
                symbols.append(Symbol(kind, m.group(1), i, line.strip()[:160]))
                break
    return symbols


def extract(path: Path, source: str) -> list[Symbol]:
    if path.suffix == ".py":
        return _python_symbols(source)
    return _regex_symbols(source, path.suffix)


# --------------------------------------------------------------------------
# Building and ranking
# --------------------------------------------------------------------------


def build(
    root: str | Path,
    task: str = "",
    *,
    max_files: int = 400,
    max_file_bytes: int = 400_000,
) -> RepoMap:
    """Index the repository and rank files by relevance to ``task``."""
    root = Path(root).resolve()
    entries: list[FileEntry] = []
    sources: dict[str, str] = {}

    for file in sorted(root.rglob("*")):
        if len(entries) >= max_files:
            break
        if not file.is_file() or file.suffix not in _known_suffixes():
            continue
        if any(part in IGNORE_DIRS for part in file.parts):
            continue
        try:
            if file.stat().st_size > max_file_bytes:
                continue
            source = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(file.relative_to(root))
        symbols = extract(file, source)
        if not symbols:
            continue
        sources[rel] = source
        entries.append(FileEntry(rel, symbols, source.count("\n") + 1))

    _rank(entries, sources, task)
    entries.sort(key=lambda e: -e.score)
    return RepoMap(entries)


def _known_suffixes() -> set[str]:
    return {".py", *_SIGNATURE_PATTERNS.keys()}


def _rank(entries: list[FileEntry], sources: dict[str, str], task: str) -> None:
    """Score each file on task overlap plus how much the rest of the repo uses it."""
    task_idents = {t.lower() for t in _IDENT.findall(task)}

    # Which files reference a symbol defined elsewhere.
    definitions: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        for name in e.names:
            if len(name) > 2:
                definitions[name].add(e.path)

    inbound: dict[str, int] = defaultdict(int)
    for path, source in sources.items():
        used = set(_IDENT.findall(source))
        for name in used & definitions.keys():
            for owner in definitions[name]:
                if owner != path:
                    inbound[owner] += 1

    max_inbound = max(inbound.values(), default=1) or 1
    for e in entries:
        haystack = {n.lower() for n in e.names} | set(
            _IDENT.findall(e.path.lower()))
        overlap = len(task_idents & haystack)
        # Task overlap dominates; centrality breaks ties and surfaces the
        # modules a task will touch without naming.
        e.score = overlap * 3.0 + (inbound[e.path] / max_inbound) * 1.0


def render_for(root: str | Path, task: str, max_chars: int = 24_000) -> str:
    """Convenience: build and render in one call, never raising."""
    try:
        return build(root, task).render(max_chars=max_chars)
    except Exception:
        return ""
