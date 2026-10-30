"""How well a river level predicts how much ground is under water.

Everyone publishes a flooded area. Nobody publishes how much that figure
would move if you measured the same river, at the same level, a month
apart. This does, and the answer is uncomfortable enough to be worth the
page it takes.

The frozen record behind it (``data/area_stage.json``) is one twelve-day
Sentinel-1 composite every few weeks since 2016, paired with the stage at
each stretch's downstream gauge. Stage is kept as metres above that
gauge's own INA alert level, which is the only way to pool gauges: every
gauge has its own zero, so raw metres cannot be compared between Goya and
Parana, while metres above alert mean the same thing at both.

**What it shows.** Water area does rise with the river - about 1.35 times
the low-water baseline when the river is three metres below alert, about
1.96 as it reaches alert. But two observations at the *same* level differ
by about half, and the river level accounts for six per cent of the
variance in the measurement. The scatter is the size of the trend.

**Why that is the finding and not a failure.** It is a statement about
what twelve-day radar composites can resolve, not about the river. The
honest conclusion is that a single flooded-area number at a given stage is
good to roughly a factor of two, and the many maps that publish one
without a range are claiming precision that this data does not support.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_SOURCE = Path("data/area_stage.json")

#: Edges for the summary bands, in metres relative to the alert level.
#: Wider at the bottom, where the river spends most of its time, and
#: narrow near alert, which is the part anyone cares about.
BAND_EDGES = (-6.0, -4.0, -3.0, -2.0, -1.0, 0.0, 2.0)

#: Fewest observations a band needs before its mean is published. Below
#: this the band is dropped rather than drawn thin, because a mean of four
#: points on a chart reads exactly like a mean of eighty.
MIN_BAND = 8

#: Where "low water" and "near alert" are taken from, for the headline
#: comparison.
LOW_WATER_BELOW = -3.0
NEAR_ALERT_ABOVE = -1.0


def load(path: Path | str = DEFAULT_SOURCE) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _sd(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = (
        sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)
    ) ** 0.5
    return None if den == 0 else num / den


def build(source: dict) -> dict[str, object]:
    """The cloud, its bands, and the numbers the page states."""
    rows = source.get("observations") or []
    if not rows:
        return {}

    xs = [r["above_alert_m"] for r in rows]
    ys = [r["ratio_to_bajante"] for r in rows]

    bands = []
    for lo, hi in zip(BAND_EDGES, BAND_EDGES[1:]):
        inside = [y for x, y in zip(xs, ys) if lo <= x < hi]
        if len(inside) < MIN_BAND:
            continue
        mean = _mean(inside)
        sd = _sd(inside)
        bands.append({
            "from_m": lo,
            "to_m": hi,
            "n": len(inside),
            "mean": round(mean, 2),
            "sd": round(sd, 2),
            "spread_pct": round(100 * sd / mean) if mean else None,
        })

    low = [y for x, y in zip(xs, ys) if x < LOW_WATER_BELOW]
    high = [y for x, y in zip(xs, ys) if x >= NEAR_ALERT_ABOVE]
    r = _pearson(xs, ys)

    # The typical disagreement between two readings at the same level,
    # taken as the average within-band spread rather than the overall
    # spread - the overall figure would include the trend itself.
    typical = (
        round(_mean([b["spread_pct"] for b in bands if b["spread_pct"]]))
        if bands else None
    )

    return {
        "schema": 1,
        "note": (
            "One 12-day Sentinel-1 composite every few weeks since 2016, "
            "against the stage at each stretch's downstream gauge, as "
            "metres above that gauge's official alert level."
        ),
        "built_on": source.get("built_on"),
        "window_days": source.get("window_days"),
        "n": len(rows),
        "stretches": sorted({r["key"] for r in rows}),
        "above_alert": sum(1 for x in xs if x >= 0),
        "r": None if r is None else round(r, 2),
        "r2": None if r is None else round(r * r, 2),
        "low_water": {
            "below_m": LOW_WATER_BELOW,
            "n": len(low),
            "mean": round(_mean(low), 2) if low else None,
        },
        "near_alert": {
            "above_m": NEAR_ALERT_ABOVE,
            "n": len(high),
            "mean": round(_mean(high), 2) if high else None,
        },
        "trend": (
            round(_mean(high) / _mean(low), 2) if low and high else None
        ),
        "typical_spread_pct": typical,
        "bands": bands,
        "observations": [
            {
                "x": r["above_alert_m"],
                "y": r["ratio_to_bajante"],
                "key": r["key"],
                "day": r["day"],
            }
            for r in rows
        ],
    }
