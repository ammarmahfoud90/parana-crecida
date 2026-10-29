"""The guard checks.

Each test states the failure the check exists to catch, since every one of
them was written in response to a real bad reading.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from ina_a5 import DailyValue, GuardThresholds, datum_step, doy, percentile, run_guards
from ina_a5.quality import guard_flatline, guard_range, guard_spike, guard_stale

START = date(2015, 1, 1)


def series(values, start=START):
    return [
        DailyValue(day=start + timedelta(days=i), value=float(v))
        for i, v in enumerate(values)
    ]


def seasonal(n_days, *, start=START, amplitude=1.0, base=3.0):
    """A plausible gauge: an annual cycle with a little wobble."""
    return [
        DailyValue(
            day=start + timedelta(days=i),
            value=base
            + amplitude * math.sin(2 * math.pi * i / 365.0)
            + 0.05 * math.sin(i * 1.7),
        )
        for i in range(n_days)
    ]


# -- helpers ---------------------------------------------------------------


def test_doy_folds_29_february_onto_the_28th():
    assert doy(date(2024, 2, 28)) == 59
    assert doy(date(2024, 2, 29)) == 59
    assert doy(date(2024, 3, 1)) == 60
    assert doy(date(2023, 3, 1)) == 60
    assert doy(date(2023, 12, 31)) == 365
    assert doy(date(2024, 12, 31)) == 365


def test_percentile_interpolates():
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0], 0) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0
    assert percentile([7.0], 42) == 7.0


def test_percentile_of_nothing_is_an_error():
    with pytest.raises(ValueError):
        percentile([], 50)


# -- stale -----------------------------------------------------------------


def test_stale_flags_a_dead_feed():
    flag = guard_stale(series([1, 2, 3]), START + timedelta(days=40), max_age=10)
    assert flag is not None and flag.code == "stale"


def test_stale_allows_a_normal_lag():
    assert guard_stale(series([1, 2, 3]), START + timedelta(days=4), max_age=10) is None


def test_stale_on_no_data_at_all():
    flag = guard_stale([], date(2024, 1, 1))
    assert flag is not None and "no cached data" in flag.detail


# -- flatline --------------------------------------------------------------


def test_flatline_catches_a_stuck_sensor():
    flag = guard_flatline(series([3.21] * 14), n_days=14)
    assert flag is not None and flag.code == "flatline"


def test_flatline_tolerates_a_steady_but_moving_river():
    flag = guard_flatline(series([3.21, 3.22] * 7), n_days=14)
    assert flag is None


def test_flatline_needs_enough_days():
    assert guard_flatline(series([3.21] * 5), n_days=14) is None


# -- spike -----------------------------------------------------------------


def test_spike_catches_a_jump_beyond_all_precedent():
    history = seasonal(500)
    history.append(DailyValue(day=history[-1].day + timedelta(days=1), value=400.0))
    flag = guard_spike(history, factor=1.5)
    assert flag is not None and flag.code == "spike"


def test_spike_allows_a_real_flood_within_precedent():
    """Floods rise fast, but within the station's own record of floods."""
    history = seasonal(500)
    last = history[-1]
    history.append(DailyValue(day=last.day + timedelta(days=1), value=last.value + 0.02))
    assert guard_spike(history, factor=1.5) is None


def test_spike_says_nothing_about_a_short_record():
    assert guard_spike(seasonal(100), factor=1.5) is None


# -- range -----------------------------------------------------------------


def test_range_catches_a_value_off_the_scale():
    history = seasonal(500)
    history.append(DailyValue(day=history[-1].day + timedelta(days=1), value=99.0))
    flag = guard_range(history, margin=2.0)
    assert flag is not None and flag.code == "range"


def test_range_catches_a_value_far_below():
    history = seasonal(500)
    history.append(DailyValue(day=history[-1].day + timedelta(days=1), value=-99.0))
    flag = guard_range(history, margin=2.0)
    assert flag is not None and flag.code == "range"


def test_range_allows_a_new_record_by_a_sensible_margin():
    history = seasonal(500)
    peak = max(v.value for v in history)
    history.append(DailyValue(day=history[-1].day + timedelta(days=1), value=peak + 0.05))
    assert guard_range(history, margin=2.0) is None


# -- datum step: the Corrientes test ---------------------------------------


def test_datum_step_catches_a_re_zeroed_gauge():
    """A gauge shifted by 4 m sits outside its own envelope every single day."""
    history = seasonal(365 * 6)
    shifted = [
        DailyValue(day=v.day, value=v.value + 4.0)
        for v in seasonal(90, start=history[-1].day + timedelta(days=1))
    ]
    today = shifted[-1].day
    shares = datum_step(history + shifted, today, window=90)
    assert shares is not None
    assert shares.share_high > 80.0
    assert "above the historical p95" in shares.detail


def test_datum_step_is_quiet_about_a_normal_run():
    history = seasonal(365 * 7)
    shares = datum_step(history, history[-1].day, window=90)
    assert shares is not None
    assert max(shares.share_high, shares.share_low) < 50.0


def test_datum_step_needs_enough_history():
    assert datum_step(seasonal(200), date(2015, 7, 19), window=90) is None


def test_a_flood_is_not_a_datum_step():
    """A flood pushes above p95 for a while and then recedes; a step never does."""
    history = seasonal(365 * 6)
    flood = []
    for i, v in enumerate(seasonal(90, start=history[-1].day + timedelta(days=1))):
        # Three weeks above the envelope, then back to normal.
        bump = 4.0 if 20 <= i < 41 else 0.0
        flood.append(DailyValue(day=v.day, value=v.value + bump))
    shares = datum_step(history + flood, flood[-1].day, window=90)
    assert shares is not None
    assert shares.share_high < 80.0  # not enough of the window to look like a step


# -- run_guards ------------------------------------------------------------


def test_run_guards_passes_a_healthy_series():
    history = seasonal(365 * 7)
    assert run_guards(history, history[-1].day) == []


def test_run_guards_short_circuits_on_a_dead_feed():
    """Nothing downstream is meaningful once the feed has stopped."""
    history = seasonal(365 * 7)
    flags = run_guards(history, history[-1].day + timedelta(days=90))
    assert [f.code for f in flags] == ["stale"]


def test_run_guards_reports_a_spike_and_a_range_together():
    history = seasonal(500)
    history.append(DailyValue(day=history[-1].day + timedelta(days=1), value=500.0))
    codes = {f.code for f in run_guards(history, history[-1].day)}
    assert {"spike", "range"} <= codes


def test_thresholds_are_adjustable():
    history = seasonal(500)
    last = history[-1]
    history.append(DailyValue(day=last.day + timedelta(days=1), value=last.value + 0.3))
    strict = GuardThresholds(spike_factor=0.1)
    codes = {f.code for f in run_guards(history, history[-1].day, strict)}
    assert "spike" in codes
