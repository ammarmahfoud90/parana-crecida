"""Stage-frequency fitting.

The return period replaced the percentile as the thing the site leads
with, so the fit has to be right in the ways that matter: the ladder must
increase, the record must sit somewhere sensible on it, and a fit must be
refused where the record is too short to support one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from parana_crecida import frequency as fq


@dataclass(frozen=True, slots=True)
class V:
    day: date
    value: float


def year_of(values, year, peak):
    """A year of daily readings with a single clear maximum."""
    start = date(year - 1, 7, 1)
    for i in range(365):
        day = start + timedelta(days=i)
        # A smooth seasonal hump so the annual maximum is unambiguous.
        frac = math.sin(math.pi * i / 365) ** 2
        values.append(V(day, 1.0 + (peak - 1.0) * frac))
    return values


def synthetic(peaks: dict[int, float]):
    out: list[V] = []
    for year, peak in sorted(peaks.items()):
        year_of(out, year, peak)
    return out


def test_the_water_year_keeps_a_flood_season_whole():
    # A December flood and the following March belong to the same season.
    assert fq.water_year(date(2026, 12, 20)) == 2027
    assert fq.water_year(date(2027, 3, 10)) == 2027
    assert fq.water_year(date(2027, 8, 1)) == 2028


def test_a_short_record_is_refused():
    values = synthetic({y: 5.0 + (y % 7) * 0.2 for y in range(2000, 2015)})
    assert fq.fit("x", "X", values) is None


def test_the_ladder_increases_with_the_return_period():
    values = synthetic({y: 4.0 + ((y * 7) % 23) * 0.15 for y in range(1950, 2026)})
    fitted = fq.fit("x", "X", values)
    assert fitted is not None
    levels = [fitted.levels[p] for p in fq.LADDER]
    assert levels == sorted(levels), levels


def test_a_higher_stage_is_a_rarer_one():
    values = synthetic({y: 4.0 + ((y * 7) % 23) * 0.15 for y in range(1950, 2026)})
    fitted = fq.fit("x", "X", values)
    low = fitted.return_period(fitted.levels[2])
    high = fitted.return_period(fitted.levels[50])
    assert low is not None and high is not None
    assert high > low


def test_the_two_year_level_sits_near_the_middle_of_the_record():
    """A median flood should be a one-in-two-year flood, give or take."""
    peaks = {y: 4.0 + ((y * 7) % 23) * 0.15 for y in range(1950, 2026)}
    fitted = fq.fit("x", "X", synthetic(peaks))
    ordered = sorted(peaks.values())
    median = ordered[len(ordered) // 2]
    assert fitted.levels[2] == pytest.approx(median, rel=0.08)


def test_a_year_with_too_few_readings_is_dropped():
    values = synthetic({y: 5.0 for y in range(1960, 2020)})
    values.append(V(date(2024, 1, 15), 99.0))  # one reading, an absurd value
    maxima = fq.annual_maxima(values)
    assert 2024 not in maxima, "a single reading cannot be an annual maximum"


def test_a_near_symmetric_record_does_not_overflow():
    """Zero skew drives the shape parameter enormous; log-gamma handles it."""
    values = synthetic({y: 5.0 + (0.1 if y % 2 else -0.1) for y in range(1950, 2026)})
    fitted = fq.fit("x", "X", values)
    assert fitted is not None
    assert all(math.isfinite(v) for v in fitted.levels.values())


# -- the committed table ---------------------------------------------------

TABLE = fq.load()


@pytest.mark.skipif(not TABLE, reason="no frozen fit")
def test_the_committed_fits_are_sane():
    for gauge in TABLE["gauges"]:
        assert gauge["years"] >= fq.MIN_YEARS, gauge["name"]
        levels = [gauge["levels"][str(p)] for p in TABLE["ladder"]]
        assert levels == sorted(levels), gauge["name"]
        # The highest year on record should be a rare height, not a common
        # one: if the record maximum reads as a one-in-three-year event the
        # fit has gone wrong.
        period = fq.return_period_of(TABLE, gauge["series_id"], gauge["record_max"])
        assert period is not None and period >= 5, (
            f"{gauge['name']}: its record maximum reads as 1-in-{period}"
        )
