"""Conversation assembly and compaction.

Two ideas do most of the work here.

**Cache-optimal ordering.**  DeepSeek's automatic context cache is a *prefix*
cache and hits are ~50x cheaper than misses, so the layout runs from most
stable to most volatile::

    system -> task context -> digest -> append-only transcript -> working memory

Working memory changes every turn, so it goes last and is never persisted into
the transcript; otherwise each turn would invalidate the prefix and bury stale
snapshots in history.

**Compact, never truncate.**  Dropping the oldest turns silently deletes the
decisions and dead ends that keep a long run from looping.  Instead the middle
of the transcript is folded into a digest that is explicitly required to carry
them forward, and the digest is emitted with ``user`` role so it is not subject
to the thinking-mode reasoning round-trip rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import messages as M
from .client import DeepSeekClient, Thinking
from .config import Config, spec_for
from .memory import WorkingMemory

COMPACT_PROMPT = """\
Compact the conversation below into a dense digest for an agent that will keep \
working on this task with no other access to these turns.

You MUST preserve, in this order:
1. DECISIONS taken and why (so they are not re-argued).
2. DEAD ENDS: what was tried and failed, and the failure mode.
3. VERIFIED FACTS: file paths, signatures, commands, test results, numbers.
4. CURRENT STATE: what is done, what is in progress, what is next.
5. UNRESOLVED questions or risks.

You MUST drop: pleasantries, restatements of the task, narration, superseded \
intermediate reasoning whose conclusion you already captured above.

Be specific over brief -- a path or an error string is worth more than an \
adjective. Output the digest only.

<conversation>
{body}
</conversation>
"""


@dataclass
class Conversation:
    """Layered history with compaction and cache-aware rendering."""

    config: Config
    system_prompt: str = ""
    task_context: str = ""
    memory: WorkingMemory = field(default_factory=WorkingMemory)
    transcript: list[M.Msg] = field(default_factory=list)
    digest: str = ""
    compactions: int = 0

    # -- mutation --------------------------------------------------------
    def add(self, msg: M.Msg) -> None:
        self.transcript.append(msg)
        if msg.role == "assistant":
            self.memory.turn += 1

    def add_user(self, text: str) -> None:
        self.add(M.user(text))

    def add_assistant(self, completion_or_msg) -> M.Msg:
        msg = getattr(completion_or_msg, "message", completion_or_msg)
        self.add(msg)
        return msg

    def add_tool_result(self, call_id: str, content: str, name: str | None = None) -> None:
        self.add(M.tool_result(call_id, content, name))

    # -- rendering -------------------------------------------------------
    def render(self, *, extra: str = "") -> list[M.Msg]:
        out: list[M.Msg] = []
        if self.system_prompt:
            out.append(M.system(self.system_prompt))
        if self.task_context:
            out.append(M.user(f"<task_context>\n{self.task_context}\n</task_context>"))
        if self.digest:
            out.append(M.user(f"<prior_context_digest>\n{self.digest}\n</prior_context_digest>"))
        out.extend(self.transcript)

        # Volatile tail -- never persisted, so the prefix above stays cacheable.
        tail_parts = [p for p in (self.memory.render(self.config.max_working_memory_chars),
                                  extra) if p]
        if tail_parts:
            out.append(M.user("\n\n".join(tail_parts), ephemeral=True))
        return out

    def approx_tokens(self) -> int:
        return M.approx_tokens(self.render())

    # -- compaction ------------------------------------------------------
    def should_compact(self, model: str) -> bool:
        window = spec_for(model).context_window
        return self.approx_tokens() > window * self.config.compact_at_fraction

    def maybe_compact(self, client: DeepSeekClient, model: str) -> bool:
        if not self.should_compact(model):
            return False
        self.compact(client)
        return True

    def compact(self, client: DeepSeekClient) -> None:
        """Fold everything but the live tail into the digest."""
        keep = max(self.config.keep_live_turns, 2)
        if len(self.transcript) <= keep:
            return

        head, tail = self.transcript[:-keep], self.transcript[-keep:]
        # Never split a tool call from its result.
        while tail and tail[0].role == "tool":
            head.append(tail.pop(0))
        if not head:
            return

        body = "\n\n".join(self._flatten(m) for m in head if self._flatten(m))
        summary = client.complete(
            [M.user(COMPACT_PROMPT.format(body=body[-180_000:]))],
            model=self.config.utility_model,
            thinking=Thinking.OFF,
            label="compact",
        ).text.strip()

        self.digest = (
            f"{self.digest}\n\n--- compaction #{self.compactions + 1} ---\n{summary}"
            if self.digest else summary
        ).strip()
        self.transcript = tail
        self.compactions += 1

    @staticmethod
    def _flatten(m: M.Msg) -> str:
        """Render one turn for the compactor, including tool traffic."""
        if m.role == "tool":
            return f"[tool result {m.name or ''}]\n{(m.content or '')[:4000]}"
        parts = [f"[{m.role}]"]
        if m.reasoning_content:
            parts.append(f"(reasoning, will be discarded)\n{m.reasoning_content[:6000]}")
        if m.content:
            parts.append(m.content[:12000])
        for tc in m.tool_calls:
            parts.append(f"[tool call] {tc.name}({tc.arguments[:1500]})")
        return "\n".join(parts)
