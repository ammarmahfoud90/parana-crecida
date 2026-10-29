"""The river coloured by how high it is, day by day.

The satellite says where the water is, every twelve days. This says how
abnormal the river is at every point along it, every day - which is what
actually shows a wave travelling.

It works because stage is measured daily at 23 stations along this run and
because the day-of-year percentile makes those stations comparable: every
gauge has its own zero, so 5.7 m is a flood at Goya and an ordinary day at
Puerto Iguazu, but "above the 95th percentile for late October" means the
same thing everywhere.

Between stations the percentile is interpolated along river distance, not
map distance. Two points 20 km apart on a meander are far closer along the
water than two points 20 km apart across a bend.

**The line breaks at Yacyreta.** Above the dam is a reservoir pool and
below it a regulated river; they are hydraulically separate, and a smooth
gradient across the wall would be plainly wrong while looking perfectly
smooth. The same applies at Itaipu, which sits above the top of this run.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import chain as ch
from . import flooded as fl
from . import gauges as roster

log = logging.getLogger(__name__)

DEFAULT_OUT = Path("site/data/river_stage.json")

#: How finely the river line is cut. Each piece gets one colour, so this
#: sets how smoothly the gradient reads; 5 km over 1,477 km is ~300 pieces,
#: which draws instantly and looks continuous.
SEGMENT_KM = 5.0

WINDOW_DAYS = 180

#: Yacyreta's wall, which separates the reservoir from the regulated river
#: below it. The line is cut at the nearest point on the centreline.
DAMS = (
    {"name": "Yacyreta", "lon": -56.7230, "lat": -27.4830},
)

#: A gauge may not anchor the river's colour if its level is not a river
#: signal. ``flow`` is a different quantity; ``bad_snap`` and ``unrouted``
#: have no trustworthy position to anchor to; and ``tide_affected`` Delta
#: gauges rise with the Rio de la Plata, so painting the Parana with their
#: percentile would show a tide as a flood wave.
EXCLUDE = {"flow", "bad_snap", "unrouted", "tide_affected"}

KM_PER_DEG_LON = ch.KM_PER_DEG_LON
KM_PER_DEG_LAT = ch.KM_PER_DEG_LAT


@dataclass(frozen=True, slots=True)
class Vertex:
    """A point on the river with its distance along it."""

    lon: float
    lat: float
    river_km: float


def walk_km(points: list[list[float]]) -> list[float]:
    """Cumulative distance along a run of coordinates."""
    run = [0.0]
    for index in range(1, len(points)):
        dx = (points[index][0] - points[index - 1][0]) * KM_PER_DEG_LON
        dy = (points[index][1] - points[index - 1][1]) * KM_PER_DEG_LAT
        run.append(run[-1] + math.hypot(dx, dy))
    return run


def centreline_with_km() -> list[Vertex]:
    """Every stretch's centreline joined into one line, with river-km.

    Distance is anchored to the chain at *both* ends of each stretch, not
    accumulated from the upstream gauge and left to run on. The two differ:
    walking the centreline's vertices gives 1.6 to 10.2 km more per stretch
    than the chain's gauge-to-gauge spacing, because the two measure the
    same river over slightly different geometry. Accumulating the walk would
    carry each stretch past where the next one starts, putting the gauges a
    few km off their own line.

    So each stretch's walk is scaled onto its chain spacing. Within a
    stretch that is a uniform stretch of at most 7%, which is well inside
    the precision these river-km claim; across the run it makes every gauge
    land exactly on its own anchor, and the line monotonic by construction.
    """
    out: list[Vertex] = []
    stretches = fl.load_corridors()
    nodes = {n.series_id: n for n in ch.load_chain()}
    stretches = sorted(
        stretches,
        key=lambda s: nodes[s.from_id].river_km if s.from_id in nodes else 0.0,
    )

    for stretch in stretches:
        # Draw no coloured river below the last gauge that can colour it.
        # The Delta stretch exists for the radar overlay, but its downstream
        # gauge at Ibicuy is tide-affected and may not anchor a percentile;
        # drawing it anyway would extrapolate the last upstream value over
        # 191 km of delta, which would look like measurement and be nothing
        # of the kind.
        downstream = roster.BY_ID.get(stretch.to_id)
        if downstream is None or downstream.flags & EXCLUDE:
            log.info(
                "the coloured line stops above %s: no gauge below it can "
                "anchor a percentile", stretch.to_name,
            )
            break
        points = stretch.centreline
        run = walk_km(points)
        base = nodes[stretch.from_id].river_km if stretch.from_id in nodes else 0.0
        span = (
            nodes[stretch.to_id].river_km - base
            if stretch.to_id in nodes and stretch.from_id in nodes
            else run[-1]
        )
        scale = span / run[-1] if run[-1] else 1.0

        for (lon, lat), distance in zip(points, run):
            vertex = Vertex(lon=lon, lat=lat, river_km=round(base + distance * scale, 2))
            # Stretches share their end gauges, so skip a repeated vertex.
            if out and abs(out[-1].river_km - vertex.river_km) < 1e-6:
                continue
            out.append(vertex)
    out.sort(key=lambda v: v.river_km)
    return out


def dam_positions(line: list[Vertex]) -> list[float]:
    """River-km of each dam, from the nearest vertex on the line."""
    positions = []
    for dam in DAMS:
        best = min(
            line,
            key=lambda v: math.hypot(
                (v.lon - dam["lon"]) * KM_PER_DEG_LON,
                (v.lat - dam["lat"]) * KM_PER_DEG_LAT,
            ),
        )
        positions.append(best.river_km)
        log.info("%s sits at river km %.0f", dam["name"], best.river_km)
    return sorted(positions)


def segments(line: list[Vertex], breaks: list[float], step_km: float = SEGMENT_KM):
    """Cut the line into coloured pieces, split at the dams.

    Returns ``[{"break_after": bool, "points": [[lat, lon], ...], "km": float}]``.
    """
    pieces: list[dict] = []
    current: list[Vertex] = []
    anchor = line[0].river_km

    for vertex in line:
        current.append(vertex)
        crossed_dam = any(
            current[0].river_km < b <= vertex.river_km for b in breaks
        )
        if vertex.river_km - anchor >= step_km or crossed_dam:
            if len(current) > 1:
                pieces.append({
                    "points": [[round(v.lat, 5), round(v.lon, 5)] for v in current],
                    "km": round((current[0].river_km + vertex.river_km) / 2, 1),
                    "dam_break": crossed_dam,
                })
            current = [vertex]
            anchor = vertex.river_km

    if len(current) > 1:
        pieces.append({
            "points": [[round(v.lat, 5), round(v.lon, 5)] for v in current],
            "km": round((current[0].river_km + current[-1].river_km) / 2, 1),
            "dam_break": False,
        })
    return pieces


def build(
    store,
    clim,
    *,
    window_days: int = WINDOW_DAYS,
    today: date | None = None,
) -> dict[str, object]:
    """The daily percentile at every gauge, plus the line to paint."""
    today = today or date.today()
    start = today - timedelta(days=window_days)

    line = centreline_with_km()
    breaks = dam_positions(line)
    pieces = segments(line, breaks)

    nodes = {n.series_id: n for n in ch.load_chain()}
    coords = {}
    layer = Path("site/data/gauges.geojson")
    if layer.exists():
        for feature in json.loads(layer.read_text(encoding="utf-8"))["features"]:
            coords[feature["properties"]["series_id"]] = feature["geometry"]["coordinates"]

    collected: list[tuple[object, dict]] = []
    for series_id, node in sorted(nodes.items(), key=lambda kv: kv[1].river_km):
        gauge = roster.BY_ID.get(series_id)
        if gauge is None or gauge.flags & EXCLUDE:
            continue
        values = store.series(series_id, since=start)
        if not values:
            continue
        collected.append((
            {v.day: clim.percentile_of(series_id, v.day, v.value) for v in values},
            {
                "series_id": series_id,
                "name": gauge.label,
                "river_km": node.river_km,
                "lonlat": coords.get(series_id),
                "raw": {v.day: round(v.value, 2) for v in values},
            },
        ))

    # The day axis is the union over every gauge, not the first gauge's own
    # days. Gauges do not all report to the same day - the top of the river
    # often lags the rest by one - and taking the first one's axis silently
    # dropped the newest day from the whole river.
    days: list[date] = sorted({d for by_day, _ in collected for d in by_day})

    anchors: list[dict] = []
    for by_day, meta in collected:
        raw = meta.pop("raw")
        anchors.append({
            **meta,
            "pct": [
                None if by_day.get(d) is None else round(by_day[d], 1) for d in days
            ],
            "value": [raw.get(d) for d in days],
        })

    log.info("%d anchors, %d days, %d line pieces", len(anchors), len(days), len(pieces))
    return {
        "schema": 1,
        "note": (
            "Daily stage at each gauge as a day-of-year percentile, "
            "interpolated along river distance between gauges. The line is "
            "cut at Yacyreta: the pool above the dam and the river below it "
            "are hydraulically separate."
        ),
        "generated_for": today.isoformat(),
        "segment_km": SEGMENT_KM,
        "dams": [
            {"name": d["name"], "river_km": km, "lonlat": [d["lon"], d["lat"]]}
            for d, km in zip(DAMS, breaks)
        ],
        "days": [d.isoformat() for d in days],
        "anchors": anchors,
        "segments": pieces,
    }


def write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
