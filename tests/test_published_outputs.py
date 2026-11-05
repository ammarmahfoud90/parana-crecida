"""What is actually published, checked against what it claims to be.

Every other test file exercises a function. This one reads the files the
site serves and asks whether they hold up: right shape, real dates, stated
units, traceable provenance, no missing value quietly turned into a zero,
no unvalidated reach inside a total.

They run against whatever is committed. That is the point - the defects
this project has met were not in the functions, they were in the artefacts
the functions produced, and nothing read those. The published total was
17.7% too large for a week; a sensor glitch of 78.91 m was served in a
downloadable CSV with a percentile of 100 attached; a withdrawn forecast
went on being published at a public URL for months after the page stopped
rendering it. Each was visible in a file that no test opened.

Where a file is absent the check skips rather than fails: a fresh clone
has no exports, and the Earth Engine products need a credential.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import date, datetime
from pathlib import Path

import pytest

SITE = Path("site/data")
DATA = Path("data")


def load(path: Path):
    if not path.exists():
        pytest.skip(f"{path} not published")
    return json.loads(path.read_text(encoding="utf-8"))


def iso(value) -> date:
    """A date, or a failure naming the value that was not one."""
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        pytest.fail(f"{value!r} is not an ISO date")


# -- shape and provenance --------------------------------------------------


def test_the_water_surface_carries_its_method_and_parameters():
    """A figure that travels without its method is not a measurement.

    Every parameter that decides what counts as water has to reach the
    published file, because the page quotes them and a reader has no other
    way to know what was computed.
    """
    payload = load(SITE / "floodwater.json")
    from parana_crecida import floodwater as fw

    assert payload["method"] == "vh-threshold/hand"
    assert payload["vh_threshold_db"] == fw.VH_THRESHOLD_DB
    assert payload["hand_limit_m"] == fw.HAND_LIMIT_M
    assert payload["window_days"] == fw.WINDOW_DAYS
    iso(payload["fetched_on"])
    # The note has to name the sensor and the band, not just "radar".
    note = (payload.get("note") or "").lower()
    assert "sentinel-1" in note and "vh" in note


def test_every_published_payload_dates_itself():
    """Undated data is unfalsifiable: nobody can tell stale from current."""
    for name, field in (
        ("river_today.json", "generated_at"),
        ("river_stage.json", "generated_at"),
        ("wave.json", "generated_at"),
        ("drivers.json", "generated_at"),
        ("track.json", "generated_for"),
        ("map_frames.json", "generated_for"),
        ("river_film.json", "generated_for"),
        ("floodwater.json", "fetched_on"),
    ):
        payload = load(SITE / name)
        stamp = payload.get(field)
        assert stamp, f"{name} has no {field}"
        # Both forms parse; a timestamp carries a zone or it is ambiguous.
        if "T" in str(stamp):
            parsed = datetime.fromisoformat(str(stamp))
            assert parsed.tzinfo is not None, f"{name}: {field} has no timezone"
        else:
            iso(stamp)


def test_the_gauge_payload_states_its_units_and_its_age():
    payload = load(SITE / "river_today.json")
    assert isinstance(payload["data_age_days"], int)
    iso(payload["last_data_day"])
    for gauge in payload["gauges"]:
        assert gauge["series_id"]
        assert gauge.get("unit") in (None, "m", "m3/s")
        if gauge.get("value") is not None:
            assert isinstance(gauge["value"], (int, float))
            assert math.isfinite(gauge["value"])


def test_no_published_number_is_nan_or_infinite():
    """They survive a float column and serialise as invalid JSON literals."""
    for path in sorted(SITE.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        for bad in ("NaN", "Infinity", "-Infinity"):
            assert bad not in text, f"{path.name} contains {bad}"


# -- dates --------------------------------------------------------------


def test_no_published_observation_is_dated_in_the_future():
    """A reading dated tomorrow is a timezone bug, not a fast river."""
    today = date.today()
    payload = load(SITE / "floodwater.json")
    for stretch in payload.get("stretches", []):
        for day in stretch.get("days", []):
            assert iso(day) <= today, f"{stretch['key']} has a cycle at {day}"
    for row in payload.get("river", []):
        assert iso(row["day"]) <= today


def test_cycle_dates_are_ordered_and_evenly_spaced():
    """They are a grid, so a hole or a reversal means something broke."""
    payload = load(SITE / "floodwater.json")
    window = payload.get("window_days") or 12
    for stretch in payload.get("stretches", []):
        days = [iso(d) for d in stretch.get("days", [])]
        assert days == sorted(days), f"{stretch['key']} cycles out of order"
        assert len(days) == len(set(days)), f"{stretch['key']} repeats a cycle"
        for a, b in zip(days, days[1:]):
            gap = (b - a).days
            assert gap % window == 0, (
                f"{stretch['key']}: {a} to {b} is {gap} days, not a multiple "
                f"of the {window}-day grid"
            )


def test_the_series_arrays_line_up():
    """days, water and flood are parallel arrays and must stay parallel.

    Where they drift, every figure after the drift is attributed to the
    wrong date and nothing downstream would notice.
    """
    payload = load(SITE / "floodwater.json")
    for stretch in payload.get("stretches", []):
        n = len(stretch.get("days", []))
        assert len(stretch.get("water_km2", [])) == n, stretch["key"]
        assert len(stretch.get("flood_km2", [])) == n, stretch["key"]
        observation = stretch.get("observation")
        if observation is not None:
            assert len(observation) == n, stretch["key"]


# -- missing values ------------------------------------------------------


def test_no_published_area_is_a_zero_standing_in_for_a_gap():
    """The rule that must not be broken, checked on the artefact.

    A corridor of several thousand km2 containing a permanent river never
    holds zero water. A zero here is a reduction over ground nobody
    observed, which is what `area_km2` used to return.
    """
    payload = load(SITE / "floodwater.json")
    for stretch in payload.get("stretches", []):
        for day, km2 in zip(stretch.get("days", []), stretch.get("water_km2", [])):
            assert km2 is not None, f"{stretch['key']}@{day} is null in a series"
            assert km2 > 0, (
                f"{stretch['key']}@{day} reports {km2} km2 of water in a "
                f"{stretch.get('corridor_km2')} km2 corridor with a river in it"
            )
    for row in payload.get("river", []):
        assert row["water_km2"] > 0, f"river@{row['day']} reports no water at all"


def test_a_missing_cycle_is_absent_and_named_rather_than_zeroed():
    """Gaps are published with a reason, not silently dropped or filled."""
    payload = load(SITE / "floodwater.json")
    gaps = list(payload.get("river_gaps") or [])
    for stretch in payload.get("stretches", []):
        gaps.extend(stretch.get("gaps") or [])
    for gap in gaps:
        iso(gap["day"])
        assert gap.get("reason"), f"gap at {gap['day']} gives no reason"
        # A gap is a gap, not a cycle with a number on it.
        assert "water_km2" not in gap


# -- area and aggregation ------------------------------------------------


def test_flood_water_never_exceeds_all_water():
    """Flood is all water minus a fixed mask, so it cannot be larger."""
    payload = load(SITE / "floodwater.json")
    for stretch in payload.get("stretches", []):
        for day, total, beyond in zip(stretch.get("days", []),
                                      stretch.get("water_km2", []),
                                      stretch.get("flood_km2", [])):
            assert beyond <= total + 1e-6, f"{stretch['key']}@{day}: {beyond} > {total}"
    for row in payload.get("river", []):
        assert row["flood_km2"] <= row["water_km2"] + 1e-6, row["day"]


def test_no_reach_reports_more_water_than_its_corridor_holds():
    payload = load(SITE / "floodwater.json")
    for stretch in payload.get("stretches", []):
        extent = stretch.get("corridor_km2")
        if not extent:
            continue
        assert max(stretch.get("water_km2") or [0]) <= extent + 1e-6, stretch["key"]


def test_the_permanent_channel_is_present_in_every_cycle():
    """It is a fixed mask over a river that never leaves it.

    water minus flood is detection inside that mask. If it collapses on a
    cycle, the mask or the coverage failed - that is the signature of the
    one bad cycle in the published record, and export._implausible exists
    to keep such a cycle out of the series.
    """
    payload = load(SITE / "floodwater.json")
    river = payload.get("river") or []
    if len(river) < 3:
        pytest.skip("too few whole-river cycles to judge")
    channel = sorted(r["water_km2"] - r["flood_km2"] for r in river)
    median = channel[len(channel) // 2]
    from parana_crecida.export import MIN_CHANNEL_FRACTION

    for row in river:
        share = (row["water_km2"] - row["flood_km2"]) / median
        assert share >= MIN_CHANNEL_FRACTION, (
            f"{row['day']} found {share:.0%} of the median channel detection"
        )


def test_the_whole_river_total_is_not_the_sum_of_the_stretches():
    """The corridors overlap by 17.7% of their summed area.

    Once the union measurement lands, a river row carries the list of
    stretches it covers; until then it carries a count, and the page warns
    that the figure is summed. Either way it must never be published as a
    union figure while being a sum.
    """
    payload = load(SITE / "floodwater.json")
    river = payload.get("river") or []
    if not river:
        pytest.skip("no whole-river series published")
    union_measured = isinstance(river[-1].get("stretches"), list)
    if not union_measured:
        # A summed series must not claim a union corridor area.
        assert payload.get("corridor_km2") is None or payload.get("corridor_note") is None
        return
    summed = sum(
        s["water_km2"][-1] for s in payload["stretches"] if s.get("water_km2")
    )
    assert river[-1]["water_km2"] < summed, (
        "the union total is not smaller than the sum of its parts, so it is "
        "probably still a sum"
    )


# -- what publishes, and what does not -----------------------------------


def test_no_unvalidated_reach_appears_in_a_published_total():
    """The rule the Delta exists to enforce."""
    payload = load(SITE / "floodwater.json")
    from parana_crecida.export import SKILL_FLOOR

    published = {s["key"] for s in payload.get("stretches", [])}
    withheld = {w["key"] for w in payload.get("withheld", [])}
    assert not (published & withheld), "a reach is both published and withheld"

    for stretch in payload.get("stretches", []):
        assert stretch.get("iou") is not None, f"{stretch['key']} publishes unscored"
        assert stretch["iou"] >= SKILL_FLOOR, stretch["key"]
        assert stretch.get("recall") is not None
        assert stretch.get("precision") is not None

    for row in payload.get("river", []):
        if isinstance(row.get("stretches"), list):
            assert set(row["stretches"]) <= published, row["day"]


def test_a_withheld_reach_carries_its_reason_and_no_numbers():
    payload = load(SITE / "floodwater.json")
    for withheld in payload.get("withheld", []):
        assert withheld.get("withheld"), f"{withheld['key']} gives no reason"
        for leaked in ("water_km2", "flood_km2", "days"):
            assert leaked not in withheld, f"{withheld['key']} leaks {leaked}"


def test_the_superseded_view_is_not_served_as_current():
    """It was: a different area for the same stretch, at a public URL."""
    path = SITE / "flooded.json"
    if not path.exists():
        return  # removed entirely, which is also fine
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload.get("superseded"), (
        "site/data/flooded.json is still served without saying it is the "
        "superseded VV mask; no page reads it and its areas are not "
        "comparable with floodwater.json's"
    )


# -- the CSVs, which are what gets downloaded and cited -------------------


def test_the_published_csv_carries_no_impossible_reading():
    """78.91 m at Libertador was published here with a percentile of 100."""
    path = SITE / "csv" / "river_daily.csv"
    if not path.exists():
        pytest.skip("no CSV published")
    from parana_crecida.validate import SANE_RANGES

    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows, "river_daily.csv is empty"
    # Each row is judged against its own unit. The file mixes stage and
    # discharge - five of the gauges are m3/s - and the column used to be
    # called level_m for both, so 8,800 m3/s sat under a header asserting
    # metres.
    assert "unit" in rows[0], "river_daily.csv does not say what its values are"
    for row in rows:
        iso(row["day"])
        unit = row["unit"]
        assert unit in SANE_RANGES, f"{row['gauge']}: unknown unit {unit!r}"
        low, high = SANE_RANGES[unit]
        value = float(row["value"])
        assert low <= value <= high, (
            f"{row['gauge']} on {row['day']}: {value} {unit} is outside the "
            f"range its unit allows and was published anyway"
        )
        if row["percentile_for_day_of_year"]:
            assert 0 <= float(row["percentile_for_day_of_year"]) <= 100


def test_the_flood_csv_carries_the_score_behind_every_row():
    """A figure that travels without its evidence stops being a measurement."""
    path = SITE / "csv" / "flooded_area.csv"
    if not path.exists():
        pytest.skip("no CSV published")
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if not rows:
        pytest.skip("no stretches published")
    from parana_crecida.export import SKILL_FLOOR

    for row in rows:
        iso(row["day"])
        assert row["iou"], f"{row['stretch']}@{row['day']} has no score"
        assert float(row["iou"]) >= SKILL_FLOOR
        assert float(row["water_km2"]) > 0


# -- the withdrawn forecast ----------------------------------------------


def test_no_arrival_date_is_published():
    """Measured at a 23% hit rate, withdrawn from the page, and still
    served in this file for months afterwards."""
    payload = load(SITE / "track.json")
    for projection in payload.get("projections", []):
        for field in ("expected", "earliest", "latest", "days_ahead", "timing"):
            assert field not in projection, (
                f"track.json still publishes {field}; the projections were "
                f"withdrawn at a 23% hit rate"
            )
    assert payload.get("schema", 1) >= 2
