"""Flooded area from Sentinel-1.

The Earth Engine calls need a credential a test runner does not have, so
those skip. What is tested here is everything that decides what the page
claims: the corridor definition, the summary, and above all the calibration
- that the method still reproduces an independent measurement.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from parana_crecida import flooded as fl


def table(**overrides):
    base = {
        "schema": 1,
        "source": fl.S1,
        "method": {"threshold_db": -16.0, "speckle_radius_m": 50,
                   "buffer_m": 20000, "scale_m": 60},
        "fetched_on": "2026-10-29",
        "window": {"start": "2026-05-02", "end": "2026-10-27"},
        "stretches": [{
            "key": "corrientes_goya",
            "from_name": "Corrientes", "to_name": "Goya",
            "river_km": 212.4, "corridor_km2": 9474,
            "permanent_water_km2": 520.8, "baseline_km2": 536.3,
            "baseline_window": {"start": "2021-08-01", "end": "2021-09-15"},
            "days": ["2026-10-01", "2026-10-13", "2026-10-25"],
            "km2": [575.5, 489.7, 1651.3],
            "images": [3, 3, 3],
        }],
    }
    base.update(overrides)
    return base


# -- the frozen corridor ---------------------------------------------------

CORRIDORS = fl.load_corridors()


@pytest.mark.skipif(not CORRIDORS, reason="no committed corridors")
def test_the_corridor_runs_corrientes_to_goya():
    stretch = CORRIDORS[0]
    assert stretch.key == "corrientes_goya"
    assert (stretch.from_id, stretch.to_id) == ("26261", "26265")


@pytest.mark.skipif(not CORRIDORS, reason="no committed corridors")
def test_the_centreline_matches_the_chain():
    """It is walked from the same topology, so the two must agree."""
    from parana_crecida.chain import load_chain

    nodes = {n.series_id: n for n in load_chain()}
    stretch = CORRIDORS[0]
    expected = nodes[stretch.to_id].river_km - nodes[stretch.from_id].river_km
    assert stretch.river_km == pytest.approx(expected, abs=5)


@pytest.mark.skipif(not CORRIDORS, reason="no committed corridors")
def test_the_centreline_is_an_ordered_run_of_coordinates():
    pts = CORRIDORS[0].centreline
    assert len(pts) > 50
    assert all(len(p) == 2 for p in pts)
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    # Somewhere in the Argentine Litoral, heading south.
    assert -60 < min(lons) and max(lons) < -58
    assert -30 < min(lats) and max(lats) < -27
    assert lats[0] > lats[-1], "the centreline should run downstream"


def test_load_corridors_of_a_missing_file_is_empty(tmp_path):
    assert fl.load_corridors(tmp_path / "nope.json") == []


# -- summarising -----------------------------------------------------------


def test_summarise_reports_the_latest_pass():
    out = fl.summarise(table())[0]
    assert out["latest"] == {"day": "2026-10-25", "km2": 1651.3}


def test_summarise_reports_water_above_the_baseline():
    """The number the page leads with: how much more than low water."""
    out = fl.summarise(table())[0]
    assert out["extra_km2"] == pytest.approx(1651.3 - 536.3, abs=0.1)


def test_summarise_finds_the_peak():
    out = fl.summarise(table())[0]
    assert out["peak"] == {"day": "2026-10-25", "km2": 1651.3}


def test_summarise_of_a_stretch_with_no_passes():
    out = fl.summarise(table(stretches=[{
        "key": "x", "from_name": "A", "to_name": "B", "river_km": 1,
        "corridor_km2": 1, "baseline_km2": 10.0, "permanent_water_km2": 9.0,
        "days": [], "km2": [],
    }]))[0]
    assert out["latest"] is None
    assert out["extra_km2"] is None
    assert out["peak"] is None


def test_summarise_without_a_baseline_cannot_claim_extra_water():
    out = fl.summarise(table(stretches=[{
        "key": "x", "from_name": "A", "to_name": "B", "river_km": 1,
        "corridor_km2": 1, "baseline_km2": None, "permanent_water_km2": 9.0,
        "days": ["2026-10-25"], "km2": [100.0],
    }]))[0]
    assert out["extra_km2"] is None


# -- storing ---------------------------------------------------------------


def test_table_round_trip(tmp_path):
    path = tmp_path / "flooded.json"
    fl.write_table(path, table())
    assert fl.load_table(path)["stretches"][0]["key"] == "corrientes_goya"


def test_load_table_of_a_missing_file_is_empty(tmp_path):
    assert fl.load_table(tmp_path / "nope.json") == {}


def test_stale_days():
    assert fl.stale_days(table(), date(2026, 11, 8)) == 10
    assert fl.stale_days({}, date(2026, 11, 8)) is None


# -- the committed measurement ---------------------------------------------

COMMITTED = fl.load_table()


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_the_threshold_still_reproduces_jrc_permanent_water():
    """The calibration, asserted rather than trusted.

    The fixed -16 dB threshold was chosen because over the 2021 bajante it
    returns the same area JRC independently maps as permanent water. If a
    change to the method breaks that agreement, this fails.
    """
    for stretch in COMMITTED["stretches"]:
        baseline = stretch["baseline_km2"]
        permanent = stretch["permanent_water_km2"]
        assert baseline == pytest.approx(permanent, rel=0.10), (
            f"{stretch['key']}: bajante {baseline:.0f} km2 against JRC "
            f"permanent {permanent:.0f} km2 - the threshold has drifted"
        )


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_the_committed_threshold_is_the_calibrated_one():
    assert COMMITTED["method"]["threshold_db"] == fl.WATER_THRESHOLD_DB


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_every_pass_is_physically_possible():
    for stretch in COMMITTED["stretches"]:
        for day, km2 in zip(stretch["days"], stretch["km2"]):
            assert 0 < km2 <= stretch["corridor_km2"], f"{day}: {km2} km2"


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_no_pass_falls_far_below_permanent_water():
    """A value under the permanent river means partial coverage got through.

    A pass covering only part of the corridor leaves the rest unobserved,
    and unobserved ground reduces to zero water -- which reads as the river
    shrinking below its own channel. The coverage filter exists to stop
    that, and this is the assertion that it worked.
    """
    for stretch in COMMITTED["stretches"]:
        floor = stretch["permanent_water_km2"] * 0.7
        low = [(d, v) for d, v in zip(stretch["days"], stretch["km2"]) if v < floor]
        assert not low, f"{stretch['key']}: passes below the permanent river: {low}"


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_passes_are_ordered_and_aligned():
    for stretch in COMMITTED["stretches"]:
        assert len(stretch["days"]) == len(stretch["km2"])
        assert stretch["days"] == sorted(stretch["days"])


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_the_window_stops_short_of_today():
    end = date.fromisoformat(COMMITTED["window"]["end"])
    fetched = date.fromisoformat(COMMITTED["fetched_on"])
    assert (fetched - end).days >= 1


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_the_committed_table_is_small_enough_to_commit():
    assert len(json.dumps(COMMITTED)) < 100_000
