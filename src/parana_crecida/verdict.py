"""What the river is doing, in one sentence, derived rather than written.

Every other module here answers "what is the reading". This one answers
the question a person actually arrives with: *is the river behaving
normally, and if not, where?* A page that opens with thirty-seven gauges
makes the reader do that work themselves, and most will not.

The sentence is assembled by the page from the parts below, in either
language. Nothing here is a phrase: it is counts, a reach, a date and a
ratio. That matters, because a headline written by hand is true on the day
it is written and quietly wrong a fortnight later.

**Where the thresholds come from.** The state is decided on the share of
the chain sitting at or above its own day-of-year 95th percentile. The p95
curve is what the rest of the site already uses to mean "high for the time
of year", so the verdict does not introduce a new definition of unusual -
it counts the one already in use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from . import chain as ch
from . import gauges as roster

log = logging.getLogger(__name__)

#: A gauge is "high" at or above its own day-of-year p95, and "at its
#: maximum" at p100 - the top of what has ever been recorded for the date.
HIGH_PERCENTILE = 95.0
TOP_PERCENTILE = 99.9

#: Share of the chain needed for each verdict. Deliberately blunt: these
#: decide one word, and a word that changes on a single gauge flickering
#: across a threshold would be worse than a blunt one.
EXTREME_SHARE = 0.4
HIGH_SHARE = 0.25

#: Gauges whose level is not a river signal cannot vote. The same rule the
#: daily river line uses, for the same reason.
EXCLUDE_FLAGS = {"flow", "bad_snap", "unrouted", "tide_affected"}


@dataclass(frozen=True, slots=True)
class Reading:
    series_id: str
    name: str
    river_km: float
    percentile: float


@dataclass(frozen=True, slots=True)
class Verdict:
    """The finding, in parts a page can assemble into a sentence."""

    state: str
    as_of: date | None
    counted: int
    high: int
    at_top: int
    readings: list[Reading] = field(default_factory=list)

    @property
    def share_high(self) -> float:
        return self.high / self.counted if self.counted else 0.0

    def longest_run(self, threshold: float) -> tuple[Reading, Reading] | None:
        """The longest unbroken run of gauges at or above ``threshold``.

        A run is what makes a flood legible - "from Puerto Iguazu to
        Corrientes" is a thing a reader can picture, where "fifteen gauges"
        is not. Unbroken on purpose: a count can be scattered the length of
        the river and mean something quite different.
        """
        best: tuple[int, int] | None = None
        start: int | None = None
        for index, reading in enumerate(self.readings):
            if reading.percentile >= threshold:
                start = index if start is None else start
                if best is None or index - start > best[1] - best[0]:
                    best = (start, index)
            else:
                start = None
        if best is None or best[0] == best[1]:
            return None
        return self.readings[best[0]], self.readings[best[1]]


def classify(high: int, counted: int) -> str:
    if not counted:
        return "unknown"
    share = high / counted
    if share >= EXTREME_SHARE:
        return "extreme"
    if share >= HIGH_SHARE:
        return "high"
    return "normal"


def build(store, clim, *, today: date | None = None) -> Verdict:
    """Read the chain once and decide what it is saying."""
    readings: list[Reading] = []
    last: date | None = None

    for node in sorted(ch.load_chain(), key=lambda n: n.river_km):
        gauge = roster.BY_ID.get(node.series_id)
        if gauge is None or gauge.flags & EXCLUDE_FLAGS:
            continue
        values = store.series(node.series_id)
        if not values:
            continue
        latest = values[-1]
        if today is not None and latest.day > today:
            continue
        percentile = clim.percentile_of(node.series_id, latest.day, latest.value)
        if percentile is None:
            continue
        last = latest.day if last is None else max(last, latest.day)
        readings.append(Reading(
            series_id=node.series_id,
            name=gauge.label,
            river_km=node.river_km,
            percentile=round(percentile, 1),
        ))

    high = sum(1 for r in readings if r.percentile >= HIGH_PERCENTILE)
    at_top = sum(1 for r in readings if r.percentile >= TOP_PERCENTILE)
    verdict = Verdict(
        state=classify(high, len(readings)),
        as_of=last,
        counted=len(readings),
        high=high,
        at_top=at_top,
        readings=readings,
    )
    log.info(
        "verdict %s: %d of %d at or above p%g, %d at the top",
        verdict.state, high, len(readings), HIGH_PERCENTILE, at_top,
    )
    return verdict


def payload(verdict: Verdict, *, track: dict | None = None,
            flooded: dict | None = None) -> dict[str, object]:
    """The verdict as the page wants it, with its supporting evidence.

    The evidence is the point. A finding on a public page that a reader
    cannot check is just an assertion in a larger font, so each part of the
    sentence carries the number it came from.
    """
    run = verdict.longest_run(HIGH_PERCENTILE)
    top_run = verdict.longest_run(TOP_PERCENTILE)

    front = None
    if track and track.get("anchor"):
        anchor = track["anchor"]
        front = {
            "gauge": anchor["name"],
            "day": anchor["peak_day"],
            "confirmed": anchor["confirmed"],
            "river_km": anchor["river_km"],
        }

    water = None
    if flooded:
        best = None
        for stretch in flooded.get("stretches", []):
            if stretch.get("calibrated") is False:
                continue
            latest, baseline = stretch.get("latest"), stretch.get("baseline_km2")
            if not latest or not baseline:
                continue
            ratio = latest["km2"] / baseline
            if best is None or ratio > best["ratio"]:
                best = {
                    "stretch": f"{stretch['from_name']} - {stretch['to_name']}",
                    "ratio": round(ratio, 1),
                    "km2": latest["km2"],
                    "day": latest["day"],
                    "tracks_river": stretch.get("stage_correlation"),
                }
        water = best

    return {
        "state": verdict.state,
        "as_of": verdict.as_of.isoformat() if verdict.as_of else None,
        "counted": verdict.counted,
        "high": verdict.high,
        "at_top": verdict.at_top,
        "high_percentile": HIGH_PERCENTILE,
        "reach": None if run is None else {
            "from": run[0].name, "to": run[1].name,
            "from_km": run[0].river_km, "to_km": run[1].river_km,
        },
        "top_reach": None if top_run is None else {
            "from": top_run[0].name, "to": top_run[1].name,
        },
        "front": front,
        "water": water,
    }
