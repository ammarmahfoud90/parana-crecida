"""Crest detection, and the smoothing claim it rests on."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from ina_a5 import DailyValue

from parana_crecida.events import (
    SMOOTH_DAYS,
    _centred_mean,
    anomaly_series,
    detect,
    latest_crest,
    to_segments,
)
from parana_crecida.normalize import Climatology

import numpy as np

START = date(2020, 1, 1)
EMPTY = Climatology({})


def flat_climatology(series_id="26261"):
    curve = {"5": 1.0, "10": 2.0, "25": 3.0, "50": 4.0, "75": 5.0, "90": 6.0, "95": 7.0}
    return Climatology(
        {series_id: {"years": 100, "percentiles": {str(d): dict(curve) for d in range(1, 366)}}}
    )


def series(values, start=START):
    return [
        DailyValue(day=start + timedelta(days=i), value=float(v))
        for i, v in enumerate(values)
    ]


def with_peak(n=200, peak_at=100, height=5.0, half_width=15, base=3.0, wobble=0.5):
    """A symmetric triangular crest on a baseline.

    ``wobble`` is a seasonal swing. Set it to 0 where a test needs the
    injected peak to be the only thing in the series: a half-metre swing is
    a real crest and the detector is right to say so.
    """
    out = []
    for i in range(n):
        v = base + wobble * math.sin(2 * math.pi * i / 90.0)
        distance = abs(i - peak_at)
        if distance < half_width:
            v += height * (1 - distance / half_width)
        out.append(v)
    return out


# -- smoothing -------------------------------------------------------------


def test_centred_mean_does_not_move_a_symmetric_peak():
    """The load-bearing claim: a trailing filter would shift every crest."""
    raw = np.array(with_peak())
    smoothed = _centred_mean(raw, SMOOTH_DAYS)
    assert int(np.argmax(smoothed)) == int(np.argmax(raw)) == 100


def test_centred_mean_actually_smooths():
    noisy = np.array([0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 0.0, 5.0, 0.0])
    smoothed = _centred_mean(noisy, 5)
    assert smoothed[3:8].std() < noisy[3:8].std()


def test_centred_mean_leaves_the_ends_alone():
    """So the taper against implicit zeros cannot read as a trough."""
    raw = np.array([4.0] * 20)
    smoothed = _centred_mean(raw, 5)
    assert smoothed[0] == 4.0
    assert smoothed[-1] == 4.0


def test_centred_mean_passes_through_a_short_series():
    raw = np.array([1.0, 2.0])
    assert list(_centred_mean(raw, 5)) == [1.0, 2.0]


# -- segments --------------------------------------------------------------


def test_a_continuous_series_is_one_segment():
    assert len(to_segments(series([3.0] * 60))) == 1


def test_a_long_gap_splits_the_record():
    first = series([3.0] * 40)
    second = series([3.0] * 40, start=START + timedelta(days=100))
    assert len(to_segments(first + second)) == 2


def test_a_short_gap_is_interpolated_not_split():
    values = series([3.0] * 40)
    values = values[:20] + values[23:]  # a three-day hole
    segments = to_segments(values)
    assert len(segments) == 1
    assert segments[0].values.size == 40


def test_a_segment_too_short_to_hold_a_crest_is_dropped():
    assert to_segments(series([3.0] * 10)) == []


def test_no_segments_from_nothing():
    assert to_segments([]) == []


# -- detection -------------------------------------------------------------


def test_a_crest_is_found_on_the_right_day():
    crests = detect("26261", series(with_peak(peak_at=100)), EMPTY)
    assert len(crests) == 1
    assert crests[0].day == START + timedelta(days=100)


@pytest.mark.parametrize("peak_at", [60, 100, 140])
def test_the_crest_day_does_not_drift_with_position(peak_at):
    """If smoothing had a phase lag, every one of these would land late."""
    crests = detect("26261", series(with_peak(peak_at=peak_at)), EMPTY)
    assert crests[0].day == START + timedelta(days=peak_at)


def test_the_reported_value_is_the_real_reading_not_the_smoothed_one():
    raw = with_peak(peak_at=100, height=5.0)
    crests = detect("26261", series(raw), EMPTY)
    assert crests[0].value == pytest.approx(raw[100])


def test_two_crests_are_found_separately():
    values = with_peak(n=300, peak_at=60)
    second = with_peak(n=300, peak_at=200)
    combined = [max(a, b) for a, b in zip(values, second)]
    days = [c.day for c in detect("26261", series(combined), EMPTY)]
    assert len(days) == 2


def test_a_ripple_is_not_a_crest():
    """The prominence bar is set from the gauge's own scale.

    Differential: adding a 5 cm bump to the series must not add a crest.
    The seasonal swing underneath it genuinely is one.
    """
    baseline = detect("26261", series(with_peak(height=0.0)), EMPTY)
    rippled = detect("26261", series(with_peak(height=0.05)), EMPTY)
    assert len(rippled) == len(baseline)


def test_no_crest_is_invented_across_a_long_gap():
    first = series(with_peak(n=60, peak_at=30, height=5.0))
    second = series([3.0] * 60, start=START + timedelta(days=200))
    crests = detect("26261", first + second, EMPTY)
    assert all(c.day <= START + timedelta(days=60) for c in crests)


def test_a_rising_series_has_no_crest_yet():
    """The live case: the wave has not peaked, so nothing is reported."""
    rising = series([3.0 + i * 0.05 for i in range(120)])
    assert detect("26261", rising, EMPTY) == []


def test_the_rise_is_measured_back_to_the_preceding_trough():
    crests = detect("26261", series(with_peak(peak_at=100, half_width=15)), EMPTY)
    crest = crests[0]
    assert crest.rise_start is not None
    assert crest.rise_days is not None and crest.rise_days > 0
    assert crest.rise_rate is not None and crest.rise_rate > 0


def test_detect_on_nothing():
    assert detect("26261", [], EMPTY) == []


def test_since_filters_by_date():
    values = series(with_peak(n=300, peak_at=60, wobble=0.0))
    assert detect("26261", values, EMPTY, since=START + timedelta(days=100)) == []
    assert detect("26261", values, EMPTY, since=START) != []


# -- describing the crest --------------------------------------------------


def test_a_crest_above_p90_is_high():
    clim = flat_climatology()
    crests = detect("26261", series(with_peak(base=3.0, height=5.0)), clim)
    assert crests[0].percentile is not None
    assert crests[0].is_high


def test_a_crest_below_p90_is_not_high():
    clim = flat_climatology()
    crests = detect("26261", series(with_peak(base=1.0, height=2.0)), clim)
    assert crests and not crests[0].is_high


def test_without_a_climatology_a_crest_is_never_called_high():
    """No reference means unknown, which must not read as a flood."""
    crests = detect("26261", series(with_peak()), EMPTY)
    assert crests[0].percentile is None
    assert not crests[0].is_high


def test_anomaly_series_skips_what_cannot_be_normalised():
    assert anomaly_series("26261", series([3.0] * 40), EMPTY) == []
    assert len(anomaly_series("26261", series([3.0] * 40), flat_climatology())) == 40


def test_latest_crest_prefers_high_ones():
    clim = flat_climatology()
    big = with_peak(n=400, peak_at=60, base=3.0, height=5.0, wobble=0.0)
    small = with_peak(n=400, peak_at=300, base=3.0, height=2.0, wobble=0.0)
    combined = [max(a, b) for a, b in zip(big, small)]
    crests = detect("26261", series(combined), clim)
    assert latest_crest(crests, high_only=True).day == START + timedelta(days=60)
    assert latest_crest(crests, high_only=False).day == START + timedelta(days=300)


def test_latest_crest_of_nothing():
    assert latest_crest([]) is None
