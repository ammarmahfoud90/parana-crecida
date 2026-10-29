"""Sanity checks on a gauge's series.

Ported from ``guard_readings.py``. These belong in the client package rather
than in any one consumer: anything reading a5 wants to know that a value is
believable before acting on it.

The checks are deliberately self-referential - each gauge is judged against
its own record, never against an absolute threshold - because gauge zeros
differ between stations and a level that is a flood at one site is a normal
summer at another.

The expensive one is :func:`datum_step`, which exists because of Corrientes:
two different gauge zeros in one spliced record made a 126-year series read
2.69 m where a 26-year series read 7.01 m. A fixed overlap test missed it.
"""

from __future__ import annotations

import calendar
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

from .models import DailyValue

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GuardThresholds:
    """Defaults carried over from ``guard_readings.py``."""

    max_age: int = 10
    flat_days: int = 14
    spike_factor: float = 1.5
    range_margin: float = 2.0
    step_window: int = 90
    step_share: float = 80.0
    #: Below this many days of record the statistical checks say nothing.
    min_record_days: int = 400


@dataclass(frozen=True, slots=True)
class GuardFlag:
    """One failed check. ``code`` is stable; ``detail`` is for humans."""

    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class StepShares:
    """How much of a recent window sits outside the historical envelope."""

    share_high: float
    share_low: float
    n_days: int
    detail: str


def doy(day: date) -> int:
    """Day of year on a 365-day calendar, 29 February folded onto the 28th."""
    n = day.timetuple().tm_yday
    if calendar.isleap(day.year) and n > 59:
        n -= 1
    return n


def percentile(sorted_values: Sequence[float], p: float) -> float:
    """Linearly interpolated percentile of an already-sorted sequence."""
    if not sorted_values:
        raise ValueError("percentile of an empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def guard_stale(
    values: Sequence[DailyValue], today: date, max_age: int = 10
) -> GuardFlag | None:
    """The feed has stopped."""
    if not values:
        return GuardFlag("stale", "no cached data at all")
    last = values[-1].day
    age = (today - last).days
    if age > max_age:
        return GuardFlag("stale", f"last reading {last} is {age} days old")
    return None


def guard_flatline(
    values: Sequence[DailyValue], n_days: int = 14
) -> GuardFlag | None:
    """A stuck sensor repeats one value exactly. A steady river does not."""
    if len(values) < n_days:
        return None
    tail = [v.value for v in values[-n_days:]]
    if len({round(v, 4) for v in tail}) == 1:
        return GuardFlag(
            "flatline", f"last {n_days} days all exactly {tail[-1]:.3f}"
        )
    return None


def guard_spike(
    values: Sequence[DailyValue],
    factor: float = 1.5,
    *,
    min_record_days: int = 400,
) -> GuardFlag | None:
    """A one-day change bigger than the record has ever produced.

    Real floods rise fast, but they rise within the station's own precedent,
    because that precedent contains real floods. A sensor glitch does not.
    """
    if len(values) < min_record_days:
        return None
    diffs = []
    # Exclude the final transition: today's jump must not set its own baseline.
    for i in range(1, len(values) - 1):
        if (values[i].day - values[i - 1].day).days == 1:
            diffs.append(abs(values[i].value - values[i - 1].value))
    if len(diffs) < 200:
        return None
    if (values[-1].day - values[-2].day).days != 1:
        return None
    worst = max(diffs)
    now = abs(values[-1].value - values[-2].value)
    if worst > 0 and now > worst * factor:
        return GuardFlag(
            "spike",
            f"jumped {now:.2f} in one day; largest ever seen in "
            f"{len(diffs)} days of record is {worst:.2f}",
        )
    return None


def guard_range(
    values: Sequence[DailyValue],
    margin: float = 2.0,
    *,
    min_record_days: int = 400,
) -> GuardFlag | None:
    """The latest value sits far outside the whole record's range."""
    if len(values) < min_record_days:
        return None
    history = sorted(v.value for v in values[:-1])
    lo, hi = history[0], history[-1]
    q1, q3 = percentile(history, 25), percentile(history, 75)
    iqr = max(q3 - q1, 1e-6)
    latest = values[-1].value
    if latest > hi + margin * iqr:
        return GuardFlag(
            "range",
            f"{latest:.2f} exceeds record max {hi:.2f} by "
            f"{(latest - hi) / iqr:.1f}x the spread",
        )
    if latest < lo - margin * iqr:
        return GuardFlag(
            "range",
            f"{latest:.2f} below record min {lo:.2f} by "
            f"{(lo - latest) / iqr:.1f}x the spread",
        )
    return None


def datum_step(
    values: Sequence[DailyValue],
    today: date,
    *,
    window: int = 90,
    min_record_days: int = 400,
) -> StepShares | None:
    """The Corrientes test: has this gauge been re-zeroed?

    Build the station's day-of-year envelope from everything older than the
    window, then ask what share of the last ``window`` days sits outside it.

    A flood pushes above p95 for days or weeks and then recedes. A gauge that
    was re-zeroed sits outside the envelope essentially every single day and
    never returns. So a very high share is evidence of a datum step, not of
    weather. Returns the shares; the caller decides.
    """
    if len(values) < min_record_days:
        return None

    cutoff = today - timedelta(days=window)
    recent = [v for v in values if v.day > cutoff]
    older = [v for v in values if v.day <= cutoff]
    if len(recent) < max(20, window // 6) or len(older) < 365 * 3:
        return None

    pool: dict[int, list[float]] = defaultdict(list)
    for v in older:
        n = doy(v.day)
        for offset in range(-7, 8):
            pool[(n + offset - 1) % 365 + 1].append(v.value)

    envelope = {
        n: (percentile(sorted(vals), 5), percentile(sorted(vals), 95))
        for n, vals in pool.items()
        if len(vals) >= 20
    }

    high = low = total = 0
    for v in recent:
        band = envelope.get(doy(v.day))
        if band is None:
            continue
        total += 1
        if v.value > band[1]:
            high += 1
        elif v.value < band[0]:
            low += 1
    if total < 20:
        return None

    share_high = round(100.0 * high / total, 1)
    share_low = round(100.0 * low / total, 1)
    return StepShares(
        share_high=share_high,
        share_low=share_low,
        n_days=total,
        detail=(
            f"{share_high}% of last {total} days above the historical p95, "
            f"{share_low}% below p5"
        ),
    )


def run_guards(
    values: Sequence[DailyValue],
    today: date,
    thresholds: GuardThresholds | None = None,
) -> list[GuardFlag]:
    """Every check, in order. An empty list means the series looks sound."""
    t = thresholds or GuardThresholds()
    flags: list[GuardFlag] = []

    stale = guard_stale(values, today, t.max_age)
    if stale is not None:
        # Nothing downstream is meaningful on a dead feed.
        return [stale]

    for flag in (
        guard_flatline(values, t.flat_days),
        guard_spike(values, t.spike_factor, min_record_days=t.min_record_days),
        guard_range(values, t.range_margin, min_record_days=t.min_record_days),
    ):
        if flag is not None:
            flags.append(flag)

    shares = datum_step(
        values, today, window=t.step_window, min_record_days=t.min_record_days
    )
    if shares is not None and max(shares.share_high, shares.share_low) >= t.step_share:
        flags.append(GuardFlag("datum_step", shares.detail))

    return flags
