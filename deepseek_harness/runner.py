"""Top-level orchestration: routing, budget enforcement, tool loops."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import messages as M
from .agents import AgentResult, CodingAgent, ReasoningAgent
from .client import DeepSeekClient, Thinking
from .config import Config
from .errors import BudgetExceeded
from .telemetry import Telemetry
from .tools import ToolRegistry, default_registry

ROUTE_PROMPT = """\
Classify this request into exactly one lane. Answer with one word.

- coding    : it wants code written, changed, debugged or reviewed in a repository
- reasoning : it wants an analysis, a decision, a proof, a puzzle solved, a \
root cause found -- thinking, not editing
- tools     : it needs to inspect or operate on the environment (run commands, \
read files) but is not itself a code change
- direct    : a short factual or conversational answer suffices

REQUEST:
{task}

One word:"""


@dataclass
class RunOutcome:
    lane: str
    result: AgentResult
    telemetry: Telemetry

    @property
    def output(self) -> str:
        return self.result.output

    @property
    def ok(self) -> bool:
        return self.result.ok

    def report(self) -> str:
        return f"[{self.lane}] {self.telemetry.report()}"


class Harness:
    """Front door.  Owns the client, budget and lane routing."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        root: str | Path = ".",
        verify_command: str | None = None,
        purpose_context: str | None = None,
        registry: ToolRegistry | None = None,
    ):
        self.config = config or Config()
        self.telemetry = Telemetry(self.config)
        self.client = DeepSeekClient(self.config, self.telemetry)
        self.root = Path(root).resolve()
        self.verify_command = verify_command
        self.purpose_context = purpose_context
        self.registry = registry or default_registry(self.root)

    # -- public ----------------------------------------------------------
    def run(self, task: str, *, lane: str | None = None, effort: str = "high",
            samples: int = 1, candidates: int = 1, review: bool = True) -> RunOutcome:
        lane = lane or self.route(task)
        self._check_budget()

        if lane == "coding":
            agent = CodingAgent(
                self.client, self.config, root=self.root,
                registry=self.registry, verify_command=self.verify_command,
                purpose_context=self.purpose_context,
            )
            result: AgentResult = agent.run(task, effort=effort,
                                            candidates=candidates, review=review)
        elif lane == "reasoning":
            agent = ReasoningAgent(self.client, self.config,
                                   purpose_context=self.purpose_context)
            result = agent.solve(task, effort=effort, samples=samples)
        elif lane == "tools":
            result = self.tool_loop(task, effort=effort)
        else:
            text = self.client.complete(
                [M.user(task)], model=self.config.worker_model,
                thinking=Thinking.OFF, label="direct",
            ).text
            result = AgentResult(output=text)

        return RunOutcome(lane, result, self.telemetry)

    def route(self, task: str) -> str:
        verdict = self.client.complete(
            [M.user(ROUTE_PROMPT.format(task=task[:4000]))],
            model=self.config.utility_model, thinking=Thinking.OFF,
            max_tokens=6, label="route",
        ).text.strip().lower()
        for lane in ("coding", "reasoning", "tools", "direct"):
            if lane in verdict:
                return lane
        return "reasoning"

    def tool_loop(self, task: str, *, effort: str = "high",
                  max_iterations: int | None = None) -> AgentResult:
        """Classic agentic loop with compaction, budget checks and parallel tools."""
        from .context import Conversation

        convo = Conversation(
            self.config,
            system_prompt="You are an operator with tool access. Use tools to "
                          "establish facts rather than assuming them. Stop as soon "
                          "as the task is done and report what you found.",
            task_context=f"WORKSPACE: {self.root}",
        )
        convo.memory.goal = task[:500]
        convo.add_user(task)

        model = self.config.planner_model
        think = Thinking(enabled=True, effort=effort)
        limit = max_iterations or self.config.budget.max_turns
        steps: list[dict] = []

        for i in range(limit):
            self._check_budget()
            convo.maybe_compact(self.client, model)
            completion = self.client.complete(
                convo.render(), model=model, thinking=think,
                tools=self.registry.schemas(), label=f"tools:{i + 1}",
            )
            convo.add_assistant(completion)

            if not completion.tool_calls:
                return AgentResult(output=completion.text, steps=steps, memory=convo.memory)

            for call, output in self.registry.run_all(completion.tool_calls):
                convo.add_tool_result(call.id, output, call.name)
                steps.append({"tool": call.name, "args": call.arguments[:500],
                              "result": output[:1000]})
                if output.startswith("ERROR:"):
                    convo.memory.add_dead_end(f"{call.name}({call.arguments[:120]}) -> {output[:200]}")

        return AgentResult(
            output="Iteration limit reached before the task completed.",
            ok=False, steps=steps, memory=convo.memory,
            note=f"stopped after {limit} iterations",
        )

    # -- internals -------------------------------------------------------
    def _check_budget(self) -> None:
        reason = self.telemetry.budget_exceeded()
        if reason:
            raise BudgetExceeded(f"run halted: {reason}")

    def close(self) -> None:
        if self.config.log_dir:
            Path(self.config.log_dir).mkdir(parents=True, exist_ok=True)
            self.telemetry.dump(str(Path(self.config.log_dir) / "telemetry.json"))
        self.client.close()

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
