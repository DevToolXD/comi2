"""Harness exception hierarchy."""
from __future__ import annotations


class HarnessError(Exception):
    """Base class for every error this package raises."""


class TransportError(HarnessError):
    """Network or HTTP failure that survived the retry policy."""

    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class APIError(TransportError):
    """A 4xx the server considered our fault."""


class ReasoningRoundtripError(APIError):
    """The server rejected our history for missing/blank ``reasoning_content``.

    Raised so the client can repair the history and retry once rather than
    surfacing DeepSeek's opaque 400 to the caller.
    """


class BudgetExceeded(HarnessError):
    """A run hit its cost / token / wall-clock ceiling."""


class RefusalUnresolved(HarnessError):
    """The model refused and the bounded re-request strategy did not clear it."""

    def __init__(self, message: str, attempts: list | None = None):
        super().__init__(message)
        self.attempts = attempts or []


class PatchError(HarnessError):
    """A model-produced edit block could not be parsed or applied."""
