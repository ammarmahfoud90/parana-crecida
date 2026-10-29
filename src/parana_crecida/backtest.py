"""How well the arrival projections have actually done.

A projection nobody has scored is a guess with a date on it. This runs the
tracker over the whole record, as it would have run on the day, and
compares what it would have published against what the river then did.

The result is frozen to ``data/track_skill.json`` and published beside the
projection, so the page states its own accuracy instead of leaving the
reader to assume it.

Two things make the scoring honest rather than flattering:

**The crest is found, not chosen.** For each projected gauge the score uses
the *first* local maximum after the projection was made - the crest of this
wave. Taking the largest peak in the next three months would quietly score
against a later, bigger flood and look far better than it is.

**There is a baseline.** The same scoring is applied to a projection with no
travel time in it at all, which claims the crest reaches every gauge on the
day the anchor peaked. A method that cannot beat that has added nothing,
and the comparison is the only way to know.

There is a floor under how well anything can do here. A Parana flood crest
sits within 10 cm of its peak for a median of six days, so "the day of the
crest" is itself only defined to about four days either way. That is
measured by :func:`crest_flatness` rather than asserted.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import chain as ch
from . import gauges as roster
from . import track as tk

log = logging.getLogger(__name__)

DEFAULT_OUT = Path("data/track_skill.json")

#: Days either side used to call a point a local maximum.
SETTLE_DAYS = 3
#: How far ahead to look for the crest that was projected.
HORIZON_DAYS = 90
#: Minimum readings around a target before it can be scored at all.
MIN_READINGS = 30


@dataclass(frozen=True, slots=True)
class Score:
    """One projection, against what the river actually did."""

    made_on: date
    anchor: str
    target: str
    legs: int
    expected: date
    actual: date
    inside_window: bool
    baseline_error: int

    @property
    def error(self) -> int:
        return (self.actual - self.expected).days


def local_maxima(values, settle: int = SETTLE_DAYS):
    """Points that are a high over ``settle`` days back and not beaten for ``settle`` ahead."""
    out = []
    for i in range(settle, len(values) - settle):
        before = max(v.value for v in values[i - settle:i])
        after = all(values[j].value <= values[i].value for j in range(i + 1, i + 1 + settle))
        if values[i].value >= before and after:
            out.append(values[i])
    return out


def score_day(store, clim, day: date, rows=None) -> list[Score]:
    """What the tracker would have published on ``day``, scored."""
    track = tk.build(store, clim, rows=rows, today=day)
    if track.anchor is None:
        return []
    out: list[Score] = []
    for projection in track.projections:
        values = [
            v for v in store.series(projection.series_id, since=day - timedelta(days=10))
            if v.day <= day + timedelta(days=HORIZON_DAYS)
        ]
        if len(values) < MIN_READINGS:
            continue
        peaks = local_maxima([v for v in values if v.day >= day])
        if not peaks:
            continue
        actual = peaks[0].day
        out.append(Score(
            made_on=day,
            anchor=track.anchor.name,
            target=projection.name,
            legs=projection.legs,
            expected=projection.expected,
            actual=actual,
            inside_window=projection.earliest <= actual <= projection.latest,
            baseline_error=abs((actual - track.anchor.peak_day).days),
        ))
    return out


def probe_days(first: int, last: int) -> list[date]:
    """Two probes a month across the years, regardless of what the river did."""
    return [
        date(year, month, dom)
        for year in range(first, last + 1)
        for month in range(1, 13)
        for dom in (1, 15)
    ]


def crest_flatness(store, clim, *, tolerance_m: float = 0.10) -> dict[str, float] | None:
    """How many days a flood crest sits within ``tolerance_m`` of its peak.

    The floor under any arrival projection: if the crest itself lasts a
    week, no method places it to the day.
    """
    spans: list[int] = []
    for node in ch.load_chain():
        gauge = roster.BY_ID.get(node.series_id)
        if gauge is None or gauge.flags & tk.EXCLUDE_FLAGS:
            continue
        values = store.series(node.series_id)
        if len(values) < 3000:
            continue
        by_day = {v.day: v.value for v in values}
        annual: dict[int, object] = {}
        for v in values:
            best = annual.get(v.day.year)
            if best is None or v.value > best.value:
                annual[v.day.year] = v
        for peak in annual.values():
            pct = clim.percentile_of(node.series_id, peak.day, peak.value)
            if pct is None or pct < 90:
                continue
            spans.append(sum(
                1 for offset in range(-20, 21)
                if (value := by_day.get(peak.day + timedelta(days=offset))) is not None
                and peak.value - value <= tolerance_m
            ))
    if not spans:
        return None
    spans.sort()
    return {
        "tolerance_m": tolerance_m,
        "crests": len(spans),
        "median_days": spans[len(spans) // 2],
        "mean_days": round(sum(spans) / len(spans), 1),
    }


def summarise(scores: list[Score]) -> dict[str, object]:
    """The numbers the page states about itself."""
    if not scores:
        return {"scored": 0}
    errors = sorted(abs(s.error) for s in scores)
    n = len(errors)
    baseline = sorted(s.baseline_error for s in scores)
    return {
        "scored": n,
        "occasions": len({s.made_on for s in scores}),
        "inside_window": sum(1 for s in scores if s.inside_window),
        "inside_window_pct": round(
            sum(1 for s in scores if s.inside_window) / n * 100
        ),
        "median_error_days": errors[n // 2],
        "mean_error_days": round(sum(errors) / n, 1),
        "within_3_days_pct": round(sum(1 for e in errors if e <= 3) / n * 100),
        "within_7_days_pct": round(sum(1 for e in errors if e <= 7) / n * 100),
        "baseline_median_error_days": baseline[n // 2],
        # Read this one carefully. The error is *worst* one leg from the
        # anchor and best at four to seven, which is not the river getting
        # more predictable with distance. It is the scorer: next door to
        # the anchor the projected date is only a day or two out, and the
        # first local maximum after it is as likely to be a ripple as the
        # crest. Far from the anchor there is more river between the two
        # and the first maximum is more often the real thing. The short
        # legs are scored pessimistically, not measured better.
        "by_legs": [
            {
                "legs": legs,
                "n": len(group),
                "median_error_days": sorted(group)[len(group) // 2],
            }
            for legs, group in sorted(
                _group(scores).items()
            )
        ],
    }


def _group(scores: list[Score]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for s in scores:
        out.setdefault(s.legs, []).append(abs(s.error))
    return out


def run(
    store,
    clim,
    *,
    first_year: int = 1998,
    last_year: int | None = None,
    rows=None,
) -> dict[str, object]:
    """Score every occasion the tracker would have published."""
    last_year = last_year or date.today().year
    scores: list[Score] = []
    for day in probe_days(first_year, last_year):
        if day > date.today():
            break
        scores.extend(score_day(store, clim, day, rows=rows))
    log.info("scored %d projections", len(scores))
    return {
        "schema": 1,
        "note": (
            "The tracker run over the record as it would have run on the "
            "day, scored against the first local maximum each gauge then "
            "reached. The baseline is the same projection with no travel "
            "time in it: the crest arriving everywhere on the day the "
            "anchor peaked."
        ),
        "measured_on": date.today().isoformat(),
        "window": {"from": first_year, "to": last_year},
        "crest_flatness": crest_flatness(store, clim),
        **summarise(scores),
    }


def write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def load(path: Path | str = DEFAULT_OUT) -> dict[str, object]:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
