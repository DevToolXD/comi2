"""Durable working memory.

Even with verbatim ``reasoning_content`` round-tripping, a long run eventually
exceeds the context window and the oldest chain of thought has to go.  What
must *not* go is the conclusions it reached.  This module keeps a small,
structured, append-only record of them.

``dead_ends`` earns its place: the dominant failure mode of a long agent loop
is re-attempting something that already failed 40 turns ago.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Literal

HypothesisStatus = Literal["open", "confirmed", "refuted"]


@dataclass
class Note:
    text: str
    turn: int = 0
    source: str = ""


@dataclass
class Hypothesis:
    text: str
    status: HypothesisStatus = "open"
    evidence: str = ""
    turn: int = 0


@dataclass
class WorkingMemory:
    """Structured state that survives compaction."""

    goal: str = ""
    facts: list[Note] = field(default_factory=list)
    decisions: list[Note] = field(default_factory=list)
    dead_ends: list[Note] = field(default_factory=list)
    open_questions: list[Note] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)  # path -> why it matters
    turn: int = 0

    # -- mutation --------------------------------------------------------
    def add_fact(self, text: str, source: str = "") -> None:
        self._append(self.facts, Note(text.strip(), self.turn, source))

    def add_decision(self, text: str, source: str = "") -> None:
        self._append(self.decisions, Note(text.strip(), self.turn, source))

    def add_dead_end(self, text: str, source: str = "") -> None:
        self._append(self.dead_ends, Note(text.strip(), self.turn, source))

    def add_question(self, text: str) -> None:
        self._append(self.open_questions, Note(text.strip(), self.turn))

    def add_hypothesis(self, text: str) -> None:
        text = text.strip()
        if not any(h.text == text for h in self.hypotheses):
            self.hypotheses.append(Hypothesis(text, turn=self.turn))

    def resolve_hypothesis(self, text: str, status: HypothesisStatus, evidence: str = "") -> None:
        for h in self.hypotheses:
            if h.text.strip() == text.strip():
                h.status, h.evidence = status, evidence
                return
        self.hypotheses.append(Hypothesis(text.strip(), status, evidence, self.turn))

    def note_artifact(self, path: str, why: str) -> None:
        self.artifacts[path] = why

    def answer_question(self, text: str) -> None:
        self.open_questions = [q for q in self.open_questions if q.text.strip() != text.strip()]

    @staticmethod
    def _append(bucket: list[Note], note: Note) -> None:
        if note.text and not any(n.text == note.text for n in bucket):
            bucket.append(note)

    # -- ingestion -------------------------------------------------------
    def apply(self, update: dict) -> None:
        """Merge a model-produced update object (see :data:`UPDATE_SCHEMA`)."""
        for t in update.get("facts", []) or []:
            self.add_fact(str(t))
        for t in update.get("decisions", []) or []:
            self.add_decision(str(t))
        for t in update.get("dead_ends", []) or []:
            self.add_dead_end(str(t))
        for t in update.get("open_questions", []) or []:
            self.add_question(str(t))
        for t in update.get("answered_questions", []) or []:
            self.answer_question(str(t))
        for h in update.get("hypotheses", []) or []:
            if isinstance(h, dict):
                status = h.get("status", "open")
                if status == "open":
                    self.add_hypothesis(str(h.get("text", "")))
                else:
                    self.resolve_hypothesis(
                        str(h.get("text", "")), status, str(h.get("evidence", ""))
                    )
            else:
                self.add_hypothesis(str(h))
        for path, why in (update.get("artifacts") or {}).items():
            self.note_artifact(str(path), str(why))

    # -- rendering -------------------------------------------------------
    def render(self, max_chars: int = 12_000) -> str:
        """Render as a prompt block.  Newest entries win when truncating."""
        if self.is_empty():
            return ""
        parts: list[str] = ["<working_memory>"]
        if self.goal:
            parts.append(f"GOAL: {self.goal}")

        def sect(title: str, notes: list[Note], limit: int) -> None:
            if not notes:
                return
            parts.append(f"\n{title}:")
            for n in notes[-limit:]:
                parts.append(f"  - (t{n.turn}) {n.text}")

        sect("ESTABLISHED FACTS", self.facts, 40)
        sect("DECISIONS MADE (do not re-litigate)", self.decisions, 25)
        sect("DEAD ENDS (do not retry these)", self.dead_ends, 30)

        open_h = [h for h in self.hypotheses if h.status == "open"]
        closed_h = [h for h in self.hypotheses if h.status != "open"]
        if open_h:
            parts.append("\nOPEN HYPOTHESES:")
            parts.extend(f"  - {h.text}" for h in open_h[-15:])
        if closed_h:
            parts.append("\nSETTLED HYPOTHESES:")
            parts.extend(
                f"  - [{h.status}] {h.text}" + (f" -- {h.evidence}" if h.evidence else "")
                for h in closed_h[-15:]
            )
        if self.open_questions:
            parts.append("\nOPEN QUESTIONS:")
            parts.extend(f"  - {q.text}" for q in self.open_questions[-12:])
        if self.artifacts:
            parts.append("\nARTIFACTS:")
            parts.extend(f"  - {p}: {w}" for p, w in list(self.artifacts.items())[-25:])
        parts.append("</working_memory>")

        block = "\n".join(parts)
        if len(block) > max_chars:  # keep the tail: newest information
            block = "<working_memory>\n...[older entries elided]...\n" + block[-max_chars:]
        return block

    def is_empty(self) -> bool:
        return not any(
            [self.goal, self.facts, self.decisions, self.dead_ends,
             self.open_questions, self.hypotheses, self.artifacts]
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "dead_ends": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "answered_questions": {"type": "array", "items": {"type": "string"}},
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "status": {"enum": ["open", "confirmed", "refuted"]},
                    "evidence": {"type": "string"},
                },
                "required": ["text"],
            },
        },
        "artifacts": {"type": "object"},
    },
}

DISTILL_PROMPT = """\
You just finished a reasoning step. Its chain of thought will be DISCARDED and \
cannot be recovered. Extract only what a future turn must not lose.

Rules:
- facts: findings you verified. Not guesses.
- decisions: choices made plus the reason, so they are not re-argued later.
- dead_ends: approaches you tried that FAILED, and why. This is the most \
valuable field; be specific enough that you would not retry the same thing.
- hypotheses: mark confirmed/refuted ones with the evidence that settled them.
- Omit anything already obvious from the task statement.

Reply with JSON matching this schema and nothing else:
{schema}
"""
