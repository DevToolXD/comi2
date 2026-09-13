"""A DeepSeek V4 harness built around reasoning-state continuity.

Quick start::

    from deepseek_harness import Harness, Config

    with Harness(Config(), root=".", verify_command="pytest -q") as h:
        print(h.run("why does the retry loop drop the last error?").output)

The design premise: on multi-turn work the dominant variable is not the prompt,
it is whether the model's reasoning state survives between turns and what
happens to it when the context fills.  DeepSeek V4 makes the first half
mandatory -- thinking mode rejects a history that does not replay
``reasoning_content`` verbatim -- and leaves the second half to you.
"""
from .config import Budget, Config, FLASH, PRO, ModelSpec, RetryPolicy, is_peak
from .client import Completion, DeepSeekClient, Thinking
from .context import Conversation
from .memory import WorkingMemory
from .refusal import RefusalHandler, RefusalKind, classify
from .patch import apply_edits, parse_edits
from . import repomap, selection
from .selection import Candidate, SelectionResult
from .tools import ToolRegistry, default_registry, exec_command
from .telemetry import Telemetry, Usage
from .agents import Agent, AgentResult, CodingAgent, ReasoningAgent
from .runner import Harness, RunOutcome
from .errors import (
    APIError, BudgetExceeded, HarnessError, PatchError,
    ReasoningRoundtripError, RefusalUnresolved, TransportError,
)

__version__ = "0.1.0"

__all__ = [
    "Harness", "RunOutcome", "Config", "Budget", "RetryPolicy", "ModelSpec",
    "FLASH", "PRO", "is_peak", "DeepSeekClient", "Completion", "Thinking",
    "Conversation", "WorkingMemory", "RefusalHandler", "RefusalKind", "classify",
    "apply_edits", "parse_edits", "repomap", "selection",
    "Candidate", "SelectionResult", "ToolRegistry", "default_registry", "exec_command",
    "Telemetry", "Usage", "Agent", "AgentResult", "CodingAgent", "ReasoningAgent",
    "HarnessError", "TransportError", "APIError", "ReasoningRoundtripError",
    "BudgetExceeded", "RefusalUnresolved", "PatchError", "__version__",
]
