"""How long a crest takes to get from one gauge to the next.

Measured, not modelled. Two estimators, reported side by side because they
fail differently and agreeing is itself evidence:

**Crest matching.** Pair each upstream crest with the downstream crest that
follows it inside a physically plausible window. Interpretable - every pair
is two real dates you can go and check - and it yields a *distribution*,
which is the honest output: travel time is not one number, it varies with
how big the wave is and how full the floodplain already was.

**Cross-correlation.** Slide the two gauges' anomaly series against each
other and take the lag that lines them up best. Uses the whole record
rather than just the peaks, so it is steadier when crests are few, but it
is a single number with no story attached.

Both are constrained to a plausible celerity band. Without that, matching
happily pairs a crest with the *next* flood two months later, and
cross-correlation locks onto the annual cycle - every gauge on the Parana
is high in summer, so a 365-day lag correlates beautifully and means
nothing.

Everything here is description, not prediction. These are the travel times
the river has shown, which is not a forecast of the next one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import numpy as np

from .chain import Leg
from .events import Crest

log = logging.getLogger(__name__)

#: Plausible celerity band for the Parana, in km/day. Wide on purpose: it
#: is here to exclude the annual cycle and the next flood, not to encode an
#: answer. Observed values land well inside it.
MIN_CELERITY = 20.0
MAX_CELERITY = 300.0

#: A matched pair must be at least this alike in size, as a ratio of the
#: smaller crest's anomaly to the larger. A big wave upstream does not
#: arrive as a ripple.
MIN_MAGNITUDE_RATIO = 0.25

#: Below this many matched pairs, report the lag but do not call it settled.
MIN_PAIRS_FOR_CONFIDENCE = 5

#: At or below this many days, a +/-1 day quantisation is a huge share of
#: the measurement, so the leg's own km/day is not meaningful.
RESOLUTION_LIMIT_DAYS = 2.0


@dataclass(frozen=True, slots=True)
class Match:
    """One upstream crest paired with its downstream arrival."""

    upstream: Crest
    downstream: Crest
    lag_days: int


@dataclass(frozen=True, slots=True)
class LegLag:
    """Travel time over one stretch of river."""

    leg: Leg
    matches: list[Match]
    median_days: float | None
    p25_days: float | None
    p75_days: float | None
    correlation_days: float | None

    @property
    def km(self) -> float:
        return self.leg.km

    @property
    def n(self) -> int:
        return len(self.matches)

    @property
    def confident(self) -> bool:
        return self.n >= MIN_PAIRS_FOR_CONFIDENCE and self.median_days is not None

    @property
    def celerity(self) -> float | None:
        """km per day from the median lag."""
        if not self.median_days:
            return None
        return self.km / self.median_days

    @property
    def estimators_agree(self) -> bool | None:
        """Do crest matching and cross-correlation land within two days?"""
        if self.median_days is None or self.correlation_days is None:
            return None
        return abs(self.median_days - self.correlation_days) <= 2.0

    @property
    def resolution_limited(self) -> bool:
        """Is this leg too short to measure at one-day resolution?

        San Lorenzo to Rosario is 21 km. At any sane celerity the crest
        covers that in a few hours, but daily readings can only record 0, 1
        or 2 days, so the measured celerity says more about the sampling
        interval than about the river. Such legs still carry a usable
        *arrival date*; their km/day is not worth quoting.
        """
        return self.median_days is None or self.median_days <= RESOLUTION_LIMIT_DAYS


def plausible_window(km: float) -> tuple[int, int]:
    """``(min_days, max_days)`` a crest could take to cover ``km``."""
    fastest = max(1, int(np.floor(km / MAX_CELERITY)))
    slowest = max(fastest + 1, int(np.ceil(km / MIN_CELERITY)))
    return fastest, slowest


def _magnitude(crest: Crest) -> float:
    return abs(crest.anomaly) if crest.anomaly is not None else crest.prominence


def match_crests(
    upstream: list[Crest],
    downstream: list[Crest],
    km: float,
    *,
    high_only: bool = True,
    min_magnitude_ratio: float = MIN_MAGNITUDE_RATIO,
) -> list[Match]:
    """Pair upstream crests with their downstream arrivals.

    Each downstream crest is claimed at most once, by the upstream crest it
    best fits, so one big flood cannot be counted as several arrivals.
    """
    lo, hi = plausible_window(km)
    ups = [c for c in upstream if c.is_high or not high_only]
    downs = [c for c in downstream if c.is_high or not high_only]
    if not ups or not downs:
        return []

    claimed: dict[date, Match] = {}
    for crest in ups:
        best: Match | None = None
        for arrival in downs:
            lag = (arrival.day - crest.day).days
            if lag < lo or lag > hi:
                continue
            up_mag, down_mag = _magnitude(crest), _magnitude(arrival)
            if max(up_mag, down_mag) > 0:
                ratio = min(up_mag, down_mag) / max(up_mag, down_mag)
                if ratio < min_magnitude_ratio:
                    continue
            candidate = Match(crest, arrival, lag)
            # Nearest plausible arrival wins; a later crest in the window is
            # more likely the next event than this one arriving slowly.
            if best is None or lag < best.lag_days:
                best = candidate
        if best is None:
            continue
        held = claimed.get(best.downstream.day)
        if held is None or best.lag_days < held.lag_days:
            claimed[best.downstream.day] = best

    matches = sorted(claimed.values(), key=lambda m: m.upstream.day)
    log.debug("%.0f km: %d matches from %d/%d crests", km, len(matches), len(ups), len(downs))
    return matches


def cross_correlate(
    upstream: list[tuple[date, float]],
    downstream: list[tuple[date, float]],
    km: float,
) -> float | None:
    """The lag in days that best lines up two anomaly series.

    Restricted to the plausible window, which is what stops it locking onto
    the annual cycle that every gauge on the river shares.
    """
    lo, hi = plausible_window(km)
    up = dict(upstream)
    down = dict(downstream)
    if not up or not down:
        return None

    common_start = max(min(up), min(down))
    common_end = min(max(up), max(down))
    if (common_end - common_start).days < 365:
        return None

    best_lag, best_score = None, -np.inf
    for lag in range(lo, hi + 1):
        pairs = [
            (value, down[day + _days(lag)])
            for day, value in up.items()
            if common_start <= day <= common_end and (day + _days(lag)) in down
        ]
        if len(pairs) < 200:
            continue
        a = np.array([p[0] for p in pairs])
        b = np.array([p[1] for p in pairs])
        if a.std() < 1e-9 or b.std() < 1e-9:
            continue
        score = float(np.corrcoef(a, b)[0, 1])
        if score > best_score:
            best_lag, best_score = lag, score
    return float(best_lag) if best_lag is not None else None


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


def leg_lag(
    leg: Leg,
    crests: dict[str, list[Crest]],
    anomalies: dict[str, list[tuple[date, float]]] | None = None,
) -> LegLag:
    """Travel time over one leg, by both estimators."""
    matches = match_crests(
        crests.get(leg.upstream.series_id, []),
        crests.get(leg.downstream.series_id, []),
        leg.km,
    )
    lags = np.array([m.lag_days for m in matches], dtype=float)
    median = float(np.median(lags)) if lags.size else None
    p25 = float(np.percentile(lags, 25)) if lags.size else None
    p75 = float(np.percentile(lags, 75)) if lags.size else None

    correlation = None
    if anomalies:
        correlation = cross_correlate(
            anomalies.get(leg.upstream.series_id, []),
            anomalies.get(leg.downstream.series_id, []),
            leg.km,
        )

    return LegLag(
        leg=leg,
        matches=matches,
        median_days=median,
        p25_days=p25,
        p75_days=p75,
        correlation_days=correlation,
    )


def cumulative_travel(legs: list[LegLag]) -> list[tuple[str, float | None]]:
    """Running travel time from the first gauge, where every leg is known.

    Stops at the first leg with no estimate rather than skipping it: a total
    that quietly omitted a stretch would read as the whole journey.
    """
    out: list[tuple[str, float | None]] = []
    total: float | None = 0.0
    for leg in legs:
        if total is not None and leg.median_days is not None:
            total += leg.median_days
        else:
            total = None
        out.append((leg.leg.downstream.series_id, total))
    return out


@dataclass(frozen=True, slots=True)
class CelerityFit:
    """One celerity for a whole run of river."""

    km_per_day: float
    total_km: float
    total_days: float
    legs_used: int
    legs_skipped: int

    @property
    def days_per_100km(self) -> float:
        return 100.0 / self.km_per_day


def fit_celerity(legs: list[LegLag]) -> CelerityFit | None:
    """Celerity over the whole chain, not leg by leg.

    Summing distance and time before dividing is the point. Every leg's lag
    is rounded to a whole day, and on a 21 km leg that rounding swamps the
    answer; over 780 km the roundings cancel rather than compound. A leg
    with no estimate is skipped and counted, never treated as zero days.
    """
    usable = [L for L in legs if L.median_days is not None]
    if not usable:
        return None
    total_km = sum(L.km for L in usable)
    total_days = sum(L.median_days for L in usable)  # type: ignore[misc]
    if total_days <= 0:
        return None
    return CelerityFit(
        km_per_day=total_km / total_days,
        total_km=total_km,
        total_days=total_days,
        legs_used=len(usable),
        legs_skipped=len(legs) - len(usable),
    )
