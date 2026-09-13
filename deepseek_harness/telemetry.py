"""Token / cost / cache accounting.

The single most useful diagnostic a harness can give you is whether a change
made quality go up *while* tokens went down.  That only works if you measure
cache hit rate, not just total tokens, because DeepSeek cache hits are ~50x
cheaper than misses.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict

from .config import Config, price_multiplier, spec_for


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0

    @classmethod
    def from_api(cls, raw: dict | None) -> "Usage":
        raw = raw or {}
        details = raw.get("completion_tokens_details") or {}
        prompt = int(raw.get("prompt_tokens", 0) or 0)
        hit = int(raw.get("prompt_cache_hit_tokens", 0) or 0)
        miss = raw.get("prompt_cache_miss_tokens")
        miss = int(miss) if miss is not None else max(prompt - hit, 0)
        return cls(
            prompt_tokens=prompt,
            completion_tokens=int(raw.get("completion_tokens", 0) or 0),
            reasoning_tokens=int(details.get("reasoning_tokens", 0) or 0),
            cache_hit_tokens=hit,
            cache_miss_tokens=miss,
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
            self.cache_hit_tokens + other.cache_hit_tokens,
            self.cache_miss_tokens + other.cache_miss_tokens,
        )

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_hit_tokens / self.prompt_tokens if self.prompt_tokens else 0.0


def cost_usd(usage: Usage, model_id: str, at=None) -> float:
    spec = spec_for(model_id)
    m = price_multiplier(at)
    return (
        usage.cache_hit_tokens * spec.price_cache_hit
        + usage.cache_miss_tokens * spec.price_cache_miss
        + usage.completion_tokens * spec.price_output
    ) * m / 1_000_000


@dataclass
class Call:
    model: str
    usage: Usage
    cost: float
    latency: float
    label: str = ""
    ok: bool = True
    note: str = ""


@dataclass
class Telemetry:
    """Accumulates per-call records and enforces the budget."""

    config: Config
    calls: list[Call] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def record(self, model: str, usage: Usage, latency: float, label: str = "",
               ok: bool = True, note: str = "") -> Call:
        call = Call(model, usage, cost_usd(usage, model), latency, label, ok, note)
        self.calls.append(call)
        if self.config.verbose:
            print(
                f"  [{label or 'call'}] {model} "
                f"in={usage.prompt_tokens} (cache {usage.cache_hit_rate:.0%}) "
                f"out={usage.completion_tokens} think={usage.reasoning_tokens} "
                f"${call.cost:.4f} {latency:.1f}s",
                flush=True,
            )
        return call

    # -- aggregates ------------------------------------------------------
    @property
    def usage(self) -> Usage:
        total = Usage()
        for c in self.calls:
            total = total + c.usage
        return total

    @property
    def cost(self) -> float:
        return sum(c.cost for c in self.calls)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def budget_exceeded(self) -> str | None:
        b = self.config.budget
        if b.max_usd is not None and self.cost >= b.max_usd:
            return f"cost ${self.cost:.2f} >= budget ${b.max_usd:.2f}"
        if b.max_tokens is not None and self.usage.total_tokens >= b.max_tokens:
            return f"tokens {self.usage.total_tokens} >= budget {b.max_tokens}"
        if b.max_wall_seconds is not None and self.elapsed >= b.max_wall_seconds:
            return f"elapsed {self.elapsed:.0f}s >= budget {b.max_wall_seconds:.0f}s"
        return None

    def summary(self) -> dict:
        u = self.usage
        return {
            "calls": len(self.calls),
            "prompt_tokens": u.prompt_tokens,
            "completion_tokens": u.completion_tokens,
            "reasoning_tokens": u.reasoning_tokens,
            "cache_hit_rate": round(u.cache_hit_rate, 4),
            "cost_usd": round(self.cost, 4),
            "elapsed_s": round(self.elapsed, 1),
        }

    def report(self) -> str:
        s = self.summary()
        return (
            f"calls={s['calls']}  in={s['prompt_tokens']:,} "
            f"(cache {s['cache_hit_rate']:.0%})  out={s['completion_tokens']:,}  "
            f"think={s['reasoning_tokens']:,}  ${s['cost_usd']:.4f}  {s['elapsed_s']}s"
        )

    def dump(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {"summary": self.summary(), "calls": [asdict(c) for c in self.calls]},
                fh, indent=2, ensure_ascii=False,
            )
