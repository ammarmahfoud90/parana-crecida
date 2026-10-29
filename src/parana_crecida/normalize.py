"""Making gauges comparable.

Raw stage cannot be compared between gauges. Every gauge has its own zero,
so Puerto Iguazu reads about 29 m where San Fernando reads about 1 m, and
5.7 m is a flood at Goya and an ordinary day at Puerto Iguazu. Anything that
puts two gauges on the same axis - a cross-correlation, a colour scale, a
sentence like "the wave is bigger at Parana than at Corrientes" - has to go
through here first.

The reference is rios-argentina's day-of-year percentile curves, built from
46 to 143 years of record per gauge, so a value is placed against what that
gauge normally does *at that time of year*. A 5 m reading in February and a
5 m reading in August are not the same event.

Two measures, because they fail in opposite ways:

``percentile_of``
    Where the value sits in the historical distribution, 0 to 100. Right for
    "how unusual is this", and what the site should say. Useless for timing:
    during a real flood everything is pinned near 100, and a flat top has no
    locatable peak.

``anomaly``
    ``(value - median) / (p90 - p10)`` - a robust z-score. Unbounded, so it
    keeps the shape of a crest no matter how large, which is what the
    travel-time work needs. Useless as a headline number, because a reader
    has no feel for "2.4 interquantile ranges above normal".

Use the first to describe, the second to measure.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ina_a5 import doy

log = logging.getLogger(__name__)

DEFAULT_CLIMATOLOGY = Path("data/climatology_parana.json")

#: The percentiles rios-argentina stores per day of year.
LEVELS = (5, 10, 25, 50, 75, 90, 95)

#: Guard against dividing by a degenerate spread at a gauge that barely moves.
MIN_SPREAD = 1e-6


@dataclass(frozen=True, slots=True)
class DoyCurve:
    """What one gauge normally does on one day of the year."""

    p5: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    p95: float

    @property
    def spread(self) -> float:
        """p90 minus p10: a robust width, unbothered by record extremes."""
        return max(self.p90 - self.p10, MIN_SPREAD)

    def points(self) -> list[tuple[float, float]]:
        """``(percentile, value)`` pairs, ascending by value."""
        return [
            (5.0, self.p5),
            (10.0, self.p10),
            (25.0, self.p25),
            (50.0, self.p50),
            (75.0, self.p75),
            (90.0, self.p90),
            (95.0, self.p95),
        ]


class Climatology:
    """Day-of-year percentile curves, by series."""

    def __init__(self, data: dict[str, dict]) -> None:
        self._data = data
        self._cache: dict[tuple[str, int], DoyCurve | None] = {}

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CLIMATOLOGY) -> Climatology:
        path = Path(path)
        if not path.exists():
            log.warning("no climatology at %s; nothing can be normalised", path)
            return cls({})
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def __contains__(self, series_id: str) -> bool:
        return str(series_id) in self._data

    def years(self, series_id: str) -> int:
        return int(self._data.get(str(series_id), {}).get("years") or 0)

    def curve(self, series_id: str, day: date) -> DoyCurve | None:
        """The curve for this gauge on this day of year, or ``None``."""
        key = (str(series_id), doy(day))
        if key in self._cache:
            return self._cache[key]

        entry = self._data.get(str(series_id))
        curve = None
        if entry:
            raw = (entry.get("percentiles") or {}).get(str(doy(day)))
            if raw:
                try:
                    curve = DoyCurve(*(float(raw[str(p)]) for p in LEVELS))
                except (KeyError, TypeError, ValueError):
                    curve = None
        self._cache[key] = curve
        return curve

    def percentile_of(
        self, series_id: str, day: date, value: float | None
    ) -> float | None:
        """Where ``value`` sits in the historical distribution, 0 to 100.

        Linear between the stored percentiles. Beyond the ends it continues
        the nearest segment's slope and clamps, so a record-breaking flood
        reads 100 rather than something invented - the curve simply does not
        know what lies past p95.
        """
        if value is None:
            return None
        curve = self.curve(series_id, day)
        if curve is None:
            return None
        points = curve.points()

        low_pct, low_val = points[0]
        if value <= low_val:
            next_pct, next_val = points[1]
            step = max(next_val - low_val, MIN_SPREAD)
            return max(0.0, low_pct - (low_val - value) / step * (next_pct - low_pct))

        high_pct, high_val = points[-1]
        if value >= high_val:
            prev_pct, prev_val = points[-2]
            step = max(high_val - prev_val, MIN_SPREAD)
            return min(
                100.0, high_pct + (value - high_val) / step * (high_pct - prev_pct)
            )

        for (p_lo, v_lo), (p_hi, v_hi) in zip(points, points[1:]):
            if v_lo <= value <= v_hi:
                step = max(v_hi - v_lo, MIN_SPREAD)
                return p_lo + (value - v_lo) / step * (p_hi - p_lo)
        return None  # unreachable while points are sorted

    def anomaly(
        self, series_id: str, day: date, value: float | None
    ) -> float | None:
        """``(value - median) / (p90 - p10)`` for this day of year.

        Unbounded on purpose: this is what carries the shape of a crest, and
        a measure that saturates would flatten the very peak we are trying
        to time.
        """
        if value is None:
            return None
        curve = self.curve(series_id, day)
        if curve is None:
            return None
        return (value - curve.p50) / curve.spread

    def is_high(
        self, series_id: str, day: date, value: float | None, *, above: float = 90.0
    ) -> bool:
        """Is this value above the given day-of-year percentile?"""
        pct = self.percentile_of(series_id, day, value)
        return pct is not None and pct >= above


def trim(source: Path | str, series_ids: list[str], out: Path) -> int:
    """Copy just the gauges in scope out of a full climatology file.

    rios-argentina's is 5 MB across 148 series; ours needs 45 of them, and a
    file this repo commits should hold what this repo uses.
    """
    full = json.loads(Path(source).read_text(encoding="utf-8"))
    wanted = {str(s) for s in series_ids}
    kept = {k: v for k, v in full.items() if str(k) in wanted}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(kept, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    missing = sorted(wanted - set(kept))
    if missing:
        log.warning("%d gauges have no climatology: %s", len(missing), ", ".join(missing))
    return len(kept)
