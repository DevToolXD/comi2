"""The DeepSeek chat client: parameter negotiation, thinking mode, telemetry."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Sequence

from . import messages as M
from .config import Config, MODEL_ID_FALLBACKS, spec_for
from .errors import APIError, ReasoningRoundtripError
from .telemetry import Telemetry, Usage
from .transport import Transport


@dataclass
class Thinking:
    """Thinking-mode request options.

    DeepSeek V4 accepts an OpenAI-style ``reasoning_effort`` and an
    Anthropic-style ``thinking`` object depending on the surface.  We prefer
    the former and fall back once if the server rejects it.
    """

    enabled: bool = True
    effort: str | None = "high"  # "low" | "medium" | "high" | "max"

    #: Shared "thinking disabled" instance; assigned below the class body so it
    #: stays a class attribute rather than becoming a third field.
    OFF: ClassVar["Thinking"]


Thinking.OFF = Thinking(enabled=False, effort=None)


@dataclass
class Completion:
    message: M.Msg
    usage: Usage
    model: str
    finish_reason: str = ""
    latency: float = 0.0
    raw: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.message.text()

    @property
    def reasoning(self) -> str:
        return self.message.reasoning_content or ""

    @property
    def tool_calls(self) -> list[M.ToolCall]:
        return self.message.tool_calls


class DeepSeekClient:
    """Thin, correct wrapper over ``/chat/completions``.

    Correctness here means three things other clients routinely miss:

    1. ``reasoning_content`` is replayed verbatim on assistant turns whenever
       thinking mode is on, and turns that cannot be replayed are downgraded
       rather than sent malformed.
    2. Unsupported parameters are stripped per model instead of 400-ing.
    3. Unknown model IDs are probed against known aliases once, so a renamed
       model degrades to a warning instead of a hard failure.
    """

    def __init__(self, config: Config | None = None, telemetry: Telemetry | None = None):
        self.config = config or Config()
        self.transport = Transport(self.config)
        self.telemetry = telemetry or Telemetry(self.config)
        self._effort_param: dict[str, str] = {}   # model -> "reasoning_effort" | "thinking"
        self._resolved_model: dict[str, str] = {}

    # -- public ----------------------------------------------------------
    def complete(
        self,
        history: Sequence[M.Msg],
        *,
        model: str | None = None,
        thinking: Thinking | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        stop: list[str] | None = None,
        label: str = "",
    ) -> Completion:
        model = self._resolve(model or self.config.worker_model)
        spec = spec_for(model)
        think = thinking or Thinking()
        use_thinking = bool(think.enabled and spec.supports_thinking)

        payload = self._build_payload(
            history, model, spec, think, use_thinking, tools, tool_choice,
            temperature, max_tokens, response_format, stop,
        )

        started = time.monotonic()
        try:
            raw = self.transport.post_json("/chat/completions", payload, label=label)
        except ReasoningRoundtripError:
            # Repair: mark every assistant turn lacking CoT as unreplayable and
            # re-render, which downgrades them to user-role digests.
            repaired = self._repair_history(history)
            payload["messages"] = M.render(repaired, thinking=use_thinking)
            raw = self.transport.post_json("/chat/completions", payload, label=label + "+repair")
        except APIError as exc:
            retry = self._maybe_renegotiate(exc, model, payload, use_thinking, think)
            if retry is None:
                alias = self._probe_alias(model, exc)
                if alias is None:
                    raise
                model, retry = alias, {**payload, "model": alias}
            raw = self.transport.post_json("/chat/completions", retry, label=label + "+renegotiate")

        latency = time.monotonic() - started
        choice = (raw.get("choices") or [{}])[0]
        msg = M.Msg.from_api(choice.get("message") or {})
        usage = Usage.from_api(raw.get("usage"))
        self.telemetry.record(model, usage, latency, label=label or "complete")

        return Completion(
            message=msg,
            usage=usage,
            model=model,
            finish_reason=choice.get("finish_reason") or "",
            latency=latency,
            raw=raw,
        )

    def ask(self, prompt: str, *, system: str | None = None, **kw) -> str:
        """One-shot convenience call; returns text only."""
        history = ([M.system(system)] if system else []) + [M.user(prompt)]
        return self.complete(history, **kw).text

    def close(self) -> None:
        self.transport.close()

    # -- internals -------------------------------------------------------
    def _build_payload(self, history, model, spec, think, use_thinking, tools,
                       tool_choice, temperature, max_tokens, response_format, stop) -> dict:
        payload: dict[str, Any] = {
            "model": model,
            "messages": M.render(history, thinking=use_thinking),
            "stream": False,
        }

        if use_thinking and think.effort:
            if self._effort_param.get(model) == "thinking":
                payload["thinking"] = {"type": "enabled"}
            else:
                payload["reasoning_effort"] = think.effort
        elif use_thinking:
            payload["thinking"] = {"type": "enabled"}
        elif spec.supports_thinking:
            payload["thinking"] = {"type": "disabled"}

        if tools and spec.supports_tools:
            payload["tools"] = tools
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
        if response_format is not None and spec.supports_json:
            payload["response_format"] = response_format
        if stop:
            payload["stop"] = stop
        if max_tokens:
            payload["max_tokens"] = min(max_tokens, spec.max_output)
        # Sampling params are ignored (or rejected) while thinking is active.
        if temperature is not None and not use_thinking:
            payload["temperature"] = temperature
        return payload

    @staticmethod
    def _repair_history(history: Sequence[M.Msg]) -> list[M.Msg]:
        repaired: list[M.Msg] = []
        for m in history:
            if m.role == "assistant" and not m.reasoning_content and not m.reasoning_lost:
                m = M.Msg(**{**m.__dict__, "reasoning_lost": True})
            repaired.append(m)
        return repaired

    def _maybe_renegotiate(self, exc: APIError, model: str, payload: dict,
                           use_thinking: bool, think: Thinking) -> dict | None:
        """Swap ``reasoning_effort`` for ``thinking`` once if the server refuses it."""
        body = (exc.body or "").lower()
        if not use_thinking or "reasoning_effort" not in body:
            return None
        if self._effort_param.get(model) == "thinking":
            return None
        self._effort_param[model] = "thinking"
        retry = dict(payload)
        retry.pop("reasoning_effort", None)
        retry["thinking"] = {"type": "enabled"}
        return retry

    def _resolve(self, model: str) -> str:
        """Return the ID to call: a previously discovered alias, or the model itself."""
        return self._resolved_model.get(model, model)

    def _probe_alias(self, model: str, exc: APIError) -> str | None:
        """Find a working alias after the server rejected ``model``.

        Model IDs get renamed between releases -- ``deepseek-chat`` and
        ``deepseek-reasoner`` were retired after 2026-07-24 -- so a rename should
        cost one probe, not a failed run.  Only runs when the server actually
        rejected the ID, and the result is cached for the client's lifetime.
        """
        if not self._is_unknown_model(exc):
            return None
        for cand in MODEL_ID_FALLBACKS.get(model, ()):
            if cand == model:
                continue
            try:
                self.transport.post_json(
                    "/chat/completions",
                    {"model": cand, "messages": M.render([M.user("ping")], thinking=False),
                     "max_tokens": 1, "stream": False},
                    label="probe",
                )
            except APIError:
                continue
            self._resolved_model[model] = cand
            return cand
        return None

    @staticmethod
    def _is_unknown_model(exc: APIError) -> bool:
        if exc.status == 404:
            return True
        body = (exc.body or "").lower()
        return "model" in body and any(
            marker in body
            for marker in ("not found", "does not exist", "unknown", "invalid model",
                           "not exist", "unsupported model", "deprecated")
        )
