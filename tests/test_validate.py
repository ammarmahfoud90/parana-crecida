"""The data tests, and the line between blocking and warning."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from ina_a5 import DailyValue, SeriesCache

from parana_crecida.store import Store
from parana_crecida.thresholds import STATUS_OFFICIAL, Threshold
from parana_crecida.validate import validate

TODAY = date(2026, 10, 28)
END = date(2026, 10, 27)


@pytest.fixture
def cache(tmp_path):
    return SeriesCache(tmp_path / "cache")


@pytest.fixture
def store():
    with Store(":memory:") as s:
        s.load_gauges()
        yield s


def plausible(n=500, *, end=END, base=3.0):
    """A believable stage series ending at ``end``.

    Ends at a real date by default: a fixture that runs into next year
    would trip the future-reading check and mask whatever it was testing.
    """
    return [
        DailyValue(day=end - timedelta(days=n - 1 - i), value=base + (i % 50) * 0.01)
        for i in range(n)
    ]


def loaded(store, cache, series_id="26261", values=None, **kw):
    cache.write(series_id, values if values is not None else plausible(**kw))
    store.load_observations(cache, [series_id])
    return store


def check(result, name):
    return next(c for c in result.checks if c.name == name)


# -- the empty case --------------------------------------------------------


def test_an_empty_store_fails_rather_than_passing_vacuously(store):
    """The most dangerous possible green light."""
    result = validate(store, today=TODAY)
    assert not result.ok
    assert check(result, "rows_present").blocking
    assert len(result.checks) == 1


# -- a healthy store -------------------------------------------------------


def test_a_healthy_store_passes(store, cache):
    loaded(store, cache)
    result = validate(store, today=TODAY)
    assert result.ok
    assert check(result, "no_duplicate_days").passed
    assert check(result, "data_is_fresh").passed


# -- blocking failures -----------------------------------------------------


def test_a_future_reading_blocks(store, cache):
    """A reading dated tomorrow means a timezone bug, not a fast river."""
    loaded(store, cache, values=plausible(500, end=TODAY + timedelta(days=5)))
    result = validate(store, today=TODAY)
    assert check(result, "no_future_days").blocking
    assert not result.ok


def test_a_whole_series_in_the_wrong_unit_blocks(store, cache):
    """Discharge filed as stage: Puerto Pilcomayo, before it was flagged."""
    end = date(2026, 10, 27)
    flows = [
        DailyValue(day=end - timedelta(days=i), value=1800.0 + i)
        for i in range(500)
    ]
    loaded(store, cache, "26262", flows)  # Barranqueras is a stage gauge
    result = validate(store, today=TODAY)
    assert check(result, "values_in_sane_range").blocking
    assert not result.ok


def test_a_nonfinite_value_blocks(store, cache):
    loaded(store, cache)
    store.con.execute(
        "INSERT INTO observation VALUES ('26261', DATE '2020-01-01', 'NaN'::DOUBLE)"
    )
    result = validate(store, today=TODAY)
    assert check(result, "values_are_finite").blocking


def test_a_threshold_in_the_wrong_unit_blocks(store, cache):
    """A 5 m alert on an m3/s gauge would mark every day catastrophic."""
    loaded(store, cache)
    store.load_thresholds(
        {"26685": Threshold("26685", "m", 5.35, 6.0, STATUS_OFFICIAL)}
    )
    result = validate(store, today=TODAY)
    assert check(result, "threshold_units_match").blocking


# -- warnings, which must not block ---------------------------------------


def test_one_bad_historical_row_warns_but_does_not_block(store, cache):
    """A glitch in INA's archive should not take the site down."""
    values = plausible(500)
    values[10] = DailyValue(day=values[10].day, value=78.91)  # Libertador's real one
    loaded(store, cache, values=values)
    result = validate(store, today=TODAY)
    assert result.ok
    assert not check(result, "values_in_sane_range").blocking
    assert not check(result, "no_isolated_outliers").passed


def test_stale_data_warns_but_does_not_block(store, cache):
    """If INA is down, keep showing the last good reading with its date."""
    loaded(store, cache, values=plausible(500, end=date(2024, 1, 1)))
    result = validate(store, today=TODAY)
    assert result.ok
    fresh = check(result, "data_is_fresh")
    assert not fresh.passed and not fresh.blocking


def test_missing_thresholds_only_warn(store, cache):
    loaded(store, cache, values=plausible(500))
    result = validate(store, today=TODAY)
    assert not check(result, "thresholds_loaded").passed
    assert result.ok


def test_some_missing_gauges_warn_but_all_missing_blocks(store, cache):
    """One silent gauge is normal; every gauge silent is an outage."""
    partial = validate(loaded(store, cache), today=TODAY)
    assert not check(partial, "required_gauges_present").passed
    assert not check(partial, "required_gauges_present").blocking

    with Store(":memory:") as empty:
        empty.load_gauges()
        empty.con.execute(
            "INSERT INTO observation VALUES ('26652', DATE '2026-10-27', 1800.0)"
        )
        result = validate(empty, today=TODAY)
    assert check(result, "required_gauges_present").blocking


# -- result plumbing -------------------------------------------------------


def test_lines_mark_failures_and_warnings(store, cache):
    loaded(store, cache, values=plausible(500, end=date(2024, 1, 1)))
    text = "\n".join(validate(store, today=TODAY).lines())
    assert "[ok  ]" in text
    assert "[warn]" in text
