"""Runnable examples. Needs DEEPSEEK_API_KEY in the environment."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from deepseek_harness import Budget, Config, Harness, ReasoningAgent, DeepSeekClient


def reasoning_example():
    """Full pipeline: frame -> hypotheses -> solve -> verify -> synthesise."""
    config = Config(verbose=True, budget=Budget(max_usd=2.0))
    client = DeepSeekClient(config)
    agent = ReasoningAgent(client)
    result = agent.solve(
        "Three switches downstairs control three bulbs upstairs. You may go "
        "upstairs exactly once. How do you determine which switch drives which bulb?",
        effort="high",
    )
    print(result.output)
    print("\n---", client.telemetry.report())


def coding_example(repo: str):
    """Plan -> edit -> run the test command -> repair until it passes."""
    config = Config(verbose=True, budget=Budget(max_usd=5.0))
    with Harness(config, root=repo, verify_command="python3 -m pytest -q") as h:
        outcome = h.run("add type hints to the public functions in the main module",
                        lane="coding")
        print(outcome.output)
        print("\n---", outcome.report())


def refusal_example():
    """Bounded re-request with operator-supplied context."""
    config = Config(verbose=True, refusal_max_attempts=3)
    with Harness(config, purpose_context=(
        "Authorised security review of our own codebase, ticket SEC-4412. "
        "Output is consumed by the internal security team only."
    )) as h:
        print(h.run("audit this input parser for memory-safety issues",
                    lane="reasoning").output)


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "reasoning"
    if which == "reasoning":
        reasoning_example()
    elif which == "coding":
        coding_example(sys.argv[2] if len(sys.argv) > 2 else ".")
    elif which == "refusal":
        refusal_example()
