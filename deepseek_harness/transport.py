"""HTTP transport with retry, backoff and rate-limit awareness.

Uses ``httpx`` when available and falls back to ``urllib`` so the harness runs
with zero third-party dependencies.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request

from .config import Config, RetryPolicy
from .errors import APIError, ReasoningRoundtripError, TransportError

try:  # pragma: no cover - exercised by whichever backend is installed
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore


# DeepSeek phrases this several ways across versions; match on the stable parts.
_ROUNDTRIP_MARKERS = (
    "reasoning_content",
    "must be passed back",
    "thinking mode",
)


def _is_roundtrip_error(status: int, body: str) -> bool:
    if status != 400:
        return False
    low = body.lower()
    return "reasoning_content" in low and any(m in low for m in _ROUNDTRIP_MARKERS[1:])


class Transport:
    """Single-purpose JSON POST client for the DeepSeek REST API."""

    def __init__(self, config: Config):
        self.config = config
        self._client = None
        if httpx is not None:
            self._client = httpx.Client(
                base_url=config.base_url,
                timeout=httpx.Timeout(config.timeout, connect=config.connect_timeout),
            )

    # -- public ----------------------------------------------------------
    def post_json(self, path: str, payload: dict, *, label: str = "") -> dict:
        """POST ``payload``, retrying transient failures per the retry policy."""
        policy = self.config.retry
        last: Exception | None = None

        for attempt in range(1, policy.max_attempts + 1):
            try:
                status, body, retry_after = self._send(path, payload)
            except Exception as exc:  # network-level failure
                last = TransportError(f"{type(exc).__name__}: {exc}")
                if attempt == policy.max_attempts:
                    break
                self._sleep(policy, attempt, None)
                continue

            if 200 <= status < 300:
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:
                    raise TransportError(f"malformed JSON response: {exc}", status, body[:2000])

            if _is_roundtrip_error(status, body):
                # Not retryable as-is; the caller must repair the history first.
                raise ReasoningRoundtripError(
                    "DeepSeek rejected the history: assistant turns in thinking mode "
                    "must replay their verbatim reasoning_content.",
                    status, body[:2000],
                )

            if status in policy.retry_status and attempt < policy.max_attempts:
                last = TransportError(f"HTTP {status}", status, body[:2000])
                self._sleep(policy, attempt, retry_after)
                continue

            cls = APIError if 400 <= status < 500 else TransportError
            raise cls(f"HTTP {status} from {path}: {body[:600]}", status, body[:4000])

        raise last or TransportError("request failed with no diagnostic")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # -- internals -------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise APIError("DEEPSEEK_API_KEY is not set")
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _send(self, path: str, payload: dict) -> tuple[int, str, str | None]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self._client is not None:
            resp = self._client.post(path, content=data, headers=self._headers())
            return resp.status_code, resp.text, resp.headers.get("retry-after")

        req = urllib.request.Request(
            self.config.base_url + path, data=data, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            return exc.code, body, exc.headers.get("Retry-After")

    @staticmethod
    def _sleep(policy: RetryPolicy, attempt: int, retry_after: str | None) -> None:
        if retry_after:
            try:
                time.sleep(min(float(retry_after), policy.max_delay))
                return
            except ValueError:
                pass
        delay = min(policy.base_delay * (2 ** (attempt - 1)), policy.max_delay)
        time.sleep(delay * (1 + random.uniform(-policy.jitter, policy.jitter)))
