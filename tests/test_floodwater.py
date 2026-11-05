"""The water-surface export: what it totals, and what it refuses to total.

The product page leads with one number - how much of the valley is under
water - and that number is a sum. Three ways a sum like that lies, all of
which this project has already produced once:

- a stretch that did not report on a cycle is read as zero water, and the
  river appears to drop;
- a stretch whose measurement has never been checked is quietly added in,
  while the page says underneath that it publishes no figure;
- the reach quoted beside the total is not the reach that was summed.

The publishing rule under test is the one the validation forced. The old
rule asked whether a per-stretch threshold reproduced permanent water at
low flow, and on that test the two gorge stretches failed and were
withheld; scored against optical water during a flood they are the best
two reaches on the river. So what publishes now is what was measured to
work, and a stretch that was never measured does not publish however
plausible it looks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parana_crecida.export import SKILL_FLOOR, ExportBlocked, export_floodwater


class Passing:
    ok = True
    failures = ()


class Failing:
    ok = False

    class _Check:
        name = "a check"

    failures = (_Check(),)


DAYS = ["2026-09-10", "2026-09-22", "2026-10-04"]
GOOD = round(SKILL_FLOOR + 0.2, 2)


def write_source(
    tmp_path: Path, stretches: list[dict], river: list[dict] | None = None
) -> Path:
    path = tmp_path / "source.json"
    payload = {
        "schema": 3, "note": "n", "method": "vh-threshold/hand",
        "vh_threshold_db": -25.0, "hand_limit_m": 10.0, "window_days": 12,
        "min_coverage": 0.95, "fetched_on": "2026-10-30",
        "stretches": stretches,
    }
    if river is not None:
        payload["river"] = river
        payload["union_corridor_km2"] = 8000
        payload["union_note"] = "measured over the dissolved union"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def union(water, keys, days=None):
    """A river series as floodwater.rebuild measures it: over the union."""
    return [
        {"day": day, "window_start": day, "water_km2": value,
         "flood_km2": round(value / 2, 1), "seen_km2": 7900.0,
         "coverage": 0.99, "stretches": sorted(keys)}
        for day, value in zip(days or DAYS, water)
    ]


def write_skill(tmp_path: Path, scores: dict[str, float | None]) -> Path:
    """A validation table naming an IoU for each stretch that was scored."""
    path = tmp_path / "skill.json"
    path.write_text(json.dumps({
        "schema": 1,
        "measured_against": "Sentinel-2 MNDWI water, 22 October 2026",
        "stretches": [
            {"key": key, "from_name": key, "to_name": key, "clear_scenes": 5,
             "now": {"recall": 0.8, "precision": 0.8, "iou": iou},
             "previous": {"recall": 0.6, "precision": 0.6, "iou": 0.45}}
            for key, iou in scores.items()
        ],
        "recall": 0.8, "precision": 0.8, "iou": GOOD,
        "previous": {"method": "per-stretch VV threshold",
                     "recall": 0.6, "precision": 0.6, "iou": 0.45},
    }), encoding="utf-8")
    return path


def row(key: str, water: list[float], days: list[str] | None = None) -> dict:
    return {
        "key": key, "from_name": "a", "to_name": "b", "river_km": 100.0,
        "corridor_km2": 5000, "days": days or DAYS, "water_km2": water,
        "flood_km2": [round(v / 2, 1) for v in water],
    }


def run(tmp_path, stretches, scores, river=None):
    export_floodwater(
        Passing(),
        source_path=write_source(tmp_path, stretches, river),
        skill_path=write_skill(tmp_path, scores),
        out_dir=tmp_path,
    )
    return json.loads((tmp_path / "floodwater.json").read_text(encoding="utf-8"))


def test_the_river_total_is_not_the_sum_of_the_published_stretches(tmp_path):
    """The corridors overlap, so the sum counts the same channel twice.

    Each corridor is a 20 km buffer around a centreline, and neighbouring
    centrelines meet at a gauge - so every adjacent pair overlaps in a
    20 km-radius region centred on the river. Over the eight reaches that
    publish that is 9,889 km2, 17.7% of their summed area, all of it main
    channel. The whole-river figure is measured once over the dissolved
    union, and the sum is never used.
    """
    payload = run(
        tmp_path,
        [row("upper", [100.0, 110.0, 120.0]), row("lower", [200.0, 210.0, 220.0])],
        {"upper": GOOD, "lower": GOOD},
        river=union([260.0, 280.0, 300.0], ["upper", "lower"]),
    )
    # The union is smaller than the sum, and it is the union that publishes.
    assert [r["water_km2"] for r in payload["river"]] == [260.0, 280.0, 300.0]
    assert payload["latest"]["water_km2"] == 300.0
    assert payload["corridor_km2"] == 8000
    assert payload["river_unavailable"] is None


def test_without_a_union_measurement_no_whole_river_total_publishes(tmp_path):
    """A figure known to be 17.7% too high is worse than an absent one.

    No arithmetic recovers the union from the per-stretch figures, so a
    record that predates the union measurement publishes the stretches and
    refuses the total, with the reason attached.
    """
    payload = run(tmp_path,
                  [row("upper", [100.0, 110.0, 120.0]), row("lower", [200.0, 210.0, 220.0])],
                  {"upper": GOOD, "lower": GOOD})
    assert payload["river"] == []
    assert payload["latest"] is None and payload["lowest"] is None
    assert "union" in payload["river_unavailable"]
    assert "crecida floodwater" in payload["river_unavailable"]
    # The stretches are unaffected: each is measured over its own corridor.
    assert [s["key"] for s in payload["stretches"]] == ["upper", "lower"]


def test_a_union_total_for_a_different_reach_is_refused(tmp_path):
    """The publishing set can change; a stale total must not survive it.

    If a reach drops below the skill floor, a total measured while it was
    still in describes a different river. The set travels with the
    measurement and has to match.
    """
    payload = run(
        tmp_path,
        [row("upper", [100.0, 110.0, 120.0]), row("lower", [200.0, 210.0, 220.0])],
        {"upper": GOOD, "lower": GOOD},
        river=union([260.0, 280.0, 300.0], ["upper", "lower", "delta"]),
    )
    assert payload["river"] == []
    assert "different set of stretches" in payload["river_unavailable"]


def test_a_gap_reads_as_a_gap_not_as_the_river_falling(tmp_path):
    """A cycle nobody could measure must be absent, never low.

    Summing stretches made this a live hazard: a stretch that did not
    report counted as zero and the river appeared to drop. The union
    measurement removes that arithmetic, and the gap survives as a gap -
    floodwater records it with a reason rather than publishing a figure
    over ground it did not see.
    """
    payload = run(
        tmp_path,
        [row("upper", [100.0, 110.0, 120.0]), row("lower", [200.0, 210.0, 220.0])],
        {"upper": GOOD, "lower": GOOD},
        # The middle cycle was not measurable, so it is simply not there.
        river=union([260.0, 300.0], ["upper", "lower"], days=[DAYS[0], DAYS[2]]),
    )
    assert [r["day"] for r in payload["river"]] == [DAYS[0], DAYS[2]]
    # No zero was invented for the missing cycle.
    assert 0.0 not in [r["water_km2"] for r in payload["river"]]
    assert payload["latest"]["day"] == DAYS[2]


def test_no_published_figure_rests_on_a_single_cycle(tmp_path):
    """The driest pass on the record was one pass, and it had failed.

    The published low was a cycle in which the mask lost half the permanent
    channel of two adjacent corridors - a 9.8-sigma outlier in a quantity
    otherwise stable to 1.5% - and the front page measured both of its
    comparisons against it. min() over a series with one bad row is not a
    statistic, so each anchor is a median over several cycles and carries
    the cycles it came from.
    """
    from parana_crecida.export import ANCHOR_CYCLES

    payload = run(
        tmp_path,
        [row("upper", [100.0, 110.0, 120.0]), row("lower", [200.0, 210.0, 220.0])],
        {"upper": GOOD, "lower": GOOD},
        river=union([100.0, 260.0, 270.0, 280.0, 300.0], ["upper", "lower"],
                    days=[*DAYS, "2026-10-16", "2026-10-28"]),
    )
    # Two guards compose here. The collapsed cycle found a third of the
    # channel, so the plausibility test rejects it outright and it never
    # reaches the anchor; and the anchor is a median over three cycles
    # rather than the single extreme, so it would have survived the cycle
    # even if the first guard had let it through.
    assert [r["day"] for r in payload["river"]] == DAYS[1:] + [
        "2026-10-16", "2026-10-28",
    ]
    assert [g["day"] for g in payload["river_gaps"]] == ["2026-09-10"]
    assert payload["river_gaps"][0]["reason"] == "channel detection implausible"

    low = payload["lowest"]
    assert low["water_km2"] != 100.0, "the anchor took the one failed cycle"
    assert low["water_km2"] == 270.0
    assert len(low["over_cycles"]) == ANCHOR_CYCLES
    # The cycles behind the anchor travel with it, so a reader can see what
    # the comparison rests on.
    assert low["over_cycles"] == ["2026-09-22", "2026-10-04", "2026-10-16"]
    # And the high end is anchored the same way.
    assert payload["highest"]["water_km2"] == 280.0
    assert len(payload["highest"]["over_cycles"]) == ANCHOR_CYCLES


def test_a_stretch_that_was_never_scored_is_withheld(tmp_path):
    """Plausible is not measured. The Delta is the reason this exists."""
    payload = run(tmp_path,
                  [row("upper", [100.0, 110.0, 120.0]), row("delta", [900.0, 900.0, 900.0])],
                  {"upper": GOOD},
                  river=union([95.0, 105.0, 115.0], ["upper"]))
    assert [r["water_km2"] for r in payload["river"]] == [95.0, 105.0, 115.0]
    assert [s["key"] for s in payload["stretches"]] == ["upper"]
    assert [w["key"] for w in payload["withheld"]] == ["delta"]
    assert "optical" in payload["withheld"][0]["withheld"]
    # Withheld means withheld: no series travels with it.
    assert "water_km2" not in payload["withheld"][0]


def test_a_stretch_scoring_below_the_floor_is_withheld(tmp_path):
    payload = run(tmp_path,
                  [row("upper", [100.0, 110.0, 120.0]), row("poor", [900.0, 900.0, 900.0])],
                  {"upper": GOOD, "poor": round(SKILL_FLOOR - 0.05, 2)})
    assert [s["key"] for s in payload["stretches"]] == ["upper"]
    assert payload["withheld"][0]["withheld"] == "scored below the floor"
    assert payload["withheld"][0]["iou"] < SKILL_FLOOR


def test_a_published_stretch_carries_the_score_that_let_it_publish(tmp_path):
    payload = run(tmp_path, [row("upper", [100.0, 110.0, 120.0])], {"upper": GOOD})
    published = payload["stretches"][0]
    assert published["iou"] == GOOD
    assert published["recall"] == 0.8 and published["precision"] == 0.8


def test_the_published_reach_is_the_reach_that_was_summed(tmp_path):
    payload = run(tmp_path,
                  [row("upper", [100.0, 110.0, 120.0]), row("delta", [900.0, 900.0, 900.0])],
                  {"upper": GOOD})
    assert payload["river_km"] == pytest.approx(100.0), "the withheld reach was counted"


def test_the_skill_table_travels_with_the_measurement(tmp_path):
    """A water mask with no score attached is a picture, not a measurement."""
    payload = run(tmp_path, [row("upper", [100.0, 110.0, 120.0])], {"upper": GOOD})
    skill = payload["skill"]
    assert skill["measured_against"]
    assert skill["iou"] > skill["previous"]["iou"], "the new mask must beat the old"
    assert payload["skill_floor"] == SKILL_FLOOR


def test_no_validation_table_publishes_nothing(tmp_path):
    """Without a score there is no rule for who publishes, so nobody does."""
    assert export_floodwater(
        Passing(),
        source_path=write_source(tmp_path, [row("upper", [100.0, 110.0, 120.0])]),
        skill_path=tmp_path / "absent.json",
        out_dir=tmp_path,
    ) is None


def test_a_failed_data_test_blocks_the_export(tmp_path):
    with pytest.raises(ExportBlocked):
        export_floodwater(
            Failing(),
            source_path=write_source(tmp_path, [row("upper", [100.0, 110.0, 120.0])]),
            skill_path=write_skill(tmp_path, {"upper": GOOD}),
            out_dir=tmp_path,
        )
    assert not (tmp_path / "floodwater.json").exists()


def test_nothing_measured_publishes_nothing(tmp_path):
    assert export_floodwater(
        Passing(),
        source_path=write_source(tmp_path, []),
        skill_path=write_skill(tmp_path, {"upper": GOOD}),
        out_dir=tmp_path,
    ) is None


# -- the published file ----------------------------------------------------

PUBLISHED = Path("site/data/floodwater.json")

published = pytest.mark.skipif(
    not PUBLISHED.exists(), reason="nothing exported yet")


@published
def test_flood_water_is_never_more_than_all_water():
    """Water outside the channel cannot exceed the water.

    An earlier change-detection trial produced exactly this, and the
    impossibility is what identified it as wrong. The check is cheap and
    the failure is silent otherwise.
    """
    payload = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    for stretch in payload["stretches"]:
        for day, water, flood in zip(
            stretch["days"], stretch["water_km2"], stretch["flood_km2"]
        ):
            if water is None or flood is None:
                continue
            assert flood <= water + 1e-6, f"{stretch['key']} {day}: {flood} > {water}"
            assert flood >= 0 and water >= 0, f"{stretch['key']} {day}: negative"


@published
def test_the_permanent_channel_is_not_counted_as_flood_all_year():
    """A flood still there in the driest week of the year is not a flood.

    Yacyreta filled in 2011, so it never reaches 90% of the 1984-2021 JRC
    occurrence record and was read as flood water in every frame - 353 km2
    of standing reservoir, in the most conspicuous place on the river.
    The shape of that error is a stretch whose flood-only area barely
    moves between its driest and wettest pass while its total water does.
    """
    payload = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    for stretch in payload["stretches"]:
        water = [v for v in stretch["water_km2"] if v is not None]
        flood = [v for v in stretch["flood_km2"] if v is not None]
        if len(water) < 4 or len(flood) != len(water) or min(water) <= 0:
            continue
        # The test that actually catches it: at the driest pass on the
        # record, water beyond the permanent channel should be a small
        # share of the water, because at low flow the river is its
        # channel. Measured across the eight published stretches that
        # share runs 1% to 13%. With the reservoir miscounted, Posadas to
        # Ita Ibate sat near 77% - and still passed a flatness check,
        # because a large constant offset rises and falls with the flood
        # on top of it.
        driest = min(range(len(water)), key=lambda i: water[i])
        if driest < len(flood):
            share = flood[driest] / water[driest]
            assert share < 0.35, (
                f"{stretch['key']}: at its driest pass {share:.0%} of the water "
                "is called flood, which means something permanent is in it"
            )


@published
def test_every_published_stretch_cleared_the_floor():
    payload = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    for stretch in payload["stretches"]:
        assert stretch["iou"] >= SKILL_FLOOR, stretch["key"]
    for row in payload["withheld"]:
        assert row.get("iou") is None or row["iou"] < SKILL_FLOOR, row["key"]


# -- the incremental rebuild ----------------------------------------------


def test_the_cycle_grid_is_anchored_to_what_was_measured():
    """A daily job must not invent a new set of windows every morning.

    Computing the grid backwards from today moves every date by a day each
    run, so the series would never grow: each morning would measure a
    fresh set of windows and discard the old. The map frames did exactly
    that and left fifteen orphaned images in the repository.
    """
    from datetime import date as _date

    from parana_crecida.floodwater import _grid

    known = ["2026-10-04", "2026-10-16", "2026-10-28"]

    # Nothing until a full repeat cycle has passed.
    assert _grid(known, _date(2026, 10, 30), 12) == []
    assert _grid(known, _date(2026, 11, 8), 12) == []

    # Then one cycle, on the grid the series already sits on.
    assert _grid(known, _date(2026, 11, 9), 12) == [_date(2026, 11, 9)]

    # And it keeps that grid rather than drifting with the run date.
    assert _grid(known, _date(2026, 11, 21), 12) == [
        _date(2026, 11, 9), _date(2026, 11, 21),
    ]


def test_an_empty_table_asks_for_a_whole_year():
    from datetime import date as _date

    from parana_crecida.floodwater import KEEP_CYCLES, _grid

    grid = _grid([], _date(2026, 10, 28), 12)
    assert len(grid) == KEEP_CYCLES
    assert grid == sorted(grid), "the grid must run oldest first"
    assert grid[-1] == _date(2026, 10, 28)


def test_a_gap_moves_the_grid_on_rather_than_stalling_it():
    """A window nobody could measure must not be retried for ever.

    The anchor is the last window *attempted*, not the last one measured.
    Anchoring on measurements alone would park the series in front of the
    first unobservable window and re-measure it every morning, never
    reaching the cycles behind it.
    """
    from datetime import date as _date

    from parana_crecida.floodwater import _grid

    measured = ["2026-10-04", "2026-10-16"]
    gap = "2026-10-28"  # rebuild() stores {"day": ..., "reason": ...}

    # On measurements alone the grid offers the gap again, for ever.
    assert _grid(measured, _date(2026, 11, 9), 12) == [
        _date(2026, 10, 28), _date(2026, 11, 9),
    ]

    # Counting the gap as attempted moves past it to the cycle behind.
    assert _grid(measured + [gap], _date(2026, 11, 9), 12) == [_date(2026, 11, 9)]


def test_the_grid_anchors_on_the_newest_attempt_whatever_the_order():
    """``gaps`` and ``days`` are separate lists and concatenate unsorted."""
    from datetime import date as _date

    from parana_crecida.floodwater import _grid

    assert _grid(
        ["2026-10-16", "2026-11-09", "2026-10-28"], _date(2026, 11, 21), 12
    ) == [_date(2026, 11, 21)]


def test_nothing_observed_is_not_zero_water():
    """``area_km2`` separates "no water" from "no observation".

    A null reduction means no unmasked pixel fell inside the corridor -
    the satellite did not look, or the mosaic does not cover this ground.
    Coercing that to 0.0 published a dry river for a window with no data,
    and it is the shape of the one failure in the published series.
    """
    from parana_crecida import floodwater as fw

    class FakeMask:
        """Just enough of an ee.Image to reach the reduction."""

        def __init__(self, result):
            self._result = result

        def selfMask(self):
            return self

        def rename(self, _name):
            return self

        def multiply(self, _other):
            return self

        def reduceRegion(self, **_kwargs):
            return self

        def getInfo(self):
            return self._result

    class FakeEE:
        class Image:
            @staticmethod
            def pixelArea():
                return None

        class Reducer:
            @staticmethod
            def sum():
                return None

    import sys

    sys.modules.setdefault("ee", FakeEE)
    try:
        assert fw.area_km2(None, FakeMask({"water": None})) is None
        assert fw.area_km2(None, FakeMask({})) is None
        assert fw.area_km2(None, FakeMask(None)) is None
        # A real zero still reads as a real zero.
        assert fw.area_km2(None, FakeMask({"water": 0.0})) == 0.0
        assert fw.area_km2(None, FakeMask({"water": 2.5e6})) == pytest.approx(2.5)
    finally:
        if sys.modules.get("ee") is FakeEE:
            del sys.modules["ee"]


def test_a_cycle_records_what_it_was_measured_from():
    """Window ends are grid dates; the acquisitions have to be recorded.

    The published series has always carried a window end and nothing about
    the passes inside it, so the real cadence per reach could not be
    recovered from the product: the dates look like a regular twelve-day
    sequence because they are a grid, not because the satellite flew one.
    """
    import inspect

    from parana_crecida import floodwater as fw

    assert fw.MIN_COVERAGE == 0.95

    # acquisitions() must return every field the series needs to describe
    # its own provenance, and must read the metadata rather than assume it.
    source = inspect.getsource(fw.acquisitions)
    for field in ("acq_days", "scenes", "orbits", "passes", "platforms"):
        assert f'"{field}"' in source, field
    for prop in ("relativeOrbitNumber_start", "orbitProperties_pass",
                 "platform_number", "system:time_start"):
        assert prop in source, prop

    # And rebuild() must store that block against the cycle, with the
    # window it covers, not just the end date it is published under.
    stored = inspect.getsource(fw.rebuild)
    assert '"window_start": start.isoformat()' in stored
    assert '"window_end": end.isoformat()' in stored
    assert '"seen_km2"' in stored and '"coverage"' in stored
    assert "**provenance," in stored


def test_a_gap_carries_its_reason():
    """A gap is published, not swallowed, and says why it is a gap."""
    import inspect

    from parana_crecida import floodwater as fw

    source = inspect.getsource(fw.rebuild)
    assert '"reason": reason' in source
    assert 'gap("nothing observed")' in source
    assert 'gap("corridor not fully observed"' in source
    # The coverage test is applied before the area is believed.
    assert source.index("min_coverage") < source.index('row["days"].append')


def test_the_cycle_that_failed_in_the_published_record_is_now_rejected():
    """The real one, by its real numbers.

    The published series ran 31 cycles. Water detected inside the permanent
    channel - water_km2 minus flood_km2 - held at 5,219 km2 with a standard
    deviation of 76, which is 1.5%, because the channel mask is fixed
    geometry over a river that never leaves it. One cycle read 4,470: a
    750 km2 deficit at z = -9.8, confined to two adjacent corridors that
    lost half and a quarter of their channel while the other six moved by
    under 2%. A 20 km-wide corridor does not lose half its channel for
    twelve days.

    That cycle was published as the driest pass on the record, and the
    front page measured both of its comparisons against it.
    """
    from parana_crecida.export import _implausible

    # The published record, (water_km2, flood_km2) per cycle.
    published = [
        (6905.6, 1617.5), (6702.6, 1419.8), (6167.1, 981.4), (6319.6, 1063.0),
        (5965.6, 739.2), (6036.2, 837.6), (5782.4, 633.6), (5740.5, 560.3),
        (5750.4, 547.2), (5740.7, 565.9), (5729.9, 570.9), (5697.3, 561.3),
        (5509.9, 383.6), (5553.6, 433.7), (5644.6, 499.9), (5800.8, 657.5),
        (5865.7, 684.4), (6414.3, 1205.8), (6120.3, 1034.8), (5741.1, 573.3),
        (5203.6, 733.7),  # 2026-06-30
        (7453.2, 2173.3), (6684.5, 1447.7), (6397.4, 1150.1), (6617.2, 1306.8),
        (6643.4, 1370.6), (6650.7, 1347.3), (6904.8, 1602.3), (7201.6, 1871.7),
        (6982.1, 1725.2), (9727.5, 4301.9),
    ]
    rows = [
        {"day": f"c{i:02d}", "water_km2": w, "flood_km2": f}
        for i, (w, f) in enumerate(published)
    ]

    kept, rejected = _implausible(rows)

    assert [r["day"] for r in rejected] == ["c20"], (
        "the cycle that lost 750 km2 of permanent channel is the one and only "
        "cycle this test must reject"
    )
    assert len(kept) == 30
    # Including the record flood, which must not be mistaken for an outlier
    # just because it is far from the median.
    assert kept[-1]["water_km2"] == 9727.5


# -- does the published measurement move with the river? -------------------


def test_every_published_stretch_moves_with_the_river():
    """The check a passing validation score does not give you.

    An IoU says the mask agrees with optical water on one date. It does not
    say the series that follows carries a flood. A reach whose area falls
    as the river rises is measuring something that is not the river -
    tide, wind, a reservoir held at a constant level - however well it
    scored against a static reference.

    This test existed for the superseded VV product and never for the live
    one: `_stage_correlation` was only ever called from `export_flooded`,
    so the mask that ships had no published relation to the gauge record at
    all. It covers the live product now.

    The bar catches a measurement pointing the wrong way, not a weak one.
    Extent at a given stage depends on topography, channel-floodplain
    connectivity, vegetation and whether the wave is rising or falling, so
    a weak correlation is the expected result on some reaches and is
    published as context rather than as corroboration.
    """
    published = Path("site/data/floodwater.json")
    if not published.exists():
        pytest.skip("nothing exported")
    payload = json.loads(published.read_text(encoding="utf-8"))

    checked = 0
    for stretch in payload.get("stretches", []):
        r = stretch.get("stage_correlation")
        if r is None:
            continue
        checked += 1
        assert -1.0 <= r <= 1.0, stretch["key"]
        assert r > -0.2, (
            f"{stretch['key']}: water area correlates {r:+.2f} with the stage "
            f"at the gauge below it - it is measuring something that is not "
            f"the flood, and it is being published"
        )
    if not checked:
        pytest.skip("no stage correlations in the export; needs a store")


def test_the_published_record_explains_what_the_correlation_is_for():
    """So it is not read as the validation it is explicitly not."""
    published = Path("site/data/floodwater.json")
    if not published.exists():
        pytest.skip("nothing exported")
    payload = json.loads(published.read_text(encoding="utf-8"))
    if not any("stage_correlation" in s for s in payload.get("stretches", [])):
        pytest.skip("no stage correlations in the export")
    note = (payload.get("stage_correlation_note") or "").lower()
    assert "context, not validation" in note
