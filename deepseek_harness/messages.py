"""Message types with correct DeepSeek V4 thinking-mode semantics.

The rule most clients get wrong: when thinking mode is enabled, DeepSeek
requires the assistant turn's ``reasoning_content`` to be replayed *verbatim*
in the next request.  Omitting it, or sending an empty string, is a 400.

Consequences this module encodes:

* ``reasoning_content`` is a first-class field on :class:`Msg`, never dropped
  on serialisation of a live assistant turn.
* History we can no longer replay faithfully (because compaction discarded the
  chain of thought) is never re-emitted as an ``assistant`` turn.  It is folded
  into a ``user``-role digest block, which carries no such requirement.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string, kept verbatim for faithful replay

    def parsed(self) -> dict:
        try:
            return json.loads(self.arguments or "{}")
        except json.JSONDecodeError:
            return {}

    def to_api(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }

    @classmethod
    def from_api(cls, raw: dict) -> "ToolCall":
        fn = raw.get("function") or {}
        return cls(
            id=raw.get("id") or "",
            name=fn.get("name") or "",
            arguments=fn.get("arguments") or "{}",
        )


@dataclass
class Msg:
    role: Role
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    # Harness bookkeeping, never sent to the API.
    ephemeral: bool = False       # re-injected fresh each turn, not persisted
    reasoning_lost: bool = False  # CoT dropped; unsafe to replay as assistant

    def to_api(self, *, thinking: bool) -> dict:
        out: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            out["content"] = self.content
        if self.name:
            out["name"] = self.name
        if self.role == "tool" and self.tool_call_id:
            out["tool_call_id"] = self.tool_call_id
        if self.role == "assistant":
            if self.tool_calls:
                out["tool_calls"] = [tc.to_api() for tc in self.tool_calls]
            # Round-trip the CoT verbatim. Only when we actually hold it --
            # an empty string is rejected just like a missing field.
            if thinking and self.reasoning_content:
                out["reasoning_content"] = self.reasoning_content
            if out.get("content") is None and not self.tool_calls:
                out["content"] = ""
        return out

    @classmethod
    def from_api(cls, raw: dict) -> "Msg":
        return cls(
            role=raw.get("role") or "assistant",
            content=raw.get("content"),
            reasoning_content=raw.get("reasoning_content") or None,
            tool_calls=[ToolCall.from_api(t) for t in (raw.get("tool_calls") or [])],
        )

    @property
    def replayable_as_assistant(self) -> bool:
        """False when replaying this turn would trip the round-trip rule."""
        return not self.reasoning_lost

    def text(self) -> str:
        return self.content or ""


def system(content: str) -> Msg:
    return Msg("system", content)


def user(content: str, *, ephemeral: bool = False) -> Msg:
    return Msg("user", content, ephemeral=ephemeral)


def assistant(content: str, reasoning: str | None = None) -> Msg:
    return Msg("assistant", content, reasoning_content=reasoning)


def tool_result(call_id: str, content: str, name: str | None = None) -> Msg:
    return Msg("tool", content, tool_call_id=call_id, name=name)


def render(messages: Iterable[Msg], *, thinking: bool) -> list[dict]:
    """Serialise a history for the API, degrading unreplayable turns safely.

    An assistant turn whose chain of thought was dropped during compaction is
    downgraded to a user-role digest rather than being sent without its
    ``reasoning_content``.
    """
    out: list[dict] = []
    for m in messages:
        if thinking and m.role == "assistant" and not m.replayable_as_assistant:
            body = m.text().strip()
            if body:
                out.append({"role": "user", "content": f"[earlier assistant turn]\n{body}"})
            continue
        out.append(m.to_api(thinking=thinking))
    return out


def approx_tokens(messages: Iterable[Msg]) -> int:
    """Cheap upper-ish bound on prompt size.

    Deliberately not a real tokeniser: this only has to decide *when* to
    compact, and every real count comes back from the API afterwards.  ~3.2
    chars/token is a safe blend of English, Korean and code.
    """
    chars = 0
    for m in messages:
        chars += len(m.content or "") + len(m.reasoning_content or "")
        for tc in m.tool_calls:
            chars += len(tc.arguments) + len(tc.name) + 16
        chars += 8
    return int(chars / 3.2) + 1
