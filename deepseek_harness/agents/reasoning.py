"""Reasoning agent: decompose -> hypothesise -> solve -> verify -> synthesise.

The pipeline exists because a single thinking-mode call, however long, has one
structural weakness: it commits to a framing early and never audits it.  Each
stage here is a place where that framing can be rejected.

* **Frame** separates what is asked from what is assumed, so the answer is not
  quietly to a different question.
* **Hypotheses** forces at least two candidate answers and asks what evidence
  would *discriminate* between them -- the step that kills confirmation bias.
* **Verify** substitutes the answer back into the original constraints.  A
  verifier that shares the solver's context inherits its blind spots, so it is
  run on a clean context holding only the problem and the answer.
* **Best-of-N** is opt-in because it multiplies cost; it pays on problems with
  a checkable answer and is near-useless on open-ended ones.
"""
from __future__ import annotations

import concurrent.futures as cf
from dataclasses import dataclass

from .. import messages as M
from ..client import DeepSeekClient, Thinking
from ..config import Config
from .base import Agent, AgentResult, parse_json

SYSTEM = """\
You are a rigorous analytical engine. You are precise, you separate what you \
verified from what you assumed, and you say "I don't know" rather than \
producing a plausible-sounding guess.

Standing rules:
- State the conclusion first, then the reasoning that supports it.
- Never assert a number, date, identifier or API signature you have not \
checked. Mark it as an assumption instead.
- When you notice you were wrong mid-way, say so in one line and change course. \
Do not defend a dead position.
"""

FRAME = """\
Before solving, frame the problem.

{problem}

Answer these four, briefly:
1. RESTATE: what exactly is being asked, in one sentence?
2. GIVENS: what is stated as fact?
3. UNKNOWNS: what would you need to know that you have not been told? For each, \
say whether it is derivable from the givens or must be assumed.
4. SUCCESS TEST: how would you check a candidate answer is right?

Do not solve yet."""

HYPOTHESES = """\
Generate at least two DISTINCT candidate answers or approaches. They must \
genuinely differ -- two phrasings of the same idea do not count.

For each: state it, then state what observation would REFUTE it.

Then identify the single piece of evidence that best discriminates between \
them, and evaluate it. Eliminate what you can."""

SOLVE = """\
Now solve it. Carry the surviving hypothesis through to a concrete answer.

Show the decisive steps -- not every step, the ones the answer depends on. \
If you rely on an assumption, mark it inline as [assumption: ...]."""

VERIFY = """\
Verify this answer independently. You have only the problem and the proposed \
answer; do not assume the reasoning behind it was sound.

<problem>
{problem}
</problem>

<proposed_answer>
{answer}
</proposed_answer>

Do all of:
1. Substitute the answer back into the problem's constraints. Does every one hold?
2. Try to construct a counterexample, or an edge case where it breaks (empty, \
zero, negative, maximum, duplicate, unicode, concurrent).
3. Check any arithmetic, dates or unit conversions by recomputing them.
4. Name the single sentence in the answer most likely to be wrong.

Reply with JSON only:
{{"verdict": "correct" | "wrong" | "incomplete" | "unverifiable",
  "problems": ["..."],
  "weakest_claim": "...",
  "corrected_answer": "... (only if verdict is wrong or incomplete, else empty)"}}"""

SYNTHESIS = """\
Produce the final answer for the user, incorporating the verification findings.

Requirements:
- Conclusion in the first sentence.
- Then the reasoning, only as much as carries the conclusion.
- Mark assumptions explicitly, and say what would change the answer.
- If verification found the answer wrong or incomplete, give the corrected one \
without narrating the correction process.
- No preamble, no restating the question back."""

JUDGE = """\
You are comparing independent candidate answers to one problem.

<problem>
{problem}
</problem>

{candidates}

Pick the best one. Agreement between candidates is weak evidence; a candidate \
that verifies against the problem's constraints beats a popular one. If the best \
answer combines parts of several, say so.

Reply with JSON only:
{{"best_index": <int, 0-based>, "why": "...", "merge_note": ""}}"""


@dataclass
class ReasoningTrace:
    frame: str = ""
    hypotheses: str = ""
    draft: str = ""
    verdict: str = ""
    problems: list[str] = None  # type: ignore[assignment]
    final: str = ""


class ReasoningAgent(Agent):
    """Maximum-strength reasoning pipeline for DeepSeek thinking mode."""

    system_prompt = SYSTEM

    def __init__(self, client: DeepSeekClient, config: Config | None = None, **kw):
        super().__init__(client, config, **kw)
        self.model = self.config.planner_model

    # -- public ----------------------------------------------------------
    def solve(
        self,
        problem: str,
        *,
        effort: str = "high",
        frame: bool = True,
        hypotheses: bool = True,
        verify: bool = True,
        samples: int = 1,
    ) -> AgentResult:
        """Run the pipeline.  ``samples > 1`` enables best-of-N with a judge."""
        if samples > 1:
            return self._best_of_n(problem, samples, effort=effort, verify=verify)

        think = Thinking(enabled=True, effort=effort)
        self.convo.memory.goal = problem[:500]
        trace = ReasoningTrace(problems=[])
        steps: list[dict] = []

        if frame:
            c = self.call(FRAME.format(problem=problem), model=self.model,
                          thinking=think, label="reason:frame")
            trace.frame = c.text
            self.distill(c, "reason:frame:distill")
            steps.append({"step": "frame", "output": c.text})
        else:
            self.convo.add_user(problem)

        if hypotheses:
            c = self.call(HYPOTHESES, model=self.model, thinking=think,
                          label="reason:hypotheses")
            trace.hypotheses = c.text
            self.distill(c, "reason:hyp:distill")
            steps.append({"step": "hypotheses", "output": c.text})

        c = self.call(SOLVE if (frame or hypotheses) else problem, model=self.model,
                      thinking=think, label="reason:solve")
        trace.draft = c.text
        self.distill(c, "reason:solve:distill")
        steps.append({"step": "solve", "output": c.text})

        if verify:
            check = self._verify(problem, trace.draft, effort=effort)
            trace.verdict = check.get("verdict", "unverifiable")
            trace.problems = list(check.get("problems") or [])
            steps.append({"step": "verify", "output": check})
            for p in trace.problems:
                self.memory.add_fact(f"verification finding: {p}")
            if trace.verdict in ("wrong", "incomplete"):
                corrected = (check.get("corrected_answer") or "").strip()
                extra = (f"\n\nVerification says the answer is {trace.verdict}. "
                         f"Problems found:\n- " + "\n- ".join(trace.problems)
                         + (f"\n\nSuggested correction:\n{corrected}" if corrected else ""))
                c = self.call(SYNTHESIS + extra, model=self.model, thinking=think,
                              label="reason:repair")
                trace.final = c.text
                steps.append({"step": "repair", "output": c.text})

        if not trace.final:
            c = self.call(SYNTHESIS, model=self.model, thinking=think, label="reason:final")
            trace.final = c.text
            steps.append({"step": "synthesis", "output": c.text})

        return AgentResult(
            output=trace.final,
            ok=trace.verdict != "wrong",
            steps=steps,
            memory=self.memory,
            note=f"verdict={trace.verdict or 'skipped'}",
        )

    # -- internals -------------------------------------------------------
    def _verify(self, problem: str, answer: str, *, effort: str) -> dict:
        """Verify on a clean context so the solver's blind spots are not inherited."""
        raw = self.client.complete(
            [M.user(VERIFY.format(problem=problem, answer=answer))],
            model=self.model,
            thinking=Thinking(enabled=True, effort=effort),
            response_format={"type": "json_object"},
            label="reason:verify",
        ).text
        return parse_json(raw) or {"verdict": "unverifiable", "problems": []}

    def _best_of_n(self, problem: str, n: int, *, effort: str, verify: bool) -> AgentResult:
        """Independent samples, then a judge.  Cost scales linearly with ``n``."""
        def one(_i: int) -> AgentResult:
            agent = ReasoningAgent(self.client, self.config)
            return agent.solve(problem, effort=effort, verify=verify, samples=1)

        with cf.ThreadPoolExecutor(max_workers=min(n, 5)) as pool:
            results = list(pool.map(one, range(n)))

        blocks = "\n\n".join(
            f"<candidate index=\"{i}\">\n{r.output}\n</candidate>"
            for i, r in enumerate(results)
        )
        raw = self.scratch(
            JUDGE.format(problem=problem, candidates=blocks),
            model=self.model,
            thinking=Thinking(enabled=True, effort=effort),
            response_format={"type": "json_object"},
            label="reason:judge",
        )
        verdict = parse_json(raw) or {}
        idx = verdict.get("best_index", 0)
        idx = idx if isinstance(idx, int) and 0 <= idx < len(results) else 0
        best = results[idx]
        return AgentResult(
            output=best.output,
            ok=best.ok,
            steps=[{"step": "best_of_n", "n": n, "chosen": idx,
                    "why": verdict.get("why", ""),
                    "candidates": [r.output for r in results]}],
            memory=best.memory,
            note=f"best-of-{n}, chose #{idx}",
        )
