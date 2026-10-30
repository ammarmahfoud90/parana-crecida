"""How well a river level predicts flooded area.

The finding this publishes is that it does not predict it well, which is
an unusual thing for a site to say about its own headline measurement. So
the numbers behind it are asserted rather than trusted: that the record is
large enough to mean something, that the scatter really is the size of the
trend, and that nobody has quietly turned a weak relationship into a
confident one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parana_crecida import areastage

SOURCE = areastage.load()
PUBLISHED = Path("site/data/area_stage.json")

pytestmark = pytest.mark.skipif(not SOURCE, reason="no frozen record")


def test_the_record_is_large_enough_to_say_anything():
    rows = SOURCE["observations"]
    assert len(rows) >= 300, f"only {len(rows)} observations"
    assert len({r["key"] for r in rows}) >= 3, "too few stretches to pool"


def test_every_observation_is_a_pass_against_a_level():
    for row in SOURCE["observations"]:
        assert row["ratio_to_bajante"] > 0, row["day"]
        assert -10 < row["above_alert_m"] < 5, row["day"]
        # Stage is relative to that gauge's own alert level, which is the
        # only thing that makes Goya and Parana comparable at all.
        assert abs(
            row["stage_m"] - row["alert_m"] - row["above_alert_m"]
        ) < 0.011, row["day"]


def test_the_scatter_really_is_the_size_of_the_trend():
    """The claim the page makes, asserted rather than written.

    If a future change made the relationship look tight, this fails, and
    the page's wording would need to change with it.
    """
    built = areastage.build(SOURCE)
    assert built["typical_spread_pct"] >= 30, (
        f"within-band spread is only {built['typical_spread_pct']}%; "
        "the page says the scatter is the size of the trend"
    )
    trend_pct = (built["trend"] - 1) * 100
    assert built["typical_spread_pct"] >= trend_pct * 0.7, (
        f"spread {built['typical_spread_pct']}% against a trend of "
        f"{trend_pct:.0f}% - the page's claim no longer holds"
    )


def test_the_weakness_is_published_not_hidden():
    built = areastage.build(SOURCE)
    assert built["r2"] is not None and built["r2"] <= 0.5, (
        "the page states how little of the variance the river explains"
    )
    assert built["above_alert"] == sum(
        1 for r in SOURCE["observations"] if r["above_alert_m"] >= 0
    )


def test_bands_too_thin_to_mean_anything_are_dropped():
    built = areastage.build(SOURCE)
    for band in built["bands"]:
        assert band["n"] >= areastage.MIN_BAND, band


def test_build_of_an_empty_record_is_empty():
    assert areastage.build({}) == {}
    assert areastage.build({"observations": []}) == {}


@pytest.mark.skipif(not PUBLISHED.exists(), reason="not exported yet")
def test_the_published_cloud_matches_the_record():
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    assert published["n"] == len(SOURCE["observations"])
    assert len(published["observations"]) == published["n"]
