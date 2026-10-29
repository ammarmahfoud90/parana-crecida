"""The client's failure contract.

The whole reason this package exists is that the original ``get_json``
collapsed "throttled", "server broke", "timed out" and "no data here" into a
single ``None``. These tests pin that distinction down.
"""

from __future__ import annotations

import pytest
from a5fakes import FakeResponse, FakeSession, undecodable

from ina_a5 import A5AuthError, A5Client, A5HttpError, A5ResponseError, A5TransportError
from ina_a5.client import _parse_retry_after


def test_success_returns_body(make_client):
    client = make_client([FakeResponse(200, {"features": [1, 2]})])
    assert client.get_json("thing") == {"features": [1, 2]}


def test_empty_result_is_not_an_error(make_client):
    """An empty-but-successful response is data, not failure."""
    client = make_client([FakeResponse(200, [])])
    assert client.get_json("getObservaciones") == []
    assert client.waits == []


def test_401_raises_and_does_not_retry(make_client):
    client = make_client([FakeResponse(401, {"error": "unauthorized"})])
    with pytest.raises(A5AuthError) as excinfo:
        client.get_json("thing")
    assert excinfo.value.status == 401
    assert client.waits == []  # a missing token stays missing


def test_403_is_an_auth_error(make_client):
    client = make_client([FakeResponse(403, {"error": "forbidden"})])
    with pytest.raises(A5AuthError):
        client.get_json("thing")


def test_404_raises_immediately(make_client):
    """Retrying a client error cannot help, so it must not cost three waits."""
    client = make_client([FakeResponse(404, {"error": "no such series"})])
    with pytest.raises(A5HttpError) as excinfo:
        client.get_json("thing")
    assert excinfo.value.status == 404
    assert client.waits == []


def test_429_is_retried_then_succeeds(make_client):
    client = make_client([FakeResponse(429, {}), FakeResponse(200, {"ok": True})])
    assert client.get_json("thing") == {"ok": True}
    assert len(client.waits) == 1


def test_429_exhausted_raises_rather_than_returning_empty(make_client):
    """The old code returned None here, i.e. "this gauge has no data"."""
    client = make_client([FakeResponse(429, {})] * 3)
    with pytest.raises(A5HttpError) as excinfo:
        client.get_json("thing")
    assert excinfo.value.status == 429
    assert len(client.waits) == 2  # between attempts, not before the first


def test_503_is_retried(make_client):
    client = make_client([FakeResponse(503, {}), FakeResponse(200, [7])])
    assert client.get_json("thing") == [7]


def test_transport_error_is_retried_then_raises(make_client, timeout_error):
    client = make_client([timeout_error] * 3)
    with pytest.raises(A5TransportError):
        client.get_json("thing")
    assert len(client.waits) == 2


def test_transport_error_recovers(make_client, timeout_error):
    client = make_client([timeout_error, FakeResponse(200, {"ok": 1})])
    assert client.get_json("thing") == {"ok": 1}


def test_undecodable_body_is_retried(make_client):
    """A truncated body is usually transient, so it is worth another go."""
    client = make_client([undecodable(), FakeResponse(200, {"ok": 1})])
    assert client.get_json("thing") == {"ok": 1}


def test_undecodable_body_exhausted_raises(make_client):
    client = make_client([undecodable()] * 3)
    with pytest.raises(A5ResponseError):
        client.get_json("thing")


def test_retry_after_is_honoured(make_client):
    client = make_client(
        [FakeResponse(429, {}, headers={"Retry-After": "7"}), FakeResponse(200, {})]
    )
    client.get_json("thing")
    assert client.waits == [pytest.approx(7.0, abs=0.001)]


def test_absurd_retry_after_is_ignored(make_client):
    """Do not park the whole run for an hour because a header said so."""
    client = make_client(
        [FakeResponse(429, {}, headers={"Retry-After": "3600"}), FakeResponse(200, {})],
        max_retry_after=60.0,
    )
    client.get_json("thing")
    assert client.waits[0] < 1.0


def test_backoff_grows(make_client):
    client = make_client(
        [FakeResponse(503, {})] * 4, attempts=4, base_delay=1.0, jitter=0.0
    )
    with pytest.raises(A5HttpError):
        client.get_json("thing")
    assert client.waits == [1.0, 2.0, 4.0]


def test_url_is_joined_without_double_slash(make_client):
    client = make_client([FakeResponse(200, {})])
    client.get_json("/obs/puntual/series")
    url, _ = client.session.calls[0]
    assert url == "https://example.test/a5/obs/puntual/series"


def test_token_sets_bearer_header():
    session = FakeSession()
    A5Client("https://example.test/a5", token="secret", session=session)
    assert session.headers["Authorization"] == "Bearer secret"


def test_no_token_sets_no_header():
    session = FakeSession()
    A5Client("https://example.test/a5", token="", session=session)
    assert "Authorization" not in session.headers


def test_clone_gets_its_own_session(make_client):
    """One session per thread; sharing one across a pool is the original bug."""
    client = make_client([FakeResponse(200, {})])
    other = client.clone()
    assert other.session is not client.session
    assert other.base_url == client.base_url


def test_with_retry_overrides_only_what_is_given(make_client):
    client = make_client([FakeResponse(200, {})], attempts=3, base_delay=2.0)
    tweaked = client.with_retry(attempts=1)
    assert tweaked.retry.attempts == 1
    assert tweaked.retry.base_delay == 2.0


@pytest.mark.parametrize(
    "header,expected",
    [("5", 5.0), ("0", 0.0), ("  12  ", 12.0), (None, None), ("", None), ("soon", None)],
)
def test_parse_retry_after(header, expected):
    assert _parse_retry_after(header) == expected
