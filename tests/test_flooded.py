"""Flooded area from Sentinel-1.

The Earth Engine calls need a credential a test runner does not have, so
those skip. What is tested here is everything that decides what the page
claims: the corridor definition, the summary, and above all the calibration
- that the method still reproduces an independent measurement.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

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


BY_KEY = {c.key: c for c in CORRIDORS}


@pytest.mark.skipif(not CORRIDORS, reason="no committed corridors")
def test_the_corridors_run_the_frontier_to_the_delta():
    """Nine stretches, end to end, each starting where the last one ended."""
    assert len(CORRIDORS) == 9
    assert CORRIDORS[0].from_name == "Puerto Iguazu"
    assert CORRIDORS[-1].to_name == "Ibicuy"
    for upstream, downstream in zip(CORRIDORS, CORRIDORS[1:]):
        assert upstream.to_id == downstream.from_id, (
            f"{upstream.key} ends at {upstream.to_id} but "
            f"{downstream.key} starts at {downstream.from_id}"
        )


@pytest.mark.skipif("corrientes_goya" not in BY_KEY, reason="no committed corridors")
def test_the_corrientes_goya_stretch_is_unchanged():
    """The first stretch built, and the one every threshold was checked against."""
    stretch = BY_KEY["corrientes_goya"]
    assert (stretch.from_id, stretch.to_id) == ("26261", "26265")
    assert stretch.threshold_db == -16.0
    assert stretch.calibrated


@pytest.mark.skipif(not CORRIDORS, reason="no committed corridors")
def test_the_centrelines_match_the_chain():
    """They are walked from the same topology, so the two must agree.

    Not exactly, though. A stretch's stored length comes from walking its
    centreline vertices; the chain's comes from summing reach lengths
    between the two gauges. The walk runs 1 to 7% longer, consistently, on
    every stretch. The tolerance is set to catch a stretch wired to the
    wrong gauge - which would be wrong by a hundred km or more - not to
    pretend the two geometries are the same measurement.

    :mod:`parana_crecida.riverstage` resolves the difference by scaling each
    walk onto the chain's spacing, so the map's river-km come from the chain.
    """
    from parana_crecida.chain import load_chain

    nodes = {n.series_id: n for n in load_chain()}
    for stretch in CORRIDORS:
        expected = nodes[stretch.to_id].river_km - nodes[stretch.from_id].river_km
        assert stretch.river_km == pytest.approx(expected, rel=0.08), stretch.key
        assert stretch.river_km >= expected, (
            f"{stretch.key}: the vertex walk should not be shorter than the chain"
        )


@pytest.mark.skipif(not CORRIDORS, reason="no committed corridors")
def test_the_delta_stretch_publishes_no_area():
    """It calibrates on low water and falls apart on a flood.

    At -15.5 dB the 2021 bajante lands 8.2% from JRC, which would pass. But
    over six months of a record flood the series swings between 430 and
    1,326 km2 with no trend, and correlates -0.39 with the gauge at Ibicuy:
    the water area falls as the river rises. The six calibrated stretches
    upstream run +0.43 to +0.72 on the same test. A negative correlation is
    not a weak measurement, it is a measurement of something else.
    """
    delta = BY_KEY["villa_constitucion_ibicuy"]
    assert delta.calibrated is False
    assert "-0.39" in delta.calibration.get("reason", "")


@pytest.mark.skipif(not CORRIDORS, reason="no committed corridors")
def test_every_centreline_is_an_ordered_run_of_coordinates():
    for stretch in CORRIDORS:
        pts = stretch.centreline
        assert len(pts) > 50, stretch.key
        assert all(len(p) == 2 for p in pts), stretch.key
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        # Between the Brazil frontier and Villa Constitucion, heading south.
        assert -61 < min(lons) and max(lons) < -54, stretch.key
        assert -34 < min(lats) and max(lats) < -25, stretch.key
        assert lats[0] > lats[-1], f"{stretch.key}: should run downstream"


def test_load_corridors_of_a_missing_file_is_empty(tmp_path):
    assert fl.load_corridors(tmp_path / "nope.json") == []


# -- summarising -----------------------------------------------------------


def test_summarise_carries_the_calibration_to_the_page():
    """The method page states the calibration, so it must receive it.

    Otherwise the published threshold is whatever someone last typed into
    the page, which is exactly the kind of claim this project does not make.
    """
    out = fl.summarise(table(stretches=[{
        "key": "x", "from_name": "A", "to_name": "B", "river_km": 1,
        "corridor_km2": 1, "baseline_km2": 10.0, "permanent_water_km2": 9.0,
        "threshold_db": -16.5, "calibrated": False,
        "days": ["2026-10-25"], "km2": [100.0],
    }]))[0]
    assert out["threshold_db"] == -16.5
    assert out["calibrated"] is False


def test_an_uncalibrated_stretch_publishes_no_number_at_all():
    """Not a low number - no number.

    The gorge reaches went on reporting 190 km2 of water against a 192 km2
    bajante baseline, which reads as a record flood covering less ground
    than the worst drought in seventy-seven years. The figure was not low,
    it was meaningless: in a basalt gorge the threshold fits radar shadow
    rather than water. Withholding it only from the map's totals was not
    enough.
    """
    out = fl.summarise(table(stretches=[{
        "key": "gorge", "from_name": "A", "to_name": "B", "river_km": 189,
        "corridor_km2": 5000, "baseline_km2": 192.1,
        "permanent_water_km2": 148.5, "threshold_db": -17.0,
        "calibrated": False,
        "days": ["2026-10-27"], "km2": [189.9],
    }]))[0]
    assert out["latest"] is None
    assert out["extra_km2"] is None
    assert out["peak"] is None
    assert out["km2"] == [] and out["days"] == []
    assert out["baseline_km2"] is None
    assert out["withheld"] == "uncalibrated"
    # What it keeps: enough for a page to say nothing is claimed, and why.
    assert out["from_name"] == "A" and out["calibrated"] is False


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

    Each stretch's threshold was chosen because over the 2021 bajante it
    returns the same area JRC independently maps as permanent water. If a
    change to the method breaks that agreement, this fails.

    Only calibrated stretches are checked, because this agreement is exactly
    what "calibrated" means. The two gorge reaches above Posadas never
    reached it -- see the companion test below, which asserts that they are
    still excluded rather than quietly let through.
    """
    for stretch in COMMITTED["stretches"]:
        if not stretch.get("calibrated", True):
            continue
        baseline = stretch["baseline_km2"]
        permanent = stretch["permanent_water_km2"]
        assert baseline == pytest.approx(permanent, rel=0.10), (
            f"{stretch['key']}: bajante {baseline:.0f} km2 against JRC "
            f"permanent {permanent:.0f} km2 - the threshold has drifted"
        )


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_an_uncalibrated_stretch_publishes_no_number():
    """The gorge reaches show a picture and claim no area.

    Between Puerto Iguazu and Posadas the river runs in a basalt gorge. No
    threshold reproduced JRC there: the search landed on -20/-21 dB with a
    6.5 dB spread between neighbouring reaches, which is a threshold
    compensating for radar shadow on the valley walls rather than measuring
    water. Those stretches are marked uncalibrated, and this asserts the
    consequence: they are kept out of any published total.
    """
    frames = json.loads(
        Path("site/data/map_frames.json").read_text(encoding="utf-8")
    )
    uncalibrated = {
        s["key"] for s in frames["stretches"] if not s["calibrated"]
    }
    assert uncalibrated == {
        "puerto_iguazu_libertador",      # the gorge: radar shadow
        "libertador_posadas",            # the gorge
        "villa_constitucion_ibicuy",     # the Delta: tide and wind
    }

    for frame in frames["frames"]:
        if frame["total_km2"] is None:
            continue
        counted = sum(
            v for k, v in frame["km2"].items() if k not in uncalibrated
        )
        assert frame["total_km2"] == pytest.approx(counted, abs=0.5), (
            f"{frame['end']}: the total includes an uncalibrated stretch"
        )


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_each_stretch_carries_its_own_calibrated_threshold():
    """The threshold does not transfer between stretches, so each stores its own."""
    corridors = {c.key: c for c in fl.load_corridors()}
    for stretch in COMMITTED["stretches"]:
        assert stretch["threshold_db"] == corridors[stretch["key"]].threshold_db
        assert -18 <= stretch["threshold_db"] <= -14


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_what_is_published_is_all_or_nothing():
    """Every stretch either carries a calibrated measurement or carries none."""
    published = json.loads(
        Path("site/data/flooded.json").read_text(encoding="utf-8")
    )
    for stretch in published["stretches"]:
        if stretch.get("calibrated") is False:
            assert stretch["latest"] is None, stretch["key"]
            assert stretch["extra_km2"] is None, stretch["key"]
            assert stretch["peak"] is None, stretch["key"]
            assert not stretch["km2"], stretch["key"]
        else:
            assert stretch["latest"], stretch["key"]


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


# -- does the measurement track the river at all? --------------------------


@pytest.mark.skipif(not COMMITTED, reason="no committed flooded area")
def test_every_published_area_moves_with_the_river():
    """The check a passing calibration does not give you.

    Calibration asks one question: over the 2021 bajante, does the threshold
    reproduce the permanent water JRC maps? The Delta corridor answered yes
    - 277 km2 against 302, an 8.2% residual, comfortably inside the bar -
    and then produced a series that swings between 430 and 1,326 km2 over
    six months of a record flood, correlating -0.39 with the gauge at
    Ibicuy. The area falls as the river rises.

    A calibration that passes is necessary and not sufficient. This is the
    sufficient half: whatever is published must actually move with the
    river. The six calibrated stretches run +0.43 to +0.72; the bar is set
    only to catch a measurement pointing the wrong way, because one
    stretch (La Paz to Parana) sits near zero on a flood that barely moved
    it, and near zero is weak rather than wrong.
    """
    numpy = pytest.importorskip("numpy")
    from parana_crecida.store import DEFAULT_DB, Store

    if not Path(DEFAULT_DB).exists():
        pytest.skip("no store to read gauge levels from")

    downstream = {c.key: c.to_id for c in fl.load_corridors()}
    checked = 0
    with Store(DEFAULT_DB, read_only=True) as store:
        for stretch in COMMITTED["stretches"]:
            if not stretch.get("calibrated", True):
                continue
            series_id = downstream.get(stretch["key"])
            if series_id is None:
                continue
            levels = {v.day.isoformat(): v.value for v in store.series(series_id)}
            pairs = [
                (levels[day], km2)
                for day, km2 in zip(stretch["days"], stretch["km2"])
                if day in levels
            ]
            if len(pairs) < 8:
                continue
            checked += 1
            r = float(numpy.corrcoef(
                [p[0] for p in pairs], [p[1] for p in pairs]
            )[0, 1])
            assert r > -0.2, (
                f"{stretch['key']}: water area correlates {r:+.2f} with the "
                f"stage at {stretch['to_name']} - it is measuring something "
                "that is not the flood"
            )
    assert checked >= 4, f"only {checked} stretches could be checked"
