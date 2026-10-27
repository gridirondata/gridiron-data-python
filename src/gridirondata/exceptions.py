"""Exceptions raised by the GridIron Data client.

Every HTTP error is an :class:`APIError`, which is also a
``requests.HTTPError`` (so ``except requests.HTTPError`` keeps working) and
carries the API's own JSON fields: ``message`` (the API's ``error`` text,
which already says what to do), ``reason`` and ``upgrade_url`` when present.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Optional

import requests


class GridIronError(Exception):
    """Base class for everything this package raises."""


class ConfigurationError(GridIronError):
    """The client is missing something it needs, such as an API key."""


class GridIronConnectionError(GridIronError):
    """The API could not be reached (DNS, refused connection, timeout),
    even after retrying."""


class APIError(GridIronError, requests.HTTPError):
    """The API answered with an HTTP error status."""

    def __init__(self, message: str, *, status_code: int,
                 body: Optional[Dict[str, Any]] = None,
                 response: Optional[requests.Response] = None) -> None:
        self.message = message
        self.status_code = status_code
        self.body: Dict[str, Any] = body or {}
        self.reason: Optional[str] = self.body.get("reason") or None
        self.upgrade_url: Optional[str] = self.body.get("upgrade_url") or None
        super().__init__(f"HTTP {status_code}: {message}", response=response)


class BadRequestError(APIError):
    """400: a parameter was invalid (bad week, limit, cursor, since...)."""


class AuthenticationError(APIError):
    """401: the API key is missing or not recognised."""


class PermissionDeniedError(APIError):
    """403: the key is valid but may not make this request.

    Most often the plan doesn't include the route (``reason`` is
    ``basic_required`` or ``pro_required``); ``message`` is the API's upgrade
    text and ``upgrade_url`` links to pricing. Other reasons:
    ``subscription_paused``, ``subscription_canceled``, ``payment_failed``,
    ``subscription_inactive``.
    """

    @property
    def required_plan(self) -> Optional[str]:
        return self.body.get("required_plan") or None


class NotFoundError(APIError):
    """404: no such player, season, opponent or endpoint."""


class RateLimitError(APIError):
    """429: a per-minute rate limit or a monthly quota was hit.

    ``reason`` is ``rate_limited`` (slow down; ``retry_after`` is the number
    of seconds to wait), ``quota_exceeded`` or ``free_monthly_quota`` (the
    month is used up; ``resets_at`` is when it resets, the 1st at 00:00 UTC,
    and ``upgrade_url`` links to pricing).
    """

    def __init__(self, message: str, *, status_code: int,
                 body: Optional[Dict[str, Any]] = None,
                 response: Optional[requests.Response] = None,
                 retry_after: Optional[float] = None,
                 resets_at: Optional[datetime.datetime] = None) -> None:
        super().__init__(message, status_code=status_code, body=body, response=response)
        self.retry_after = retry_after
        self.resets_at = resets_at


class ServerError(APIError):
    """5xx: the API failed, even after retrying."""
