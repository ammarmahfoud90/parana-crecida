"""Basin rainfall.

The Earth Engine calls need a credential a CI runner does not have, so
those tests skip there. Everything that shapes what the page says - the
summary, the staleness, the basin definitions and the committed table -
is tested without one.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from parana_crecida import rainfall as rf


def payload(**overrides):
    base = {
        "schema": 1,
        "source": rf.IMERG,
        "fetched_on": "2026-10-29",
        "window": {"start": "2026-04-30", "end": "2026-10-27"},
        "lag_days": 2,
        "basins": [
            {
                "key": "iguazu",
                "sub_basins": 9,
                "area_km2": 67536,
                "days": ["2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"],
                "mm": [11.81, 28.27, 30.97, 88.58],
            }
        ],
    }
    base.update(overrides)
    return base


# -- basin definitions -----------------------------------------------------


def test_the_two_basins_are_the_arms_that_meet_at_puerto_iguazu():
    keys = {b.key for b in rf.BASINS}
    assert keys == {"alto_parana", "iguazu"}


def test_published_areas_are_recorded_for_checking_against():
    by_key = {b.key: b for b in rf.BASINS}
    assert by_key["alto_parana"].published_km2 == pytest.approx(834_184, abs=1)
    assert by_key["iguazu"].published_km2 == pytest.approx(67_537, abs=1)


def test_the_two_basins_have_different_seeds():
    seeds = [b.seed for b in rf.BASINS]
    assert len(set(seeds)) == len(seeds)


# -- summarising -----------------------------------------------------------


def test_summarise_totals_the_recent_days():
    out = rf.summarise(payload(), days=3)
    # Totals are rounded to 0.1 mm, which is finer than the data deserves.
    assert out[0]["recent_mm"] == pytest.approx(28.27 + 30.97 + 88.58, abs=0.05)
    assert out[0]["recent_days"] == 3


def test_summarise_finds_the_wettest_day():
    out = rf.summarise(payload())
    assert out[0]["wettest_day"] == {"day": "2026-10-24", "mm": 88.58}


def test_summarise_ignores_gaps_rather_than_counting_them_as_dry():
    """A missing day is not a day with no rain."""
    out = rf.summarise(payload(basins=[{
        "key": "iguazu", "sub_basins": 9, "area_km2": 67536,
        "days": ["2026-10-23", "2026-10-24"], "mm": [None, 10.0],
    }]), days=2)
    assert out[0]["recent_mm"] == pytest.approx(10.0)
    assert out[0]["wettest_day"]["mm"] == 10.0


def test_summarise_of_an_all_empty_basin():
    out = rf.summarise(payload(basins=[{
        "key": "iguazu", "sub_basins": 9, "area_km2": 1,
        "days": ["2026-10-23"], "mm": [None],
    }]))
    assert out[0]["recent_mm"] is None
    assert out[0]["wettest_day"] is None


def test_summarise_carries_the_series_through_for_plotting():
    out = rf.summarise(payload())
    assert len(out[0]["days"]) == len(out[0]["mm"]) == 4


# -- staleness -------------------------------------------------------------


def test_stale_days_counts_from_the_fetch():
    """This one really does go stale, unlike the chain or the travel times."""
    assert rf.stale_days(payload(), date(2026, 11, 3)) == 5
    assert rf.stale_days(payload(), date(2026, 10, 29)) == 0


def test_stale_days_without_a_fetch_date():
    assert rf.stale_days({}, date(2026, 10, 29)) is None


# -- storing ---------------------------------------------------------------


def test_table_round_trip(tmp_path):
    path = tmp_path / "rain.json"
    rf.write_table(path, payload())
    loaded = rf.load_table(path)
    assert loaded["source"] == rf.IMERG
    assert loaded["basins"][0]["key"] == "iguazu"


def test_load_table_of_a_missing_file_is_empty_not_an_error(tmp_path):
    assert rf.load_table(tmp_path / "nope.json") == {}


# -- the committed table ---------------------------------------------------

COMMITTED = rf.load_table()


@pytest.mark.skipif(not COMMITTED, reason="no committed rainfall")
def test_the_committed_table_covers_both_basins():
    assert {b["key"] for b in COMMITTED["basins"]} == {"alto_parana", "iguazu"}


@pytest.mark.skipif(not COMMITTED, reason="no committed rainfall")
def test_the_committed_areas_match_what_hydrosheds_publishes():
    """The topology walk is only trustworthy if it reproduces the area."""
    published = {b.key: b.published_km2 for b in rf.BASINS}
    for basin in COMMITTED["basins"]:
        assert basin["area_km2"] == pytest.approx(published[basin["key"]], rel=0.01)


@pytest.mark.skipif(not COMMITTED, reason="no committed rainfall")
def test_the_committed_values_are_physically_plausible():
    for basin in COMMITTED["basins"]:
        values = [v for v in basin["mm"] if v is not None]
        assert values, f"{basin['key']} has no readings"
        assert min(values) >= 0, "negative rainfall"
        # A basin-average day above 200 mm would mean the reduction is wrong.
        assert max(values) < 200, f"{basin['key']} peaks at {max(values)} mm/day"


@pytest.mark.skipif(not COMMITTED, reason="no committed rainfall")
def test_the_small_basin_is_flashier_than_the_continental_one():
    """A 68,000 km2 basin averages a storm much less than an 834,000 km2 one."""
    peaks = {
        b["key"]: max(v for v in b["mm"] if v is not None)
        for b in COMMITTED["basins"]
    }
    assert peaks["iguazu"] > peaks["alto_parana"]


@pytest.mark.skipif(not COMMITTED, reason="no committed rainfall")
def test_the_committed_days_line_up_with_the_values():
    for basin in COMMITTED["basins"]:
        assert len(basin["days"]) == len(basin["mm"])
        assert basin["days"] == sorted(basin["days"])


@pytest.mark.skipif(not COMMITTED, reason="no committed rainfall")
def test_the_committed_window_stops_short_of_today():
    """IMERG lags by about half a day; asking for today returns a part-day."""
    assert COMMITTED["lag_days"] >= 1
    end = date.fromisoformat(COMMITTED["window"]["end"])
    fetched = date.fromisoformat(COMMITTED["fetched_on"])
    assert (fetched - end).days >= COMMITTED["lag_days"]


@pytest.mark.skipif(not COMMITTED, reason="no committed rainfall")
def test_the_committed_table_is_small_enough_to_commit():
    assert len(json.dumps(COMMITTED)) < 200_000
