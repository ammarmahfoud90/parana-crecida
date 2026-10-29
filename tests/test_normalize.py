"""Making gauges comparable: percentiles to describe, anomalies to measure."""

from __future__ import annotations

import json
from datetime import date

import pytest

from parana_crecida.normalize import (
    Climatology,
    DoyCurve,
    trim,
)

# A gauge that runs 1 m at p5 up to 7 m at p95, the same all year.
CURVE = {"5": 1.0, "10": 2.0, "25": 3.0, "50": 4.0, "75": 5.0, "90": 6.0, "95": 7.0}


def flat_climatology(series_id="26261", curve=None, years=100):
    curve = curve or CURVE
    return Climatology(
        {
            series_id: {
                "station": "Test",
                "years": years,
                "variable_family": "level",
                "percentiles": {str(d): dict(curve) for d in range(1, 366)},
            }
        }
    )


DAY = date(2026, 6, 15)


# -- the curve -------------------------------------------------------------


def test_spread_is_p90_minus_p10():
    assert DoyCurve(1, 2, 3, 4, 5, 6, 7).spread == pytest.approx(4.0)


def test_spread_never_collapses_to_zero():
    """A gauge that barely moves must not divide the anomaly by nothing."""
    assert DoyCurve(3, 3, 3, 3, 3, 3, 3).spread > 0


def test_points_are_ascending_by_value():
    points = DoyCurve(1, 2, 3, 4, 5, 6, 7).points()
    assert [p for p, _ in points] == [5, 10, 25, 50, 75, 90, 95]
    assert [v for _, v in points] == sorted(v for _, v in points)


# -- percentile_of ---------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [(1.0, 5), (2.0, 10), (3.0, 25), (4.0, 50), (5.0, 75), (6.0, 90), (7.0, 95)],
)
def test_percentile_at_the_stored_points(value, expected):
    clim = flat_climatology()
    assert clim.percentile_of("26261", DAY, value) == pytest.approx(expected)


def test_percentile_interpolates_between_points():
    clim = flat_climatology()
    # Halfway from p25 (3 m) to p50 (4 m).
    assert clim.percentile_of("26261", DAY, 3.5) == pytest.approx(37.5)


def test_percentile_clamps_rather_than_running_away():
    """Past p95 the curve simply does not know; it must not invent a number."""
    clim = flat_climatology()
    assert clim.percentile_of("26261", DAY, 50.0) == 100.0
    assert clim.percentile_of("26261", DAY, -50.0) == 0.0


def test_percentile_just_past_the_ends_still_moves():
    clim = flat_climatology()
    above = clim.percentile_of("26261", DAY, 7.5)
    assert 95.0 < above < 100.0


def test_percentile_without_a_reading_or_a_curve_is_none():
    clim = flat_climatology()
    assert clim.percentile_of("26261", DAY, None) is None
    assert clim.percentile_of("99999", DAY, 4.0) is None


# -- anomaly ---------------------------------------------------------------


def test_anomaly_is_zero_at_the_median():
    assert flat_climatology().anomaly("26261", DAY, 4.0) == pytest.approx(0.0)


def test_anomaly_scales_by_the_robust_spread():
    # (6 - 4) / (6 - 2) = 0.5
    assert flat_climatology().anomaly("26261", DAY, 6.0) == pytest.approx(0.5)


def test_anomaly_does_not_saturate():
    """The reason it exists: a percentile pins at 100 and loses the peak."""
    clim = flat_climatology()
    assert clim.percentile_of("26261", DAY, 20.0) == clim.percentile_of("26261", DAY, 40.0)
    assert clim.anomaly("26261", DAY, 40.0) > clim.anomaly("26261", DAY, 20.0)


def test_anomaly_goes_negative_below_the_median():
    assert flat_climatology().anomaly("26261", DAY, 2.0) < 0


# -- comparability, which is the whole point -------------------------------


def test_two_gauges_on_different_datums_become_comparable():
    """5.7 m is a flood at one gauge and an ordinary day at another."""
    clim = Climatology(
        {
            "low": {
                "years": 100,
                "percentiles": {
                    str(d): {"5": 1.0, "10": 2.0, "25": 3.0, "50": 4.0,
                             "75": 5.0, "90": 6.0, "95": 7.0}
                    for d in range(1, 366)
                },
            },
            "high": {
                "years": 100,
                "percentiles": {
                    str(d): {"5": 21.0, "10": 22.0, "25": 23.0, "50": 24.0,
                             "75": 25.0, "90": 26.0, "95": 27.0}
                    for d in range(1, 366)
                },
            },
        }
    )
    # The same raw number means opposite things at the two gauges.
    assert clim.percentile_of("low", DAY, 5.7) > 75
    assert clim.percentile_of("high", DAY, 5.7) == 0.0
    # And the same anomaly means the same thing at both.
    assert clim.anomaly("low", DAY, 6.0) == pytest.approx(clim.anomaly("high", DAY, 26.0))


# -- plumbing --------------------------------------------------------------


def test_is_high_uses_the_day_of_year_distribution():
    clim = flat_climatology()
    assert clim.is_high("26261", DAY, 6.5)
    assert not clim.is_high("26261", DAY, 4.0)


def test_contains_and_years():
    clim = flat_climatology(years=126)
    assert "26261" in clim
    assert "99999" not in clim
    assert clim.years("26261") == 126
    assert clim.years("99999") == 0


def test_a_day_with_no_stored_curve_yields_nothing():
    clim = Climatology({"26261": {"years": 10, "percentiles": {"1": dict(CURVE)}}})
    assert clim.curve("26261", date(2026, 1, 1)) is not None
    assert clim.curve("26261", date(2026, 6, 15)) is None


def test_load_of_a_missing_file_is_empty_not_an_error(tmp_path):
    assert Climatology.load(tmp_path / "nope.json")._data == {}


def test_trim_keeps_only_the_gauges_asked_for(tmp_path):
    source = tmp_path / "full.json"
    source.write_text(
        json.dumps({"26261": {"years": 1}, "26276": {"years": 1}, "99999": {"years": 1}}),
        encoding="utf-8",
    )
    out = tmp_path / "trimmed.json"
    assert trim(source, ["26261", "26276"], out) == 2
    assert set(json.loads(out.read_text(encoding="utf-8"))) == {"26261", "26276"}
