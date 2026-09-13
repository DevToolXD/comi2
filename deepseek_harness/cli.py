"""Command line entry point: ``python -m deepseek_harness``."""
from __future__ import annotations

import argparse
import sys

from .config import Budget, Config
from .errors import BudgetExceeded, HarnessError, RefusalUnresolved
from .runner import Harness


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deepseek-harness",
        description="Run a task through the DeepSeek harness.",
    )
    p.add_argument("task", nargs="?", help="the task; omit to read from stdin")
    p.add_argument("--lane", choices=["coding", "reasoning", "tools", "direct"],
                   help="skip routing and force a lane")
    p.add_argument("--root", default=".", help="workspace root (default: cwd)")
    p.add_argument("--verify", metavar="CMD",
                   help="command proving the change works, e.g. 'pytest -q'")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "max"])
    p.add_argument("--samples", type=int, default=1,
                   help="reasoning lane: best-of-N with a judge (cost scales linearly)")
    p.add_argument("--no-review", action="store_true", help="coding lane: skip self-review")
    p.add_argument("--planner", help="override the planner model id")
    p.add_argument("--worker", help="override the worker model id")
    p.add_argument("--max-usd", type=float, help="hard cost ceiling for this run")
    p.add_argument("--max-tokens", type=int, help="hard token ceiling for this run")
    p.add_argument("--purpose", metavar="TEXT",
                   help="operator context attached when re-requesting after a refusal")
    p.add_argument("--refusal-attempts", type=int, default=3)
    p.add_argument("--log-dir", help="write telemetry.json here")
    p.add_argument("-v", "--verbose", action="store_true", help="per-call token/cost lines")
    p.add_argument("--json", action="store_true", help="emit the full result as JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    task = args.task or sys.stdin.read().strip()
    if not task:
        print("error: no task given", file=sys.stderr)
        return 2

    config = Config(
        budget=Budget(max_usd=args.max_usd, max_tokens=args.max_tokens),
        refusal_max_attempts=args.refusal_attempts,
        verbose=args.verbose,
        log_dir=args.log_dir,
    )
    if args.planner:
        config.planner_model = args.planner
    if args.worker:
        config.worker_model = config.utility_model = args.worker
    if not config.api_key:
        print("error: DEEPSEEK_API_KEY is not set", file=sys.stderr)
        return 2

    try:
        with Harness(config, root=args.root, verify_command=args.verify,
                     purpose_context=args.purpose) as h:
            outcome = h.run(task, lane=args.lane, effort=args.effort,
                            samples=args.samples, review=not args.no_review)
            if args.json:
                import json
                print(json.dumps({
                    "lane": outcome.lane,
                    "ok": outcome.ok,
                    "output": outcome.output,
                    "steps": outcome.result.steps,
                    "telemetry": outcome.telemetry.summary(),
                }, ensure_ascii=False, indent=2))
            else:
                print(outcome.output)
                print(f"\n--- {outcome.report()}", file=sys.stderr)
            return 0 if outcome.ok else 1
    except RefusalUnresolved as exc:
        print(f"model did not proceed: {exc}", file=sys.stderr)
        return 3
    except BudgetExceeded as exc:
        print(f"{exc}", file=sys.stderr)
        return 4
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
