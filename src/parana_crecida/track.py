"""When the crest now in the river reaches the gauges below it.

Everything else on this site describes what has already happened. This is
the one thing that points forward, so it is built to be conservative about
what it is willing to say.

**It says when, not how high.** A crest's height at the next gauge depends
on the channel it travels through and on whatever the tributaries between
the two gauges contribute, and there is no routing model here to work that
out. A level projected without one would be a guess wearing a number.

**It projects from the nearest crest, not the furthest.** The anchor is the
most *downstream* gauge that has already turned over, because every leg
added to the journey adds its own error. Projecting Rosario from Corrientes
when Goya has already crested throws away the better measurement.

**It refuses to cross a leg it cannot measure.** If any leg between the
anchor and a gauge has no confident travel time, that gauge gets no
projection at all - rather than a total that quietly skipped a stretch.
That is the same rule :func:`parana_crecida.lag.cumulative_travel` follows.

**The range is not a confidence interval, and is not described as one.**
It is what happens if every leg runs at its historical fast end, against
every leg at its slow end. Quantiles do not add, so summing the per-leg
p25s does not give the 25th percentile of the total journey; it gives a
wider, more pessimistic spread. Wider in the honest direction, and labelled
for what it is.

**A crest is provisional until it has actually fallen.** A river that has
been flat for three days may be over its peak or may be pausing. Both are
published, with the difference stated, because at the moment this was
written Corrientes sat 0.07 m below a peak it had risen 2.50 m to reach -
which is not yet a crest, it is a river that has stopped rising.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta

from . import chain as ch
from . import gauges as roster
from .lag import DEFAULT_TABLE, load_table

log = logging.getLogger(__name__)

#: How far back to look for the crest now in the river.
WINDOW_DAYS = 45

#: Days of no new high before a peak is called a turnover. Two is noise on
#: a river that routinely holds a level for a day; three is the shortest
#: that means anything.
TURNOVER_DAYS = 3

#: How far a river must fall from its peak, as a share of the rise that
#: reached it, before the crest is called confirmed rather than provisional.
CONFIRM_FALL_FRACTION = 0.10

#: A gauge is rising if it has gained this much over the last week. Below
#: it the gauge is holding, which is neither a rise nor a crest.
RISING_M = 0.03

#: Gauges that cannot anchor or receive a projection. Tide-affected Delta
#: gauges are excluded for the reason they are excluded everywhere else:
#: their level is partly the Rio de la Plata, not the Parana.
EXCLUDE_FLAGS = {"flow", "bad_snap", "unrouted", "tide_affected"}


@dataclass(frozen=True, slots=True)
class GaugeState:
    """Where one gauge stands in the wave now passing it."""

    series_id: str
    name: str
    river_km: float
    last_day: date
    last_value: float
    percentile: float | None
    peak_day: date
    peak_value: float
    rise_m: float
    fall_m: float
    weekly_change: float

    @property
    def days_since_peak(self) -> int:
        return (self.last_day - self.peak_day).days

    @property
    def turned(self) -> bool:
        """Has it stopped rising long enough to call the peak a peak?"""
        return self.days_since_peak >= TURNOVER_DAYS

    @property
    def confirmed(self) -> bool:
        """Has it actually come off the peak, or merely stopped rising?"""
        if not self.turned or self.rise_m <= 0:
            return False
        return self.fall_m >= CONFIRM_FALL_FRACTION * self.rise_m

    @property
    def rising(self) -> bool:
        return self.weekly_change >= RISING_M and not self.turned

    @property
    def status(self) -> str:
        if self.confirmed:
            return "fallen"
        if self.turned:
            return "cresting"
        if self.rising:
            return "rising"
        return "holding"


@dataclass(frozen=True, slots=True)
class Projection:
    """When the anchor's crest is due at one gauge downstream."""

    series_id: str
    name: str
    river_km: float
    km_from_anchor: float
    expected: date
    earliest: date
    latest: date
    legs: int
    percentile_now: float | None
    state: str

    def days_ahead(self, today: date) -> int:
        """Days from today, which is what a reader is actually asking."""
        return (self.expected - today).days

    def timing(self, as_of: date) -> str:
        """``ahead`` while it is still coming, ``due`` or ``overdue`` after.

        Judged against the last day of *data*, not today. INA's feed runs a
        day or two behind the calendar, and judging a projection against a
        date nobody has measured yet marks every gauge overdue by exactly
        the lag in the feed.

        A projection whose window has passed with the gauge still rising is
        not dropped. It is the most interesting thing here: the wave is
        running slower than the record says it should, or it is still being
        fed from upstream.
        """
        if self.expected > as_of:
            return "ahead"
        return "due" if self.latest >= as_of else "overdue"


@dataclass(frozen=True, slots=True)
class Track:
    """The wave now in the river, and where it is due."""

    anchor: GaugeState | None
    projections: list[Projection]
    states: list[GaugeState]
    reason: str | None = None


def gauge_state(
    series_id: str,
    name: str,
    river_km: float,
    values,
    clim,
) -> GaugeState | None:
    """Where this gauge stands, from its recent record alone."""
    if len(values) < 10:
        return None
    peak = max(values, key=lambda v: v.value)
    trough_before = min(
        (v for v in values if v.day <= peak.day), key=lambda v: v.value, default=peak
    )
    last = values[-1]
    week_ago = next(
        (v for v in reversed(values) if v.day <= last.day - timedelta(days=7)),
        values[0],
    )
    return GaugeState(
        series_id=series_id,
        name=name,
        river_km=river_km,
        last_day=last.day,
        last_value=round(last.value, 2),
        percentile=clim.percentile_of(series_id, last.day, last.value),
        peak_day=peak.day,
        peak_value=round(peak.value, 2),
        rise_m=round(peak.value - trough_before.value, 2),
        fall_m=round(peak.value - last.value, 2),
        weekly_change=round(last.value - week_ago.value, 2),
    )


def find_anchor(states: list[GaugeState]) -> GaugeState | None:
    """The gauge at the wave front: crested, with the river below it rising.

    Not simply the most downstream gauge that has turned over. Over any
    window that ends well after a flood, *every* gauge has turned, and
    "most downstream" then picks the last gauge on the chain - which has
    nothing below it to project to. The front is the boundary: the last
    gauge that has crested before the first one that has not.

    Downstream-most boundary on purpose. It is the shortest remaining
    journey, so it carries the least accumulated error. And a front with
    nothing rising below it is not a wave in progress at all, so that is
    required too.
    """
    ordered = sorted(states, key=lambda s: s.river_km)
    for index in range(len(ordered) - 2, -1, -1):
        here, below = ordered[index], ordered[index + 1]
        if not here.turned or below.turned:
            continue
        if any(s.rising for s in ordered[index + 1:]):
            return here
    return None


def _leg_index(rows: list[dict]) -> dict[str, dict]:
    return {str(row["from"]): row for row in rows}


def project(
    anchor: GaugeState,
    states: list[GaugeState],
    rows: list[dict],
) -> list[Projection]:
    """Walk downstream from the anchor, accumulating travel time."""
    legs = _leg_index(rows)
    by_id = {s.series_id: s for s in states}
    out: list[Projection] = []

    current = anchor.series_id
    median = low = high = 0.0
    count = 0
    while True:
        row = legs.get(current)
        if row is None:
            break
        if not row.get("confident") or row.get("median_days") is None:
            # A leg with no travel time ends the chain. Skipping it would
            # publish a journey that silently left a stretch of river out.
            log.info("no confident lag past %s; projections stop there", current)
            break
        median += float(row["median_days"])
        low += float(row.get("p25_days") or row["median_days"])
        high += float(row.get("p75_days") or row["median_days"])
        count += 1
        current = str(row["to"])

        state = by_id.get(current)
        if state is None:
            continue
        if state.turned:
            # The crest is already here; there is nothing to project.
            continue
        out.append(Projection(
            series_id=current,
            name=state.name,
            river_km=state.river_km,
            km_from_anchor=round(state.river_km - anchor.river_km, 1),
            expected=anchor.peak_day + timedelta(days=round(median)),
            earliest=anchor.peak_day + timedelta(days=math.floor(low)),
            latest=anchor.peak_day + timedelta(days=math.ceil(high)),
            legs=count,
            percentile_now=state.percentile,
            state=state.status,
        ))
    return out


def build(
    store,
    clim,
    *,
    nodes=None,
    rows: list[dict] | None = None,
    window_days: int = WINDOW_DAYS,
    today: date | None = None,
) -> Track:
    """The wave now in the river, and where its crest is due."""
    today = today or date.today()
    start = today - timedelta(days=window_days)
    nodes = nodes if nodes is not None else ch.load_chain()
    rows = rows if rows is not None else load_table(DEFAULT_TABLE)

    states: list[GaugeState] = []
    for node in sorted(nodes, key=lambda n: n.river_km):
        gauge = roster.BY_ID.get(node.series_id)
        if gauge is None or gauge.flags & EXCLUDE_FLAGS:
            continue
        # Truncated at `today` as well as started at `start`. The store
        # returns everything from `since` onwards, so without this a run
        # asked about a past date sees the whole record after it too - and
        # anchors on a flood that had not happened yet.
        values = [
            v for v in store.series(node.series_id, since=start) if v.day <= today
        ]
        state = gauge_state(
            node.series_id, gauge.label, node.river_km, values, clim
        )
        if state is not None:
            states.append(state)

    if not states:
        return Track(anchor=None, projections=[], states=[], reason="no_data")

    anchor = find_anchor(states)
    if anchor is None:
        # Every gauge is still rising. There is no crest to project yet,
        # and saying so is the honest output.
        return Track(
            anchor=None, projections=[], states=states, reason="no_crest_yet"
        )

    projections = project(anchor, states, rows)
    if not projections:
        # Two different silences. A crest below every gauge we can project
        # to is a wave on its way out; a crest on a reach with no measured
        # travel times - above Yacyreta, where the frozen table stops - is
        # a wave we simply cannot follow. Saying "past every gauge" for the
        # second would be wrong.
        reason = (
            "crest_past_every_gauge"
            if _leg_index(rows).get(anchor.series_id)
            else "no_measured_legs_below_anchor"
        )
        return Track(
            anchor=anchor, projections=[], states=states, reason=reason,
        )
    log.info(
        "anchor %s (peak %s, %s), %d projections",
        anchor.name, anchor.peak_day,
        "confirmed" if anchor.confirmed else "provisional", len(projections),
    )
    return Track(anchor=anchor, projections=projections, states=states)


def payload(track: Track, *, today: date | None = None) -> dict[str, object]:
    """The track as the page wants it."""
    today = today or date.today()
    anchor = track.anchor
    # Timing is judged against the newest reading, not today: see
    # Projection.timing.
    as_of = max((s.last_day for s in track.states), default=today)
    return {
        "schema": 1,
        "note": (
            "When the crest now in the river is due at the gauges below it, "
            "from travel times measured over the whole record. It says when, "
            "not how high. The range is every leg at its fast end against "
            "every leg at its slow end, not a confidence interval."
        ),
        "generated_for": today.isoformat(),
        "as_of": as_of.isoformat(),
        "reason": track.reason,
        "anchor": None if anchor is None else {
            "series_id": anchor.series_id,
            "name": anchor.name,
            "river_km": anchor.river_km,
            "peak_day": anchor.peak_day.isoformat(),
            "peak_value": anchor.peak_value,
            "last_day": anchor.last_day.isoformat(),
            "last_value": anchor.last_value,
            "days_since_peak": anchor.days_since_peak,
            "rise_m": anchor.rise_m,
            "fall_m": anchor.fall_m,
            "confirmed": anchor.confirmed,
        },
        "projections": [
            {
                "series_id": p.series_id,
                "name": p.name,
                "river_km": p.river_km,
                "km_from_anchor": p.km_from_anchor,
                "expected": p.expected.isoformat(),
                "earliest": p.earliest.isoformat(),
                "latest": p.latest.isoformat(),
                "days_ahead": p.days_ahead(today),
                "legs": p.legs,
                "percentile_now": (
                    None if p.percentile_now is None else round(p.percentile_now, 1)
                ),
                "state": p.state,
                "timing": p.timing(as_of),
            }
            for p in track.projections
        ],
    }
