"""Exception types for the a5 client.

The point of this module is the distinction the original scripts could not
make: ``get_json`` returned ``None`` for a 429, a 500, a timeout and a
genuinely empty result alike, so a rate-limited run looked exactly like a
station with no data. Here every failure raises and only an empty-but-
successful response returns empty.
"""

from __future__ import annotations


class A5Error(Exception):
    """Base class for every error raised by this package."""


class A5TransportError(A5Error):
    """The request never produced an HTTP response (timeout, DNS, reset)."""


class A5HttpError(A5Error):
    """The server answered with a status we cannot use."""

    def __init__(self, status: int, url: str, body: str = "") -> None:
        self.status = status
        self.url = url
        self.body = body[:500]
        detail = f" - {self.body}" if self.body else ""
        super().__init__(f"HTTP {status} for {url}{detail}")


class A5AuthError(A5HttpError):
    """401 or 403.

    A token is not normally required; the public endpoints have always been
    open. If this fires, set ``A5_TOKEN`` (request one from INA).
    """


class A5ResponseError(A5Error):
    """A 2xx response whose body was not the JSON we expected."""


class A5Truncated(A5Error):
    """A history response looked cut short and could not be completed."""
