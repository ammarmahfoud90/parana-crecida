"""Observation parsing, timezone handling and the truncation heuristic."""

from __future__ import annotations

from datetime import date

import pytest
from a5fakes import FakeResponse

from ina_a5 import (
    A5Truncated,
    extract_observations,
    fetch_daily,
    fetch_history,
    rows_to_daily,
)
from ina_a5.observations import parse_timestamp


def row(ts, value):
    return {"timestart": ts, "valor": value}


def obs(*rows):
    return FakeResponse(200, list(rows))


# -- response unwrapping ---------------------------------------------------


def test_extract_from_a_bare_list():
    rows = [row("2024-01-01T09:00:00Z", 1.0)]
    assert extract_observations(rows) == rows


@pytest.mark.parametrize("key", ["observaciones", "data", "rows", "result", "series", "values"])
def test_extract_from_each_wrapper_key(key):
    rows = [row("2024-01-01T09:00:00Z", 1.0)]
    assert extract_observations({key: rows}) == rows


def test_extract_from_a_nested_wrapper():
    rows = [row("2024-01-01T09:00:00Z", 1.0)]
    assert extract_observations({"result": {"data": rows}}) == rows


def test_extract_from_nothing():
    assert extract_observations(None) == []
    assert extract_observations([]) == []
    assert extract_observations({"unrelated": 1}) == []


# -- timestamps ------------------------------------------------------------


def test_offset_timestamp_keeps_its_local_day():
    stamp = parse_timestamp("2024-01-05T23:30:00-03:00")
    assert stamp is not None and stamp.date() == date(2024, 1, 5)


def test_utc_timestamp_is_converted_not_truncated():
    """01:00Z on the 6th is 22:00 on the 5th in Buenos Aires.

    The original code took ``str(ts)[:10]`` and so filed this under the 6th,
    shifting three hours of every night onto the wrong day.
    """
    stamp = parse_timestamp("2024-01-06T01:00:00Z")
    assert stamp is not None and stamp.date() == date(2024, 1, 5)


def test_naive_timestamp_is_read_as_local():
    stamp = parse_timestamp("2024-01-05T09:00:00")
    assert stamp is not None and stamp.date() == date(2024, 1, 5)


def test_date_only_timestamp():
    stamp = parse_timestamp("2024-01-05")
    assert stamp is not None and stamp.date() == date(2024, 1, 5)


@pytest.mark.parametrize("bad", [None, "", "nope", 17, "2024"])
def test_unparseable_timestamps(bad):
    assert parse_timestamp(bad) is None


# -- aggregation -----------------------------------------------------------


def test_sub_daily_rows_are_averaged_and_counted():
    values = rows_to_daily(
        [
            row("2024-01-05T03:00:00-03:00", 2.0),
            row("2024-01-05T09:00:00-03:00", 3.0),
            row("2024-01-05T15:00:00-03:00", 4.0),
        ]
    )
    assert len(values) == 1
    assert values[0].value == pytest.approx(3.0)
    assert values[0].n_raw == 3


def test_rows_are_sorted_and_missing_data_dropped():
    values = rows_to_daily(
        [
            row("2024-01-07T09:00:00-03:00", 7.0),
            row("2024-01-05T09:00:00-03:00", 5.0),
            row("2024-01-06T09:00:00-03:00", None),      # no value
            {"valor": 9.9},                               # no timestamp
            row("2024-01-08T09:00:00-03:00", "not a number"),
            "not even a row",
        ]
    )
    assert [v.day.day for v in values] == [5, 7]


def test_skip_days_removes_the_named_day():
    values = rows_to_daily(
        [row("2024-01-05T09:00:00-03:00", 5.0), row("2024-01-06T09:00:00-03:00", 6.0)],
        skip_days={date(2024, 1, 6)},
    )
    assert [v.day.day for v in values] == [5]


# -- fetch_daily -----------------------------------------------------------


def test_fetch_daily_drops_the_partial_today(make_client):
    """A day still filling up averages to a level the river never had."""
    client = make_client(
        [obs(row("2024-01-05T09:00:00-03:00", 5.0), row("2024-01-06T03:00:00-03:00", 6.0))],
        attempts=1,
    )
    values = fetch_daily(
        client, 26261, date(2024, 1, 1), date(2024, 1, 6), today=date(2024, 1, 6)
    )
    assert [v.day.day for v in values] == [5]


def test_fetch_daily_can_keep_today(make_client):
    client = make_client([obs(row("2024-01-06T03:00:00-03:00", 6.0))], attempts=1)
    values = fetch_daily(
        client,
        26261,
        date(2024, 1, 1),
        date(2024, 1, 6),
        drop_partial_today=False,
        today=date(2024, 1, 6),
    )
    assert [v.day.day for v in values] == [6]


def test_fetch_daily_sends_the_expected_params(make_client):
    client = make_client([obs()], attempts=1)
    fetch_daily(client, 26261, date(2024, 1, 1), date(2024, 3, 1), today=date(2024, 3, 2))
    _, params = client.session.calls[0]
    assert params["tipo"] == "puntual"
    assert params["series_id"] == 26261
    assert params["timestart"] == "2024-01-01T00:00:00Z"
    assert params["timeend"] == "2024-03-01T23:59:59Z"


def test_empty_window_returns_empty_not_error(make_client):
    client = make_client([obs()], attempts=1)
    assert fetch_daily(client, 26261, date(2024, 1, 1), date(2024, 1, 5)) == []


# -- fetch_history and truncation -----------------------------------------

START, END = date(2000, 1, 1), date(2024, 12, 31)


def many_rows(n, start_day=date(2000, 1, 1)):
    from datetime import timedelta

    return [
        row((start_day + timedelta(days=i)).isoformat() + "T09:00:00-03:00", float(i))
        for i in range(n)
    ]


def test_a_plausible_single_request_is_not_rechunked(make_client):
    client = make_client([obs(*many_rows(37))], attempts=1)
    values = fetch_history(client, 26261, START, END, today=date(2025, 1, 1))
    assert len(values) == 37
    assert len(client.session.calls) == 1


def test_a_round_row_count_triggers_chunking(make_client):
    """1000 rows is the shape of a server-side cap, not of a real record."""
    client = make_client(
        [obs(*many_rows(1000)), obs(*many_rows(3))], attempts=1
    )
    values = fetch_history(
        client, 26261, START, END, chunk_years=50, today=date(2025, 1, 1)
    )
    assert len(client.session.calls) == 2
    assert len(values) == 3


def test_a_late_first_row_triggers_chunking(make_client):
    """Asked for 2000 onward, got nothing before 2020: the start was ignored."""
    client = make_client(
        [obs(*many_rows(5, start_day=date(2020, 1, 1))), obs(*many_rows(4))],
        attempts=1,
    )
    values = fetch_history(
        client, 26261, START, END, chunk_years=50, today=date(2025, 1, 1)
    )
    assert len(client.session.calls) == 2
    assert len(values) == 4


def test_an_empty_long_window_triggers_chunking(make_client):
    client = make_client([obs(), obs(*many_rows(2))], attempts=1)
    values = fetch_history(
        client, 26261, START, END, chunk_years=50, today=date(2025, 1, 1)
    )
    assert len(values) == 2


def test_an_empty_short_window_is_believed(make_client):
    """A genuinely empty fortnight is data, not a truncated response."""
    client = make_client([obs()], attempts=1)
    values = fetch_history(
        client, 26261, date(2024, 1, 1), date(2024, 1, 15), today=date(2024, 2, 1)
    )
    assert values == []
    assert len(client.session.calls) == 1


def test_strict_mode_raises_when_chunking_did_not_help(make_client):
    client = make_client([obs(*many_rows(1000)), obs(*many_rows(1000))], attempts=1)
    with pytest.raises(A5Truncated):
        fetch_history(
            client,
            26261,
            START,
            END,
            chunk_years=50,
            strict=True,
            today=date(2025, 1, 1),
        )
