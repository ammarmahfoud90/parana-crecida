"""Finding rises and crests in a gauge's record.

A flood wave shows up at a gauge as a rise, a crest and a recession. This
module finds the crests, which is what travel time is measured between.

Three choices worth knowing about:

**Timing is done on the raw series, not the normalised one.** Within one
gauge, raw stage is perfectly comparable with itself, and normalising first
would push the peak around for no gain. The percentile and anomaly are
computed *at* the crest, for describing it.

**Smoothing is centred.** Daily stage is noisy enough that a raw local
maximum is often a wobble, but a trailing or exponential filter shifts peaks
later by roughly half its window - and since the same shift would land on
every gauge, it would look harmless while quietly biasing the lags it is
fed into. A centred mean has no phase lag.

**Gaps are not silently bridged.** A short gap is interpolated; a long one
splits the record, and no crest is reported spanning it. Inventing a peak
inside a month of missing data is exactly the kind of thing that then gets
published as a travel time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from ina_a5 import DailyValue
from scipy.signal import find_peaks

from .normalize import Climatology

log = logging.getLogger(__name__)

#: Centred window, in days. Wide enough to kill daily noise, narrow enough
#: to leave a crest where it actually is.
SMOOTH_DAYS = 5
#: A crest must stand this many interquantile spreads above its surroundings.
MIN_PROMINENCE_SPREADS = 0.4
#: Two crests closer than this are one event seen twice.
MIN_SEPARATION_DAYS = 12
#: Gaps up to this are interpolated; longer ones split the record.
MAX_GAP_DAYS = 5
#: A segment shorter than this cannot support a crest plus its rise.
MIN_SEGMENT_DAYS = 30


@dataclass(frozen=True, slots=True)
class Crest:
    """One peak in a gauge's record."""

    series_id: str
    day: date
    value: float
    #: Where the crest sits in this gauge's day-of-year distribution.
    percentile: float | None
    anomaly: float | None
    #: How far the crest stands above its surroundings, in raw units.
    prominence: float
    rise_start: date | None
    rise_days: int | None
    #: Rise in raw units per day, over the rise.
    rise_rate: float | None

    @property
    def is_high(self) -> bool:
        """Above the day-of-year p90: a flood crest rather than a ripple."""
        return self.percentile is not None and self.percentile >= 90.0


@dataclass(frozen=True, slots=True)
class Segment:
    """A run of days with no long gap."""

    start: date
    days: np.ndarray
    values: np.ndarray
    smoothed: np.ndarray


def _centred_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Rolling mean with no phase lag, with the ends left as they are."""
    if window <= 1 or values.size < window:
        return values.astype(float)
    kernel = np.ones(window) / window
    smoothed = np.convolve(values, kernel, mode="same")
    # convolve's 'same' tapers the ends against implicit zeros; keep the raw
    # values there instead of a dip that could read as a trough.
    half = window // 2
    smoothed[:half] = values[:half]
    smoothed[-half:] = values[-half:]
    return smoothed


def to_segments(
    values: list[DailyValue],
    *,
    max_gap_days: int = MAX_GAP_DAYS,
    smooth_days: int = SMOOTH_DAYS,
) -> list[Segment]:
    """Put a gappy series on a daily grid and split it at long gaps."""
    if not values:
        return []
    ordered = sorted(values, key=lambda v: v.day)
    segments: list[list[DailyValue]] = [[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:]):
        if (current.day - previous.day).days > max_gap_days:
            segments.append([current])
        else:
            segments[-1].append(current)

    out: list[Segment] = []
    for chunk in segments:
        span = (chunk[-1].day - chunk[0].day).days + 1
        if span < MIN_SEGMENT_DAYS:
            continue
        start = chunk[0].day
        grid = np.arange(span)
        known_x = np.array([(v.day - start).days for v in chunk], dtype=float)
        known_y = np.array([v.value for v in chunk], dtype=float)
        filled = np.interp(grid, known_x, known_y)
        out.append(
            Segment(
                start=start,
                days=grid,
                values=filled,
                smoothed=_centred_mean(filled, smooth_days),
            )
        )
    return out


def _typical_spread(
    series_id: str, values: list[DailyValue], clim: Climatology
) -> float:
    """The gauge's own scale, for setting a prominence threshold.

    From the climatology where there is one, so the bar is "unusual for this
    gauge at this time of year" rather than a number picked for the Parana
    and wrong everywhere else.
    """
    spreads = [
        curve.spread
        for v in values[:: max(1, len(values) // 200)]
        if (curve := clim.curve(series_id, v.day)) is not None
    ]
    if spreads:
        return float(np.median(spreads))
    # No climatology: fall back to the series' own interquantile width.
    raw = np.array([v.value for v in values], dtype=float)
    return float(max(np.percentile(raw, 90) - np.percentile(raw, 10), 1e-6))


def detect(
    series_id: str,
    values: list[DailyValue],
    clim: Climatology,
    *,
    smooth_days: int = SMOOTH_DAYS,
    min_prominence_spreads: float = MIN_PROMINENCE_SPREADS,
    min_separation_days: int = MIN_SEPARATION_DAYS,
    max_gap_days: int = MAX_GAP_DAYS,
    since: date | None = None,
) -> list[Crest]:
    """Every crest in one gauge's record, oldest first."""
    if not values:
        return []
    spread = _typical_spread(series_id, values, clim)
    prominence = max(min_prominence_spreads * spread, 1e-6)

    crests: list[Crest] = []
    for segment in to_segments(
        values, max_gap_days=max_gap_days, smooth_days=smooth_days
    ):
        peaks, props = find_peaks(
            segment.smoothed,
            prominence=prominence,
            distance=max(1, min_separation_days),
        )
        troughs, _ = find_peaks(-segment.smoothed, distance=max(1, min_separation_days))

        for index, peak in enumerate(peaks):
            day = segment.start + timedelta(days=int(segment.days[peak]))
            if since is not None and day < since:
                continue
            # The crest's value is the real reading at that day, not the
            # smoothed one: smoothing is for locating the peak, not for
            # reporting how high the river got.
            value = float(segment.values[peak])

            before = troughs[troughs < peak]
            rise_start = rise_days = rise_rate = None
            if before.size:
                trough = int(before[-1])
                rise_start = segment.start + timedelta(days=int(segment.days[trough]))
                rise_days = int(peak - trough)
                if rise_days > 0:
                    rise_rate = (value - float(segment.values[trough])) / rise_days

            crests.append(
                Crest(
                    series_id=series_id,
                    day=day,
                    value=value,
                    percentile=clim.percentile_of(series_id, day, value),
                    anomaly=clim.anomaly(series_id, day, value),
                    prominence=float(props["prominences"][index]),
                    rise_start=rise_start,
                    rise_days=rise_days,
                    rise_rate=rise_rate,
                )
            )

    crests.sort(key=lambda c: c.day)
    log.debug("series %s: %d crests", series_id, len(crests))
    return crests


def anomaly_series(
    series_id: str, values: list[DailyValue], clim: Climatology
) -> list[tuple[date, float]]:
    """The series as day-of-year anomalies, for cross-gauge comparison."""
    out = []
    for v in values:
        a = clim.anomaly(series_id, v.day, v.value)
        if a is not None:
            out.append((v.day, a))
    return out


def latest_crest(crests: list[Crest], *, high_only: bool = True) -> Crest | None:
    """The most recent crest, optionally only a flood-sized one."""
    chosen = [c for c in crests if c.is_high] if high_only else list(crests)
    return chosen[-1] if chosen else None
