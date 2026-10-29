"""HTTP client for the INA a5 API.

Replaces the six near-identical copies of ``session()`` / ``get_json()`` in
the rios-argentina scripts. Three behaviours differ from those copies, and
they are the reason this package exists:

1. Failure raises. The originals returned ``None`` for a 429, a 500, a
   timeout and an empty result alike, which silently turned "the server
   throttled us" into "this gauge has no data".
2. Retries are status-aware: 429 and 5xx back off exponentially and honour
   ``Retry-After``; other 4xx fail immediately because retrying cannot help.
3. One session per client. ``requests.Session`` is not thread-safe, and the
   original climatology build shared one across a thread pool. Use
   :meth:`A5Client.clone` to give each worker its own.

The server belongs to a public agency. Keep concurrency low.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from .errors import A5AuthError, A5HttpError, A5ResponseError, A5TransportError

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://alerta.ina.gob.ar/a5"
DEFAULT_USER_AGENT = "ina-a5/0.1 (hydrology research)"
DEFAULT_TIMEOUT = 60.0

#: Statuses worth another attempt. Everything else in 4xx is our own fault.
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff with jitter, capped."""

    attempts: int = 4
    base_delay: float = 1.5
    max_delay: float = 30.0
    jitter: float = 0.25
    respect_retry_after: bool = True
    #: Ignore an absurd Retry-After; fail fast rather than park for an hour.
    max_retry_after: float = 60.0

    def delay_for(self, failed_attempt: int) -> float:
        """Seconds to wait after ``failed_attempt`` (0-based) has failed."""
        raw = min(self.base_delay * (2**failed_attempt), self.max_delay)
        return raw * (1.0 + random.uniform(-self.jitter, self.jitter))


class _RetryableStatus(Exception):
    """Internal marker for a status we intend to retry."""

    def __init__(self, status: int, url: str, body: str, retry_after: str | None) -> None:
        self.status = status
        self.url = url
        self.body = body
        self.retry_after = retry_after
        super().__init__(f"HTTP {status} for {url}")


def _parse_retry_after(value: str | None) -> float | None:
    """``Retry-After`` as seconds. Accepts both the delta and HTTP-date forms."""
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


class A5Client:
    """A session against one a5 base URL."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT,
        retry: RetryPolicy | None = None,
        session: requests.Session | None = None,
    ) -> None:
        env_base = os.environ.get("A5_BASE_URL")
        self.base_url = (base_url or env_base or DEFAULT_BASE_URL).rstrip("/")
        # A token has never been required for the public endpoints, but the
        # rios-argentina deploy passes one as an optional secret.
        self.token = token if token is not None else os.environ.get("A5_TOKEN")
        self.user_agent = user_agent
        self.timeout = timeout
        self.retry = retry or RetryPolicy()
        self._owns_session = session is None
        self.session = session or requests.Session()
        self.session.headers.update(
            {"User-Agent": user_agent, "Accept": "application/json"}
        )
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    # -- lifecycle ---------------------------------------------------------

    def clone(self) -> A5Client:
        """A second client with the same configuration and its own session.

        One per thread. Sharing a ``requests.Session`` across a thread pool
        works most of the time, which is the worst way for a thing to fail.
        """
        return A5Client(
            self.base_url,
            token=self.token,
            user_agent=self.user_agent,
            timeout=self.timeout,
            retry=self.retry,
        )

    def with_retry(self, **kwargs: Any) -> A5Client:
        """A clone with an adjusted policy, e.g. ``with_retry(attempts=1)``."""
        clone = self.clone()
        clone.retry = replace(self.retry, **kwargs)
        return clone

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def __enter__(self) -> A5Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- requests ----------------------------------------------------------

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``path`` and return the decoded body.

        Raises :class:`A5AuthError`, :class:`A5HttpError`,
        :class:`A5TransportError` or :class:`A5ResponseError`. Never returns
        ``None`` to mean failure: an empty result comes back as an empty list
        or dict, and only when the server really said so.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        last: Exception | None = None

        for attempt in range(self.retry.attempts):
            if attempt:
                wait = self._wait_for(last, attempt - 1)
                log.debug(
                    "retrying %s in %.1fs (attempt %d/%d)",
                    url, wait, attempt + 1, self.retry.attempts,
                )
                self._sleep(wait)
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last = A5TransportError(f"{type(exc).__name__} for {url}: {exc}")
                continue

            if response.status_code in (401, 403):
                # No retry: a missing token will still be missing next time.
                raise A5AuthError(response.status_code, response.url, response.text)
            if response.status_code in RETRY_STATUSES:
                last = _RetryableStatus(
                    response.status_code,
                    response.url,
                    response.text,
                    response.headers.get("Retry-After"),
                )
                continue
            if response.status_code >= 400:
                raise A5HttpError(response.status_code, response.url, response.text)

            try:
                return response.json()
            except ValueError as exc:
                # Usually a truncated body, which the next attempt may well
                # get whole, so this is retryable rather than fatal.
                last = A5ResponseError(f"undecodable JSON from {response.url}: {exc}")
                continue

        assert last is not None  # the loop always runs at least once
        if isinstance(last, _RetryableStatus):
            raise A5HttpError(last.status, last.url, last.body) from None
        raise last

    # -- internals ---------------------------------------------------------

    def _wait_for(self, last: Exception | None, failed_attempt: int) -> float:
        backoff = self.retry.delay_for(failed_attempt)
        if self.retry.respect_retry_after and isinstance(last, _RetryableStatus):
            hinted = _parse_retry_after(last.retry_after)
            if hinted is not None and hinted <= self.retry.max_retry_after:
                return max(backoff, hinted)
        return backoff

    def _sleep(self, seconds: float) -> None:
        """Overridden in tests so the suite does not actually wait."""
        time.sleep(seconds)
