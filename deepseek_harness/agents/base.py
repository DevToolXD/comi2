"""Shared agent scaffolding."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .. import messages as M
from ..client import Completion, DeepSeekClient, Thinking
from ..config import Config
from ..context import Conversation
from ..memory import DISTILL_PROMPT, UPDATE_SCHEMA, WorkingMemory
from ..refusal import RefusalHandler


@dataclass
class AgentResult:
    output: str
    ok: bool = True
    steps: list[dict] = field(default_factory=list)
    memory: WorkingMemory | None = None
    note: str = ""

    def __str__(self) -> str:
        return self.output


class Agent:
    """Base class wiring together client, conversation, memory and refusal retry."""

    system_prompt: str = ""

    def __init__(
        self,
        client: DeepSeekClient,
        config: Config | None = None,
        *,
        purpose_context: str | None = None,
    ):
        self.client = client
        self.config = config or client.config
        self.refusals = RefusalHandler(client, self.config, purpose_context=purpose_context)
        self.convo = Conversation(self.config, system_prompt=self.system_prompt)

    # -- model calls -----------------------------------------------------
    def call(self, prompt: str | None = None, *, model: str | None = None,
             thinking: Thinking | None = None, label: str = "",
             persist: bool = True, extra: str = "", **kw) -> Completion:
        """Run one turn through the conversation, with compaction and retry."""
        if prompt is not None:
            self.convo.add_user(prompt)
        model = model or self.config.worker_model
        self.convo.maybe_compact(self.client, model)

        result = self.refusals.complete(
            self.convo.render(extra=extra), model=model,
            thinking=thinking, label=label, **kw
        )
        completion = result.completion
        if persist:
            self.convo.add_assistant(completion)
        return completion

    def scratch(self, prompt: str, *, model: str | None = None,
                thinking: Thinking | None = None, label: str = "", **kw) -> str:
        """One-off call that does not touch the conversation history."""
        return self.client.complete(
            [M.user(prompt)],
            model=model or self.config.utility_model,
            thinking=thinking or Thinking.OFF,
            label=label or "scratch",
            **kw,
        ).text

    # -- memory ----------------------------------------------------------
    def distill(self, completion: Completion, label: str = "distill") -> None:
        """Fold a turn's chain of thought into durable working memory.

        DeepSeek's CoT is replayed verbatim only while it fits in the window.
        Once compaction drops it, whatever was not distilled here is gone.
        """
        source = (completion.reasoning or "") + "\n\n" + (completion.text or "")
        if len(source.strip()) < 200:
            return
        raw = self.scratch(
            DISTILL_PROMPT.format(schema=json.dumps(UPDATE_SCHEMA))
            + f"\n\n<step>\n{source[-60_000:]}\n</step>",
            label=label,
            response_format={"type": "json_object"},
        )
        update = parse_json(raw)
        if update:
            self.convo.memory.apply(update)

    @property
    def memory(self) -> WorkingMemory:
        return self.convo.memory


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)


def parse_json(text: str) -> Any:
    """Best-effort JSON extraction from a model response."""
    if not text:
        return None
    text = text.strip()
    for candidate in (text, *(m.group(1) for m in _JSON_BLOCK.finditer(text))):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    # Last resort: outermost brace span.
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None
