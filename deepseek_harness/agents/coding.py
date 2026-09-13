"""Coding agent: explore -> plan -> edit -> apply -> verify -> repair -> review.

Design notes worth knowing before changing this file:

* **Plan and edit are separate calls, often on separate models.**  Planning is
  where thinking-mode depth pays; emitting exact SEARCH text is a transcription
  job where it does not.  Mixing them makes the model reason *while* quoting
  code, which is where malformed blocks come from.
* **A failed patch is repaired, not regenerated.**  Sending back the specific
  reason ("matched 3 places", plus the closest real lines) converges in one or
  two turns; asking for the whole answer again usually reproduces the mistake.
* **Every failed attempt is written to dead-end memory.**  Without it a long
  repair loop cycles between the same two wrong fixes.
* **The review pass reads the diff, not the plan.**  Asking a model whether it
  followed its own plan gets you "yes"; asking what a reviewer would reject gets
  you real findings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import messages as M
from ..client import DeepSeekClient, Thinking
from ..config import Config
from .. import repomap, selection
from ..patch import EDIT_FORMAT_SPEC, PatchReport, apply_edits, parse_edits
from ..tools import CommandResult, ToolRegistry, default_registry, exec_command
from .base import Agent, AgentResult, parse_json

SYSTEM = """\
You are a senior engineer working directly in a real repository. Your edits are \
applied verbatim, so they must be correct and complete.

Standing rules:
- Match the surrounding code: its naming, its error handling, its comment \
density, its idioms. Code that reads as foreign is a defect even when it works.
- Change the minimum that solves the problem. Do not refactor adjacent code, \
rename things, reformat, or add dependencies unless the task requires it.
- Handle the edge cases the existing code handles. If it validates input, yours does too.
- Never invent an API. If you are unsure a function exists, read the file.
- No placeholder comments, no `...`, no "implementation left as an exercise".
"""

EXPLORE = """\
Task:
{task}

Repository map (definitions, ranked by relevance):
{tree}

Before planning, identify what you must read. List up to {k} files, most \
important first, each with one line on why. Prefer the files that define the \
behaviour being changed over files that merely call it.

Reply with JSON only: {{"files": [{{"path": "...", "why": "..."}}]}}"""

PLAN = """\
Now plan the change.

Relevant source:
{sources}

Produce:
1. ROOT CAUSE / REQUIREMENT: what is actually being asked, in one or two sentences.
2. CHANGES: for each file, what changes and why. Be specific about functions.
3. RISKS: what could break elsewhere, and what existing behaviour must be preserved.
4. VERIFICATION: the exact command that would prove this works.

Keep it tight. Do not write the code yet."""

IMPLEMENT = """\
Implement the plan now.

{fmt}

Before each block, one line saying what it does. No other prose."""

REPAIR_PATCH = """\
Some edit blocks did not apply:

{report}

Resend ONLY the failed blocks, corrected. The SEARCH text must match the file \
byte for byte -- re-read the file if you are unsure rather than guessing. Do not \
resend blocks that already applied."""

REPAIR_TEST = """\
The verification command failed.

{output}

Diagnose before editing: is the failure in the code you changed, in a test whose \
expectation your change invalidated, or pre-existing and unrelated?

State the diagnosis in one or two lines, then send SEARCH/REPLACE blocks that \
fix the root cause. Do not weaken, skip or delete a test to make it pass.

{dead_ends}"""

CANDIDATE_FRAMINGS = [
    "Prefer the smallest change that fixes this. Touch as few lines as possible.",
    "Fix the root cause rather than the symptom, even if that means changing a "
    "function signature or moving logic.",
    "Before writing anything, check whether the codebase already has a helper, "
    "pattern or abstraction for this and reuse it instead of adding new code.",
    "Consider the edge cases first -- empty, zero, negative, duplicate, unicode, "
    "concurrent -- and write the fix so those are handled, then the main path.",
    "Assume your first instinct is wrong. Name the obvious fix, say why it is "
    "insufficient, then implement the one that survives that critique.",
]

REVIEW = """\
Review the diff you just produced, as a reviewer who wants to reject it.

{diff}

Look specifically for: off-by-one and boundary errors; None/empty/zero inputs; \
error paths that swallow failures; resource leaks; changed behaviour the task \
did not ask for; anything that contradicts the surrounding code's conventions.

Reply with JSON only:
{{"verdict": "ship" | "fix",
  "findings": [{{"severity": "high"|"medium"|"low", "file": "...", "issue": "...", "fix": "..."}}]}}

Report only defects you can point at in the diff. An empty findings list is a \
valid and common answer."""


@dataclass
class CodingResult(AgentResult):
    patch: PatchReport | None = None
    verification: CommandResult | None = None
    files_changed: list[str] = field(default_factory=list)


class CodingAgent(Agent):
    """Plan-edit-verify-repair loop over a real working tree."""

    system_prompt = SYSTEM

    def __init__(
        self,
        client: DeepSeekClient,
        config: Config | None = None,
        *,
        root: str | Path = ".",
        registry: ToolRegistry | None = None,
        verify_command: str | None = None,
        max_workspace_bytes: int = 256 * 1024 * 1024,
        **kw,
    ):
        super().__init__(client, config, **kw)
        self.root = Path(root).resolve()
        self.registry = registry or default_registry(self.root)
        self.verify_command = verify_command
        self.max_workspace_bytes = max_workspace_bytes
        self.planner = self.config.planner_model
        self.worker = self.config.worker_model

    # -- public ----------------------------------------------------------
    def run(
        self,
        task: str,
        *,
        effort: str = "high",
        candidates: int = 1,
        max_patch_repairs: int = 3,
        max_test_repairs: int = 4,
        review: bool = True,
    ) -> CodingResult:
        think = Thinking(enabled=True, effort=effort)
        self.memory.goal = task[:500]
        steps: list[dict] = []

        # 1. Explore -----------------------------------------------------
        sources = self._gather_sources(task, think)
        steps.append({"step": "explore", "files": list(sources)})

        # 2. Plan --------------------------------------------------------
        plan = self.call(
            PLAN.format(sources=self._render_sources(sources)),
            model=self.planner, thinking=think, label="code:plan",
        )
        self.distill(plan, "code:plan:distill")
        steps.append({"step": "plan", "output": plan.text})

        # 3. Implement + apply ------------------------------------------
        if candidates > 1 and self.verify_command:
            report, sel = self._implement_by_selection(think, candidates, effort)
            steps.append({"step": "candidates", "output": sel})
        else:
            report = self._implement(think, max_patch_repairs)
        steps.append({"step": "implement", "output": report.render()})
        if not report.applied:
            return CodingResult(
                output="No edits could be applied.\n" + report.render(),
                ok=False, steps=steps, memory=self.memory, patch=report,
            )

        changed = list(report.files_changed)

        # 4. Verify + repair --------------------------------------------
        verification = None
        if self.verify_command:
            verification, extra = self._verify_loop(think, max_test_repairs)
            steps.extend(extra)
            for outcome in extra:
                changed.extend(outcome.get("files", []))

        # 5. Self-review -------------------------------------------------
        if review:
            findings = self._review(changed, think)
            steps.append({"step": "review", "output": findings})
            high = [f for f in findings.get("findings", [])
                    if f.get("severity") in ("high", "medium")]
            if findings.get("verdict") == "fix" and high:
                fix_report = self._apply_response(self.call(
                    "Fix these review findings with SEARCH/REPLACE blocks:\n"
                    + "\n".join(f"- [{f['severity']}] {f.get('file','')}: {f['issue']}"
                                f" -> {f.get('fix','')}" for f in high)
                    + f"\n\n{EDIT_FORMAT_SPEC}",
                    model=self.worker, thinking=Thinking.OFF, label="code:review-fix",
                ).text)
                changed.extend(fix_report.files_changed)
                steps.append({"step": "review-fix", "output": fix_report.render()})
                if self.verify_command:  # never ship a review fix unverified
                    verification = exec_command(self.verify_command, self.root)
                    steps.append({"step": "reverify", "output": verification.render()})

        ok = verification.ok if verification is not None else report.ok
        return CodingResult(
            output=self._summarise(task, sorted(set(changed)), verification),
            ok=ok,
            steps=steps,
            memory=self.memory,
            patch=report,
            verification=verification,
            files_changed=sorted(set(changed)),
        )

    # -- stages ----------------------------------------------------------
    def _gather_sources(self, task: str, think: Thinking, k: int = 8) -> dict[str, str]:
        tree = repomap.render_for(self.root, task, max_chars=24_000)
        if not tree:  # unparseable or unsupported languages -- fall back to paths
            tree = self.registry.tools["list_files"].fn(".", 400)
        raw = self.scratch(
            EXPLORE.format(task=task, tree=tree[:30_000], k=k),
            model=self.planner, thinking=think,
            response_format={"type": "json_object"}, label="code:explore",
        )
        picked = (parse_json(raw) or {}).get("files") or []
        sources: dict[str, str] = {}
        for item in picked[:k]:
            path = item.get("path") if isinstance(item, dict) else str(item)
            if not path:
                continue
            target = self.root / path
            if target.is_file():
                sources[path] = target.read_text(encoding="utf-8", errors="replace")
                self.memory.note_artifact(path, (item.get("why") if isinstance(item, dict) else "") or "read")
        self.convo.task_context = f"TASK:\n{task}\n\nWORKSPACE: {self.root}"
        return sources

    @staticmethod
    def _render_sources(sources: dict[str, str], limit: int = 24_000) -> str:
        if not sources:
            return "(no files read -- this may be a new-file task)"
        return "\n\n".join(
            f"<file path=\"{p}\">\n{c[:limit]}\n</file>" for p, c in sources.items()
        )

    def _implement(self, think: Thinking, max_repairs: int) -> PatchReport:
        completion = self.call(
            IMPLEMENT.format(fmt=EDIT_FORMAT_SPEC),
            model=self.worker, thinking=think, label="code:implement",
        )
        report = self._apply_response(completion.text)

        for attempt in range(max_repairs):
            if report.ok or not report.failed:
                break
            for f in report.failed:
                self.memory.add_dead_end(f"edit block failed on {f.edit.path}: {f.detail[:200]}")
            completion = self.call(
                REPAIR_PATCH.format(report=report.render()),
                model=self.worker, thinking=Thinking.OFF,
                label=f"code:repair-patch:{attempt + 1}",
            )
            retry = self._apply_response(completion.text)
            report.applied.extend(retry.applied)
            report.failed = retry.failed
            report.files_changed = sorted(set(report.files_changed) | set(retry.files_changed))
        return report

    def _implement_by_selection(self, think: Thinking, n: int, effort: str):
        """Generate ``n`` patches independently and let the test suite choose.

        Diversity is manufactured deliberately: thinking mode ignores
        ``temperature``, so identical prompts yield near-identical patches and
        best-of-N collapses into paying N times for one answer. Each candidate
        gets a different framing and the models alternate.
        """
        base = self.convo.render(extra=IMPLEMENT.format(fmt=EDIT_FORMAT_SPEC))
        models = [self.worker, self.planner]

        def generate(i: int) -> str:
            framing = CANDIDATE_FRAMINGS[i % len(CANDIDATE_FRAMINGS)]
            history = list(base) + [M.user(f"Approach for this attempt: {framing}")]
            return self.client.complete(
                history,
                model=models[i % len(models)],
                thinking=Thinking(enabled=True, effort=effort),
                label=f"code:candidate:{i}",
            ).text

        result = selection.select(
            self.root, generate, self.verify_command, n=n,
            max_workspace_bytes=self.max_workspace_bytes,
        )
        try:
            if result.winner is None:
                for c in result.candidates:
                    self.memory.add_dead_end(f"candidate {c.index}: {c.error or 'no edits'}")
                return PatchReport(), result.render()

            selection.adopt(result.winner, self.root)
            for c in result.candidates:
                if c is not result.winner and c.applied and not c.passed:
                    self.memory.add_dead_end(
                        f"rejected patch: {c.summary()}"
                    )
            if result.any_passed:
                self.memory.add_fact(
                    f"candidate {result.winner.index} passed `{self.verify_command}` "
                    f"while {len(result.candidates) - 1} alternative(s) did not"
                )
            report = result.winner.report or PatchReport()
            for path in report.files_changed:
                self.memory.note_artifact(path, "modified by this run")
            return report, result.render()
        finally:
            selection.cleanup(result)

    def _apply_response(self, text: str) -> PatchReport:
        edits = parse_edits(text)
        if not edits:
            return PatchReport(failed=[], applied=[], files_changed=[])
        report = apply_edits(edits, self.root)
        for path in report.files_changed:
            self.memory.note_artifact(path, "modified by this run")
        return report

    def _verify_loop(self, think: Thinking, max_repairs: int):
        steps: list[dict] = []
        result = exec_command(self.verify_command or "", self.root)
        steps.append({"step": "verify", "output": result.render(), "ok": result.ok})
        if result.ok:
            self.memory.add_fact(f"verification passed: {self.verify_command}")
            return result, steps

        for attempt in range(max_repairs):
            self.memory.add_dead_end(
                f"after attempt {attempt}, `{self.verify_command}` still failed: "
                f"{(result.stderr or result.stdout)[-400:]}"
            )
            dead = self.memory.render(4000)
            completion = self.call(
                REPAIR_TEST.format(output=result.render(), dead_ends=dead),
                model=self.planner if attempt >= 1 else self.worker,
                thinking=think, label=f"code:repair-test:{attempt + 1}",
            )
            report = self._apply_response(completion.text)
            steps.append({"step": f"repair-{attempt + 1}", "output": report.render(),
                          "files": report.files_changed})
            if not report.applied:
                break
            result = exec_command(self.verify_command or "", self.root)
            steps.append({"step": f"verify-{attempt + 1}", "output": result.render(),
                          "ok": result.ok})
            if result.ok:
                self.memory.add_fact(f"verification passed after {attempt + 1} repair(s)")
                break
        return result, steps

    def _review(self, changed: list[str], think: Thinking) -> dict:
        diff = exec_command("git diff -- " + " ".join(f"'{c}'" for c in changed)
                            if changed else "git diff", self.root)
        body = diff.stdout.strip()
        if not body:
            body = "\n\n".join(
                f"<file path=\"{c}\">\n"
                f"{(self.root / c).read_text(encoding='utf-8', errors='replace')[:12_000]}\n</file>"
                for c in changed if (self.root / c).is_file()
            )
        if not body.strip():
            return {"verdict": "ship", "findings": []}
        raw = self.scratch(
            REVIEW.format(diff=body[:120_000]),
            model=self.planner, thinking=think,
            response_format={"type": "json_object"}, label="code:review",
        )
        return parse_json(raw) or {"verdict": "ship", "findings": []}

    def _summarise(self, task: str, changed: list[str], verification) -> str:
        lines = [f"Task: {task[:200]}", ""]
        lines.append(f"Files changed ({len(changed)}): {', '.join(changed) or 'none'}")
        if verification is not None:
            state = "PASSED" if verification.ok else "FAILED"
            lines.append(f"Verification `{self.verify_command}`: {state}")
            if not verification.ok:
                lines.append((verification.stderr or verification.stdout)[-1500:])
        return "\n".join(lines)
