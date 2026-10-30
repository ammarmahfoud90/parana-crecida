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


def write_source(tmp_path: Path, stretches: list[dict]) -> Path:
    path = tmp_path / "source.json"
    path.write_text(json.dumps({
        "schema": 2, "note": "n", "method": "vh-threshold/hand",
        "vh_threshold_db": -25.0, "hand_limit_m": 10.0, "window_days": 12,
        "fetched_on": "2026-10-30", "stretches": stretches,
    }), encoding="utf-8")
    return path


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


def run(tmp_path, stretches, scores):
    export_floodwater(
        Passing(),
        source_path=write_source(tmp_path, stretches),
        skill_path=write_skill(tmp_path, scores),
        out_dir=tmp_path,
    )
    return json.loads((tmp_path / "floodwater.json").read_text(encoding="utf-8"))


def test_the_river_total_is_the_sum_of_the_published_stretches(tmp_path):
    payload = run(tmp_path,
                  [row("upper", [100.0, 110.0, 120.0]), row("lower", [200.0, 210.0, 220.0])],
                  {"upper": GOOD, "lower": GOOD})
    assert [r["water_km2"] for r in payload["river"]] == [300.0, 320.0, 340.0]
    assert all(r["complete"] for r in payload["river"])


def test_a_cycle_missing_a_stretch_is_not_counted_as_a_drop(tmp_path):
    """A gap must read as a gap, not as the river falling."""
    payload = run(
        tmp_path,
        [row("upper", [100.0, 110.0, 120.0]),
         row("lower", [200.0, 220.0], days=[DAYS[0], DAYS[2]])],
        {"upper": GOOD, "lower": GOOD},
    )
    middle = next(r for r in payload["river"] if r["day"] == DAYS[1])
    assert middle["complete"] is False
    assert middle["stretches"] == 1
    # The incomplete cycle is published, so a reader can see the gap, but it
    # can never be the headline or the low-water mark.
    assert payload["latest"]["day"] == DAYS[2]
    assert payload["lowest"]["complete"] is True
    assert payload["highest"]["complete"] is True


def test_a_stretch_that_was_never_scored_is_withheld(tmp_path):
    """Plausible is not measured. The Delta is the reason this exists."""
    payload = run(tmp_path,
                  [row("upper", [100.0, 110.0, 120.0]), row("delta", [900.0, 900.0, 900.0])],
                  {"upper": GOOD})
    assert [r["water_km2"] for r in payload["river"]] == [100.0, 110.0, 120.0]
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
