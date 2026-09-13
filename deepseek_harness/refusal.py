"""Refusal detection and bounded re-request.

A spurious refusal mid-loop kills an entire agent run, and models over-refuse
constantly on legitimate work -- security tooling, parsers for hostile input,
deletion paths, anything that merely *sounds* dangerous.  Worse, a model that
simply lacks a file will often phrase it as "I can't help with that", which is
not a refusal at all.  So retrying is the right default behaviour.

The retry strategies here are all forms of *supplying information*: restating
the operator's real context, making the ask concrete, decomposing it, or
switching to a different model.  None of them apply escalating pressure,
inject a persona, or forge an assistant prefix, because those do not fix a
correct refusal -- they only launder it, and they degrade output quality on
the far more common spurious case anyway.

Attempts are bounded and every attempt is logged.  When the budget is spent the
refusal is handed to the caller with the full trail, rather than looped on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Sequence

from . import messages as M
from .client import Completion, DeepSeekClient, Thinking
from .config import Config
from .errors import RefusalUnresolved


class RefusalKind(str, Enum):
    NONE = "none"
    CAPABILITY = "capability"   # "I don't have access to that file"
    SPURIOUS = "spurious"       # benign task read as harmful
    PRINCIPLED = "principled"   # a refusal the model means
    UNKNOWN = "unknown"


# Deliberately narrow: these fire only on refusal-shaped openers, not on any
# sentence containing "cannot".
_REFUSAL_PATTERNS = [
    r"\b(i'?m sorry,? but i (can'?t|cannot|won'?t))",
    r"\b(i (can'?t|cannot|won'?t) (help|assist|provide|comply|do that|create|generate))",
    r"\b(i'?m (not able|unable) to (help|assist|provide|comply))",
    r"\b(as an ai\b.{0,40}(can'?t|cannot|unable))",
    r"\b(that (request|task) (is|would be) (something i can'?t|outside))",
    r"\b(i must (decline|refuse))",
    r"(죄송(하지만|합니다)|도와드릴 수 없|답변(을)? 드릴 수 없|제공할 수 없|할 수 없습니다)",
    r"(요청(을|은)? 거절|수행할 수 없)",
    r"(抱歉[，,]?\s*我(不能|无法))|(我不能(帮|提供|协助))|(无法(满足|完成|提供))",
]
_CAPABILITY_PATTERNS = [
    r"\b(i (don'?t|do not) have (access|the ability|permission|enough (context|information)))",
    r"\b(no such file|file (not found|does not exist)|i can'?t see (the|any) (file|code))",
    r"\b(could you (share|provide|paste)|please provide (the|more))",
    r"(파일(을)? 찾을 수 없|접근할 수 없|정보가 부족|컨텍스트가 부족)",
]
_COMPILED = [re.compile(p, re.I) for p in _REFUSAL_PATTERNS]
_COMPILED_CAP = [re.compile(p, re.I) for p in _CAPABILITY_PATTERNS]

# Short replies weigh more: a 3000-word answer containing "I can't help with
# the last part" is an answer, not a refusal.
_SHORT_REPLY_CHARS = 900


@dataclass
class Attempt:
    strategy: str
    prompt: str
    response: str
    kind: RefusalKind
    model: str = ""


@dataclass
class RefusalResult:
    completion: Completion
    resolved: bool
    attempts: list[Attempt] = field(default_factory=list)
    final_kind: RefusalKind = RefusalKind.NONE

    @property
    def text(self) -> str:
        return self.completion.text


def classify(text: str) -> RefusalKind:
    """Heuristic first pass over a response."""
    if not text or not text.strip():
        return RefusalKind.UNKNOWN
    head = text.strip()[:600]
    hit = any(p.search(head) for p in _COMPILED)
    cap = any(p.search(head) for p in _COMPILED_CAP)
    if cap and not hit:
        return RefusalKind.CAPABILITY
    if not hit:
        return RefusalKind.NONE
    if cap:
        return RefusalKind.CAPABILITY
    # A refusal opener on a short reply is a real refusal; on a long reply it is
    # usually a caveat inside real work.
    return RefusalKind.SPURIOUS if len(text.strip()) <= _SHORT_REPLY_CHARS else RefusalKind.NONE


CLASSIFY_PROMPT = """\
Classify the ASSISTANT RESPONSE to the given REQUEST. Answer with exactly one word:

- none        : it attempted the task (even partially, even with caveats)
- capability  : it declined for lack of access, context, files, or ability
- spurious    : it declined on safety grounds, but the request is ordinary \
technical work that was misread
- principled  : it declined on safety grounds and the request genuinely seeks \
harm

REQUEST:
{request}

ASSISTANT RESPONSE:
{response}

One word:"""


class RefusalHandler:
    """Bounded, logged re-request around a completion callable.

    ``purpose_context`` is operator-supplied text describing the real, legitimate
    context of the work (engagement scope, project, authorisation).  It is
    attached on retry so the model can re-evaluate with facts it did not have.
    It is asserted by you, the operator -- the harness does not invent it.
    """

    def __init__(
        self,
        client: DeepSeekClient,
        config: Config | None = None,
        *,
        purpose_context: str | None = None,
        use_classifier: bool = True,
        on_escalate: Callable[[RefusalResult], None] | None = None,
    ):
        self.client = client
        self.config = config or client.config
        self.purpose_context = purpose_context
        self.use_classifier = use_classifier
        self.on_escalate = on_escalate

    # -- public ----------------------------------------------------------
    def complete(self, history: Sequence[M.Msg], **kw) -> RefusalResult:
        """Call the model, re-requesting on refusal up to the configured bound."""
        history = list(history)
        request_text = self._last_user_text(history)
        completion = self.client.complete(history, **kw)
        kind = self._classify(request_text, completion.text)

        if kind is RefusalKind.NONE:
            return RefusalResult(completion, True, [], kind)

        attempts: list[Attempt] = [
            Attempt("initial", request_text, completion.text, kind, completion.model)
        ]
        strategies = self._strategies(kind)

        for name, build in strategies[: self.config.refusal_max_attempts]:
            nudge = build(request_text, completion.text)
            retry_kw = dict(kw)
            if name == "switch_model":
                retry_kw["model"] = self._other_model(kw.get("model"))
            retry_kw["label"] = f"refusal:{name}"

            completion = self.client.complete(list(history) + [M.user(nudge)], **retry_kw)
            kind = self._classify(request_text, completion.text)
            attempts.append(Attempt(name, nudge, completion.text, kind, completion.model))

            if kind is RefusalKind.NONE:
                return RefusalResult(completion, True, attempts, kind)
            if kind is RefusalKind.PRINCIPLED:
                break  # do not grind on a refusal the model means

        result = RefusalResult(completion, False, attempts, kind)
        if self.on_escalate:
            self.on_escalate(result)
        if self.config.refusal_escalate:
            raise RefusalUnresolved(
                f"model did not proceed after {len(attempts)} attempt(s) "
                f"(final classification: {kind.value}). Last response:\n"
                f"{completion.text[:800]}",
                attempts,
            )
        return result

    # -- strategies ------------------------------------------------------
    def _strategies(self, kind: RefusalKind):
        ordered = [
            ("clarify", self._clarify),
            ("decompose", self._decompose),
            ("switch_model", self._switch),
        ]
        if kind is RefusalKind.CAPABILITY:
            # Missing context is the likely cause; ask concretely first.
            ordered.insert(0, ("specify_missing", self._specify_missing))
        return ordered

    def _clarify(self, request: str, response: str) -> str:
        ctx = f"\n\nOperator-provided context:\n{self.purpose_context}" if self.purpose_context else ""
        return (
            "That response did not carry out the task. To be explicit about what "
            "is being asked: this is ordinary software engineering work on code "
            "the operator controls, and the output is read by the operator alone."
            f"{ctx}\n\n"
            "If part of the request was ambiguous, state the narrowest reasonable "
            "reading and proceed under it. If something genuinely cannot be done, "
            "say specifically which part and why, then do every other part in full."
        )

    def _decompose(self, request: str, response: str) -> str:
        return (
            "Let's take it in pieces. List the concrete sub-steps this task breaks "
            "into, then carry out every sub-step you can, in order. For any step "
            "you skip, name it and give the reason in one line. Do not return an "
            "empty answer: partial completed work is more useful than none."
        )

    def _specify_missing(self, request: str, response: str) -> str:
        return (
            "You indicated something was missing. Name exactly what you need "
            "(file paths, signatures, values) as a short list. Then, assuming the "
            "most standard version of each, produce the best answer you can now "
            "and mark the assumptions inline."
        )

    def _switch(self, request: str, response: str) -> str:
        return (
            "Restating the task plainly, answer it directly and completely:\n\n"
            f"{request}"
        )

    # -- helpers ---------------------------------------------------------
    def _other_model(self, current: str | None) -> str:
        current = current or self.config.worker_model
        return (
            self.config.planner_model
            if current == self.config.worker_model
            else self.config.worker_model
        )

    def _classify(self, request: str, response: str) -> RefusalKind:
        kind = classify(response)
        if kind in (RefusalKind.NONE, RefusalKind.CAPABILITY) or not self.use_classifier:
            return kind
        try:
            verdict = self.client.complete(
                [M.user(CLASSIFY_PROMPT.format(request=request[:4000], response=response[:4000]))],
                model=self.config.utility_model,
                thinking=Thinking.OFF,
                max_tokens=8,
                label="refusal:classify",
            ).text.strip().lower()
        except Exception:
            return kind
        for candidate in (RefusalKind.NONE, RefusalKind.CAPABILITY,
                          RefusalKind.SPURIOUS, RefusalKind.PRINCIPLED):
            if candidate.value in verdict:
                return candidate
        return kind

    @staticmethod
    def _last_user_text(history: Sequence[M.Msg]) -> str:
        for m in reversed(list(history)):
            if m.role == "user" and m.content:
                return m.content
        return ""
