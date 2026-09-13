"""Model registry, pricing and runtime profiles for the DeepSeek harness.

Everything here is data, not logic, so it can be overridden without touching
code paths.  Model IDs and prices move faster than this file does -- override
them via ``DSH_MODEL_*`` env vars or by passing a custom ``Config``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, time as _time

# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    """Capabilities of one served model.

    ``requires_reasoning_roundtrip`` is the important one: DeepSeek V4 thinking
    mode rejects a request whose assistant history is missing the verbatim
    ``reasoning_content`` from the turn that produced it.
    """

    id: str
    context_window: int = 1_000_000
    max_output: int = 384_000
    supports_thinking: bool = True
    supports_tools: bool = True
    supports_json: bool = True
    supports_fim: bool = False
    requires_reasoning_roundtrip: bool = True
    # USD per 1M tokens, peak rates. Off-peak is applied as a multiplier.
    price_cache_hit: float = 0.014
    price_cache_miss: float = 0.44
    price_output: float = 1.32


# Current lineup (verified 2026-09). Legacy `deepseek-chat` / `deepseek-reasoner`
# aliases were retired after 2026-07-24 and are intentionally absent.
FLASH = ModelSpec(
    id=os.getenv("DSH_MODEL_FLASH", "deepseek-flash"),
    price_cache_hit=0.014,
    price_cache_miss=0.44,
    price_output=1.32,
)

PRO = ModelSpec(
    id=os.getenv("DSH_MODEL_PRO", "deepseek-v4-pro"),
    price_cache_hit=0.017,
    price_cache_miss=0.435,
    price_output=0.87,
)

MODELS: dict[str, ModelSpec] = {FLASH.id: FLASH, PRO.id: PRO}

# Alternate IDs seen in the wild; probed at runtime if the primary 404s.
MODEL_ID_FALLBACKS: dict[str, tuple[str, ...]] = {
    FLASH.id: ("deepseek-v4-flash", "deepseek-v4.1-flash", "deepseek-chat"),
    PRO.id: ("deepseek-v4-pro-0813", "deepseek-pro", "deepseek-reasoner"),
}


def spec_for(model_id: str) -> ModelSpec:
    """Return the spec for ``model_id``, synthesising a permissive one if unknown."""
    if model_id in MODELS:
        return MODELS[model_id]
    for primary, alts in MODEL_ID_FALLBACKS.items():
        if model_id in alts:
            return replace(MODELS[primary], id=model_id)
    return ModelSpec(id=model_id)


# --------------------------------------------------------------------------
# Peak / off-peak
# --------------------------------------------------------------------------

# DeepSeek peak windows, UTC, Mon-Fri. Off-peak is half price, which covers
# roughly 79% of the week including all weekend.
PEAK_WINDOWS_UTC: tuple[tuple[_time, _time], ...] = (
    (_time(1, 0), _time(4, 0)),
    (_time(6, 0), _time(10, 0)),
)
OFF_PEAK_MULTIPLIER = 0.5


def is_peak(now: datetime | None = None) -> bool:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if now.weekday() >= 5:  # Sat/Sun are fully off-peak
        return False
    t = now.timetz().replace(tzinfo=None)
    return any(start <= t < end for start, end in PEAK_WINDOWS_UTC)


def price_multiplier(now: datetime | None = None) -> float:
    return 1.0 if is_peak(now) else OFF_PEAK_MULTIPLIER


# --------------------------------------------------------------------------
# Runtime config
# --------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    max_attempts: int = 6
    base_delay: float = 1.0
    max_delay: float = 45.0
    jitter: float = 0.3
    retry_status: tuple[int, ...] = (408, 409, 425, 429, 500, 502, 503, 504)


@dataclass
class Budget:
    """Hard stops so a runaway agent loop cannot drain an account."""

    max_usd: float | None = None
    max_tokens: int | None = None
    max_wall_seconds: float | None = None
    max_turns: int = 80


@dataclass
class Config:
    api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    base_url: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    )
    # Model routing. Deep work goes to PRO, mechanical work to FLASH.
    planner_model: str = PRO.id
    worker_model: str = FLASH.id
    utility_model: str = FLASH.id  # compaction, classification, distillation

    timeout: float = 600.0
    connect_timeout: float = 15.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    budget: Budget = field(default_factory=Budget)

    # Context management
    compact_at_fraction: float = 0.60   # of the model context window
    keep_live_turns: int = 6            # turns kept with verbatim reasoning_content
    max_digest_chars: int = 24_000      # cap on the accumulated digest
    max_working_memory_chars: int = 12_000

    # Refusal handling
    refusal_max_attempts: int = 3
    refusal_escalate: bool = True

    log_dir: str | None = field(default_factory=lambda: os.getenv("DSH_LOG_DIR"))
    verbose: bool = bool(os.getenv("DSH_VERBOSE"))

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
