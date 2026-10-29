"""The DuckDB store: loading, keying, and the river-today query."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from ina_a5 import DailyValue, SeriesCache

from parana_crecida import gauges as roster
from parana_crecida.store import Store
from parana_crecida.thresholds import STATUS_OFFICIAL, Threshold


@pytest.fixture
def store():
    with Store(":memory:") as s:
        s.load_gauges()
        yield s


@pytest.fixture
def cache(tmp_path):
    return SeriesCache(tmp_path / "cache")


def values(start: date, *vals: float) -> list[DailyValue]:
    return [
        DailyValue(day=start + timedelta(days=i), value=float(v))
        for i, v in enumerate(vals)
    ]


# -- gauges ----------------------------------------------------------------


def test_load_gauges_covers_the_roster(store):
    assert store.con.execute("SELECT count(*) FROM gauge").fetchone()[0] == len(
        roster.GAUGES
    )


def test_river_order_follows_the_stem(store):
    rows = store.con.execute(
        "SELECT name FROM gauge WHERE river_order IS NOT NULL ORDER BY river_order"
    ).fetchall()
    names = [r[0] for r in rows]
    assert names[0] == "Puerto Iguazu"
    assert names[-1] == "Olivos"
    assert names.index("Corrientes") < names.index("Rosario")


def test_flow_gauges_are_off_the_stem(store):
    """m3/s does not belong on a stage profile."""
    order = store.con.execute(
        "SELECT river_order FROM gauge WHERE series_id = '26685'"
    ).fetchone()[0]
    assert order is None


def test_display_names_carry_accents(store):
    display = store.con.execute(
        "SELECT display FROM gauge WHERE series_id = '26251'"
    ).fetchone()[0]
    assert display == "Puerto Iguazú"


# -- observations ----------------------------------------------------------


def test_load_observations(store, cache):
    cache.write(26261, values(date(2026, 10, 25), 7.1, 7.2, 7.35))
    series, rows = store.load_observations(cache, ["26261"])
    assert (series, rows) == (1, 3)
    assert store.row_count() == 3
    assert store.last_data_day() == date(2026, 10, 27)


def test_reloading_replaces_rather_than_duplicates(store, cache):
    """Re-running after a top-up must not double-count."""
    cache.write(26261, values(date(2026, 10, 25), 7.1, 7.2))
    store.load_observations(cache, ["26261"])
    cache.write(26261, values(date(2026, 10, 25), 7.1, 7.2, 7.35))
    store.load_observations(cache, ["26261"])
    assert store.row_count() == 3


def test_a_series_with_nothing_cached_is_skipped_not_faked(store, cache):
    series, rows = store.load_observations(cache, ["26261"])
    assert (series, rows) == (0, 0)


def test_duplicate_days_are_structurally_impossible(store, cache):
    cache.write(26261, values(date(2026, 10, 25), 7.1))
    store.load_observations(cache, ["26261"])
    import duckdb

    with pytest.raises(duckdb.ConstraintException):
        store.con.execute(
            "INSERT INTO observation VALUES ('26261', DATE '2026-10-25', 9.9)"
        )


def test_load_run_is_recorded(store, cache):
    cache.write(26261, values(date(2026, 10, 25), 7.1))
    store.load_observations(cache, ["26261"], note="test")
    row = store.con.execute("SELECT series, rows, note FROM load_run").fetchone()
    assert row == (1, 1, "test")


def test_series_range(store, cache):
    cache.write(26261, values(date(2026, 10, 25), 7.1, 9.0, 3.0))
    store.load_observations(cache, ["26261"])
    first, last, lo, hi = store.series_range("26261")
    assert (first, last) == (date(2026, 10, 25), date(2026, 10, 27))
    assert (lo, hi) == (3.0, 9.0)
    assert store.series_range("99999") is None


# -- thresholds ------------------------------------------------------------


def test_load_thresholds(store):
    loaded = {
        "26261": Threshold("26261", "m", 6.5, 7.0, STATUS_OFFICIAL, "Corrientes"),
    }
    assert store.load_thresholds(loaded) == 1
    assert store.con.execute("SELECT alert FROM threshold").fetchone()[0] == 6.5


# -- river today -----------------------------------------------------------


def test_river_today_joins_level_change_and_thresholds(store, cache):
    cache.write(26261, values(date(2026, 10, 25), 7.10, 7.20, 7.35))
    store.load_observations(cache, ["26261"])
    store.load_thresholds(
        {"26261": Threshold("26261", "m", 6.5, 7.0, STATUS_OFFICIAL, "Corrientes")}
    )
    row = next(g for g in store.river_today() if g.series_id == "26261")
    assert row.name == "Corrientes"
    assert row.day == date(2026, 10, 27)
    assert row.value == pytest.approx(7.35)
    assert row.change == pytest.approx(0.15)
    assert row.change_days == 1
    assert (row.alert, row.evacuation) == (6.5, 7.0)


def test_change_spans_the_real_gap_not_an_assumed_day(store, cache):
    """A two-day gap must not be reported as a one-day rise."""
    cache.write(
        26261,
        [
            DailyValue(day=date(2026, 10, 20), value=7.0),
            DailyValue(day=date(2026, 10, 27), value=7.5),
        ],
    )
    store.load_observations(cache, ["26261"])
    row = next(g for g in store.river_today() if g.series_id == "26261")
    assert row.change == pytest.approx(0.5)
    assert row.change_days == 7


def test_a_single_reading_has_no_change(store, cache):
    """None, not zero: a gap is not a river that did not move."""
    cache.write(26261, values(date(2026, 10, 27), 7.35))
    store.load_observations(cache, ["26261"])
    row = next(g for g in store.river_today() if g.series_id == "26261")
    assert row.change is None
    assert row.change_days is None


def test_river_today_lists_every_stem_gauge_even_with_no_data(store):
    rows = store.river_today()
    assert len(rows) == len(roster.main_stem_sequence())
    assert all(r.value is None for r in rows)


def test_river_today_is_in_river_order(store):
    rows = store.river_today()
    assert [r.river_order for r in rows] == sorted(r.river_order for r in rows)
    assert rows[0].name == "Puerto Iguazú"
