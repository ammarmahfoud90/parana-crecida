"""Scripted HTTP fakes.

A hand-rolled fake rather than ``responses``/``requests-mock``: the client's
contract is almost entirely about *failure* - which status retries, which
raises, how long it waits - and driving that from a queue of canned responses
is clearer than a matcher DSL, and keeps the test dependencies to pytest.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any

from ina_a5 import A5Client

_UNDECODABLE = object()


class FakeResponse:
    """The parts of ``requests.Response`` the client touches."""

    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        *,
        text: str | None = None,
        headers: dict[str, str] | None = None,
        url: str = "https://example.test/a5/thing",
    ) -> None:
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self._payload = payload
        if text is not None:
            self.text = text
        elif payload is _UNDECODABLE:
            self.text = '{"features": ['
        else:
            self.text = jsonlib.dumps(payload)

    def json(self) -> Any:
        if self._payload is _UNDECODABLE:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


def undecodable(status_code: int = 200) -> FakeResponse:
    """A 2xx whose body is not JSON - typically a truncated response."""
    return FakeResponse(status_code, _UNDECODABLE)


class FakeSession:
    """Returns queued responses in order; raises queued exceptions."""

    def __init__(self, script: list[Any] | None = None) -> None:
        self.headers: dict[str, str] = {}
        self.script = list(script or [])
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.closed = False

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append((url, params))
        if not self.script:
            raise AssertionError(f"unexpected extra request to {url} {params}")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


class RecordingClient(A5Client):
    """An :class:`A5Client` that records its backoff instead of sleeping."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.waits: list[float] = []

    def _sleep(self, seconds: float) -> None:
        self.waits.append(seconds)


def build_client(script: list[Any], **retry_kwargs: Any) -> RecordingClient:
    """A client over a scripted session, with fast, deterministic backoff."""
    from ina_a5 import RetryPolicy

    retry_kwargs.setdefault("attempts", 3)
    retry_kwargs.setdefault("base_delay", 0.01)
    return RecordingClient(
        "https://example.test/a5",
        token="",  # explicit empty: never pick up a real A5_TOKEN
        session=FakeSession(script),
        retry=RetryPolicy(**retry_kwargs),
    )
