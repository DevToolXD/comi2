"""Tool registry and execution loop."""
from __future__ import annotations

import concurrent.futures as cf
import inspect
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import messages as M


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]
    timeout: float = 120.0

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)
    max_result_chars: int = 20_000

    def register(self, name: str, description: str, parameters: dict,
                 fn: Callable[..., Any], timeout: float = 120.0) -> None:
        self.tools[name] = Tool(name, description, parameters, fn, timeout)

    def tool(self, description: str, parameters: dict, timeout: float = 120.0):
        """Decorator form: ``@registry.tool("desc", {...})``."""
        def deco(fn: Callable[..., Any]):
            self.register(fn.__name__, description, parameters, fn, timeout)
            return fn
        return deco

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    # -- execution -------------------------------------------------------
    def run(self, call: M.ToolCall) -> str:
        tool = self.tools.get(call.name)
        if tool is None:
            return f"ERROR: unknown tool {call.name!r}. Available: {', '.join(self.tools)}"
        try:
            args = call.parsed()
        except Exception as exc:
            return f"ERROR: could not parse arguments: {exc}"
        try:
            sig = inspect.signature(tool.fn)
            accepted = {k: v for k, v in args.items() if k in sig.parameters}
            with cf.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(tool.fn, **accepted).result(timeout=tool.timeout)
        except cf.TimeoutError:
            return f"ERROR: tool {call.name} timed out after {tool.timeout}s"
        except Exception as exc:
            return f"ERROR: {type(exc).__name__}: {exc}"
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > self.max_result_chars:
            half = self.max_result_chars // 2
            text = (f"{text[:half]}\n\n...[{len(text) - self.max_result_chars} chars elided]...\n\n"
                    f"{text[-half:]}")
        return text

    def run_all(self, calls: list[M.ToolCall], parallel: bool = True) -> list[tuple[M.ToolCall, str]]:
        if not parallel or len(calls) == 1:
            return [(c, self.run(c)) for c in calls]
        with cf.ThreadPoolExecutor(max_workers=min(8, len(calls))) as pool:
            futures = [pool.submit(self.run, c) for c in calls]
            return [(c, f.result()) for c, f in zip(calls, futures)]


# --------------------------------------------------------------------------
# Built-in filesystem / shell tools
# --------------------------------------------------------------------------


def default_registry(root: str | Path = ".", *, allow_shell: bool = True) -> ToolRegistry:
    """A workspace-scoped toolset.  All paths are confined under ``root``."""
    root = Path(root).resolve()
    reg = ToolRegistry()

    def _safe(path: str) -> Path:
        p = (root / path).resolve()
        if not str(p).startswith(str(root)):
            raise ValueError(f"path escapes workspace root: {path}")
        return p

    reg.register(
        "read_file",
        "Read a UTF-8 text file from the workspace. Returns numbered lines.",
        {"type": "object",
         "properties": {"path": {"type": "string"},
                        "start": {"type": "integer", "description": "1-indexed start line"},
                        "end": {"type": "integer"}},
         "required": ["path"]},
        lambda path, start=1, end=None: _read_file(_safe(path), start, end),
    )

    reg.register(
        "write_file",
        "Create or overwrite a file with the given content.",
        {"type": "object",
         "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
         "required": ["path", "content"]},
        lambda path, content: _write_file(_safe(path), content),
    )

    reg.register(
        "list_files",
        "List files under a directory, respecting common ignore patterns.",
        {"type": "object",
         "properties": {"path": {"type": "string"}, "max_entries": {"type": "integer"}},
         "required": []},
        lambda path=".", max_entries=400: _list_files(_safe(path), max_entries),
    )

    reg.register(
        "search",
        "Regex search across workspace files. Returns path:line:text matches.",
        {"type": "object",
         "properties": {"pattern": {"type": "string"},
                        "glob": {"type": "string", "description": "e.g. '*.py'"},
                        "max_results": {"type": "integer"}},
         "required": ["pattern"]},
        lambda pattern, glob="*", max_results=80: _search(root, pattern, glob, max_results),
    )

    if allow_shell:
        reg.register(
            "run_command",
            "Run a shell command in the workspace root. Use for tests, linters, builds.",
            {"type": "object",
             "properties": {"command": {"type": "string"},
                            "timeout": {"type": "integer"}},
             "required": ["command"]},
            lambda command, timeout=300: run_command(command, root, timeout),
            timeout=360.0,
        )
    return reg


def _read_file(p: Path, start: int = 1, end: int | None = None) -> str:
    if not p.is_file():
        return f"ERROR: not a file: {p}"
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    end = end or len(lines)
    start = max(1, start)
    body = "\n".join(f"{i:>5}| {l}" for i, l in enumerate(lines[start - 1:end], start))
    return body or "(empty file)"


def _write_file(p: Path, content: str) -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {p} ({len(content)} chars, {content.count(chr(10)) + 1} lines)"


_IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
           ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".next"}


def _list_files(p: Path, max_entries: int) -> str:
    if not p.exists():
        return f"ERROR: no such path: {p}"
    out: list[str] = []
    for child in sorted(p.rglob("*")):
        if any(part in _IGNORE for part in child.parts):
            continue
        if child.is_file():
            out.append(str(child.relative_to(p)))
        if len(out) >= max_entries:
            out.append(f"...[truncated at {max_entries}]")
            break
    return "\n".join(out) or "(no files)"


def _search(root: Path, pattern: str, glob: str, max_results: int) -> str:
    import re as _re
    try:
        rx = _re.compile(pattern)
    except _re.error as exc:
        return f"ERROR: bad regex: {exc}"
    hits: list[str] = []
    for f in root.rglob(glob):
        if any(part in _IGNORE for part in f.parts) or not f.is_file():
            continue
        try:
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(root)}:{i}: {line.strip()[:240]}")
                    if len(hits) >= max_results:
                        return "\n".join(hits) + f"\n...[stopped at {max_results}]"
        except OSError:
            continue
    return "\n".join(hits) or "(no matches)"


@dataclass
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    duration: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def render(self, limit: int = 12_000) -> str:
        body = f"$ {self.command}\nexit={self.returncode} ({self.duration:.1f}s)\n"
        if self.stdout.strip():
            body += f"\n--- stdout ---\n{self.stdout[-limit:]}"
        if self.stderr.strip():
            body += f"\n--- stderr ---\n{self.stderr[-limit:]}"
        return body


def run_command(command: str, cwd: str | Path = ".", timeout: int = 300) -> str:
    return exec_command(command, cwd, timeout).render()


def exec_command(command: str, cwd: str | Path = ".", timeout: int = 300) -> CommandResult:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command, shell=True, cwd=str(cwd), capture_output=True,
            text=True, timeout=timeout, errors="replace",
            # Keep verification runs from leaving bytecode that a later
            # same-size edit could make stale.
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return CommandResult(command, proc.returncode, proc.stdout, proc.stderr,
                             time.monotonic() - started)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(command, 124, exc.stdout or "", f"timed out after {timeout}s",
                             time.monotonic() - started)
