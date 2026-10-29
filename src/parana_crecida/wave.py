"""Assembling the wave view.

Pulls the chain, the crests and the measured travel times into one payload:
the recent record for every gauge on the chain, laid out by river-km, with
crests marked and the historical travel time for each stretch.

The historical fit uses the whole record - 46 to 143 years per gauge - while
the plotted window is the last few months. That split matters: there may be
no crest at all in the window (a wave still rising has not crested yet), and
the travel times should still be there, because they come from a century of
previous waves rather than from this one.

Nothing here predicts. "This stretch has historically taken nine days" is a
statement about the past, and the page says so.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ina_a5.observations import DEFAULT_TZ

from . import chain as ch
from . import gauges as roster
from .events import Crest, anomaly_series, detect
from .lag import CelerityFit, LegLag, fit_celerity, leg_lag
from .normalize import Climatology
from .store import Store

log = logging.getLogger(__name__)

#: How much recent record the plot shows.
WINDOW_DAYS = 180

#: Crests older than this are history, not "the current picture".
RECENT_CREST_DAYS = 365


@dataclass(frozen=True, slots=True)
class GaugeWave:
    """One gauge's row in the wave view."""

    node: ch.ChainNode
    tier: int
    #: Day offsets from the window start, with the matching readings.
    offsets: list[int]
    values: list[float]
    anomalies: list[float | None]
    percentiles: list[float | None]
    crests: list[Crest]
    rising: bool | None


@dataclass(frozen=True, slots=True)
class WaveAnalysis:
    """Everything the wave view needs."""

    window_start: date
    window_end: date
    gauges: list[GaugeWave]
    legs: list[LegLag]
    celerity: CelerityFit | None


def analyse(
    store: Store,
    clim: Climatology,
    nodes: list[ch.ChainNode],
    *,
    window_days: int = WINDOW_DAYS,
    today: date | None = None,
) -> WaveAnalysis:
    """Detect crests, fit travel times, and frame the plotting window."""
    today = today or datetime.now(DEFAULT_TZ).date()
    window_start = today - timedelta(days=window_days)

    crests: dict[str, list[Crest]] = {}
    anomalies: dict[str, list[tuple[date, float]]] = {}
    gauges: list[GaugeWave] = []

    for node in nodes:
        full = store.series(node.series_id)
        if not full:
            log.warning("no data for %s; leaving it out of the wave view", node.name)
            continue

        # Crests and lags come from the whole record; only the plot is
        # windowed. A wave still rising has no crest yet, and the travel
        # times must not vanish because of that.
        crests[node.series_id] = detect(node.series_id, full, clim)
        anomalies[node.series_id] = anomaly_series(node.series_id, full, clim)

        recent = [v for v in full if v.day >= window_start]
        offsets = [(v.day - window_start).days for v in recent]
        values = [round(v.value, 2) for v in recent]
        anoms = [
            None if (a := clim.anomaly(node.series_id, v.day, v.value)) is None
            else round(a, 3)
            for v in recent
        ]
        pcts = [
            None if (p := clim.percentile_of(node.series_id, v.day, v.value)) is None
            else round(p, 1)
            for v in recent
        ]

        rising = None
        if len(recent) >= 8:
            rising = recent[-1].value > recent[-8].value

        gauges.append(
            GaugeWave(
                node=node,
                tier=roster.gauge(node.series_id).tier,
                offsets=offsets,
                values=values,
                anomalies=anoms,
                percentiles=pcts,
                crests=[c for c in crests[node.series_id] if c.day >= window_start],
                rising=rising,
            )
        )

    legs = [leg_lag(leg, crests, anomalies) for leg in ch.routing_legs(nodes)]
    return WaveAnalysis(
        window_start=window_start,
        window_end=today,
        gauges=gauges,
        legs=legs,
        celerity=fit_celerity(legs),
    )


def _crest_payload(crest: Crest) -> dict[str, object]:
    return {
        "day": crest.day.isoformat(),
        "value": round(crest.value, 2),
        "percentile": None if crest.percentile is None else round(crest.percentile, 1),
        "anomaly": None if crest.anomaly is None else round(crest.anomaly, 2),
        "is_high": crest.is_high,
        "rise_days": crest.rise_days,
    }


def _leg_payload(leg: LegLag) -> dict[str, object]:
    return {
        "from": leg.leg.upstream.series_id,
        "from_name": leg.leg.upstream.name,
        "to": leg.leg.downstream.series_id,
        "to_name": leg.leg.downstream.name,
        "km": round(leg.km, 1),
        "median_days": leg.median_days,
        "p25_days": leg.p25_days,
        "p75_days": leg.p75_days,
        "correlation_days": leg.correlation_days,
        "n": leg.n,
        "confident": leg.confident,
        "agree": leg.estimators_agree,
        # Short legs carry a usable arrival date but no meaningful km/day.
        "resolution_limited": leg.resolution_limited,
        "celerity": None if leg.resolution_limited or not leg.celerity
        else round(leg.celerity, 1),
    }


def build_payload(
    analysis: WaveAnalysis, *, now: datetime | None = None
) -> dict[str, object]:
    """The wave payload. Pure: builds the dict, writes nothing."""
    now = now or datetime.now(DEFAULT_TZ)
    display = {g.series_id: g.label for g in roster.GAUGES}

    return {
        "schema": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "window": {
            "start": analysis.window_start.isoformat(),
            "end": analysis.window_end.isoformat(),
            "days": (analysis.window_end - analysis.window_start).days,
        },
        "celerity": None
        if analysis.celerity is None
        else {
            "km_per_day": round(analysis.celerity.km_per_day, 1),
            "total_km": round(analysis.celerity.total_km, 1),
            "total_days": round(analysis.celerity.total_days, 1),
            "legs_used": analysis.celerity.legs_used,
            "legs_skipped": analysis.celerity.legs_skipped,
        },
        "gauges": [
            {
                "series_id": g.node.series_id,
                "name": display.get(g.node.series_id, g.node.name),
                "river_km": g.node.river_km,
                "tier": g.tier,
                "rising": g.rising,
                "offsets": g.offsets,
                "values": g.values,
                "anomalies": g.anomalies,
                "percentiles": g.percentiles,
                "crests": [_crest_payload(c) for c in g.crests],
            }
            for g in analysis.gauges
        ],
        "legs": [_leg_payload(leg) for leg in analysis.legs],
    }
