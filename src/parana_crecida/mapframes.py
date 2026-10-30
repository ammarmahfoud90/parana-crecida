"""Georeferenced water overlays for the map.

The loop in :mod:`animation` is a picture of one stretch. This is the same
measurement cut for a map: transparent PNGs, each tagged with the bounds it
covers, so they can be laid over a real basemap with towns, roads and
departamento lines underneath.

Two differences from the per-pass series, both deliberate:

**Twelve-day composites, not single passes.** Sentinel-1's swath is narrower
than this run of river, and the corridors sit across swath edges: Goya to La
Paz gets full coverage only five times in six months, while Esquina to La
Paz gets it twenty times. A composite over one repeat cycle covers every
stretch at 99.6% every time, which is what a whole-river animation needs.
The cost is that a frame is a window rather than an instant, and the page
says so.

**One threshold per stretch.** Each corridor was calibrated separately
against JRC permanent water, landing at -16.0 or -16.5 dB. The stretches are
masked and thresholded individually, then mosaicked, so no stretch is
measured with another's number.

Land is transparent. Only water is drawn, in two colours: the permanent
channel and everything beyond it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import flooded as fl

log = logging.getLogger(__name__)

DEFAULT_DIR = Path("site/media/map")
DEFAULT_INDEX = Path("site/data/map_frames.json")

#: One Sentinel-1 repeat cycle.
WINDOW_DAYS = 12
#: How many cycles back the animation runs.
CYCLES = 15
#: Longest edge of each overlay. The run is about 800 km by 110 km, so this
#: is roughly 500 m per pixel - coarse close up, but the basemap carries the
#: detail and the overlay only has to show where the water is.
FRAME_PX = 1600

COLOUR_CHANNEL = (0.13, 0.40, 0.72)
COLOUR_FLOOD = (0.95, 0.42, 0.09)


@dataclass(frozen=True, slots=True)
class MapFrame:
    """One composite, rendered and georeferenced."""

    start: date
    end: date
    path: Path
    bounds: list[list[float]]
    km2: dict[str, float]


def union_geometry(stretches: list[fl.Stretch]):
    """One geometry covering every corridor."""
    import ee

    return ee.Geometry.MultiPolygon(
        [fl.corridor_geometry(s) for s in stretches]
    ).dissolve(maxError=1000)


def water_image(stretches: list[fl.Stretch], start: date, end: date):
    """Water over the whole run, each stretch at its own threshold.

    Returns an image where only water carries colour and everything else is
    masked out, so the basemap shows through rather than a grey rectangle.
    """
    import ee

    permanent = ee.Image(fl.GSW).select("occurrence").gt(90).unmask(0)
    masks = []
    corridors = []
    for stretch in stretches:
        corridor = fl.corridor_geometry(stretch)
        corridors.append(corridor)
        mosaic = fl._collection(corridor, start, end).mosaic().clip(corridor)
        masks.append(
            fl.water_mask(
                mosaic,
                threshold_db=stretch.threshold_db,
                slope_mask=stretch.slope_mask,
            )
            .unmask(0)
            .clip(corridor)
        )

    detected = ee.ImageCollection(masks).mosaic().unmask(0)
    inside = ee.Image.constant(1).clip(
        ee.Geometry.MultiPolygon(corridors).dissolve(maxError=1000)
    ).mask()

    # The permanent channel is drawn whether or not this window's mask
    # caught it: it is a constant, not a measurement, and without it the
    # river breaks into fragments wherever the radar was ambiguous.
    visible = detected.Or(permanent).And(inside)
    painted = ee.Image.rgb(*COLOUR_FLOOD).where(permanent, ee.Image.rgb(*COLOUR_CHANNEL))
    return painted.updateMask(visible), detected


def stretch_areas(
    stretches: list[fl.Stretch], start: date, end: date
) -> dict[str, float]:
    """Water area per stretch for this window, so the frame carries numbers."""
    out: dict[str, float] = {}
    for stretch in stretches:
        corridor = fl.corridor_geometry(stretch)
        value = fl.water_area_km2(
            corridor, start, end,
            threshold_db=stretch.threshold_db, slope_mask=stretch.slope_mask,
        )
        if value is not None:
            out[stretch.key] = value
    return out


def render(
    *,
    out_dir: Path = DEFAULT_DIR,
    index_path: Path = DEFAULT_INDEX,
    cycles: int = CYCLES,
    window_days: int = WINDOW_DAYS,
    px: int = FRAME_PX,
    today: date | None = None,
    with_areas: bool = True,
) -> dict[str, object]:
    """Render one transparent overlay per cycle, plus an index for the page."""
    import ee
    import requests

    stretches = fl.load_corridors()
    if not stretches:
        raise ValueError("no corridors defined")

    today = today or date.today()
    last = today - timedelta(days=fl.LAG_DAYS)
    union = union_geometry(stretches)
    region = union.bounds()
    coords = region.getInfo()["coordinates"][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    # Leaflet wants [[south, west], [north, east]].
    bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]

    calibrated = {s.key for s in stretches if s.calibrated}
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[MapFrame] = []
    for index in range(cycles - 1, -1, -1):
        end = last - timedelta(days=window_days * index)
        start = end - timedelta(days=window_days - 1)
        painted, _ = water_image(stretches, start, end)
        # visualize() turns the float RGB into 8-bit bands; the mask comes
        # through as the PNG's alpha channel, which is what makes the land
        # transparent over the basemap.
        # crs matters, and its absence was a bug worth naming. getThumbURL
        # renders in EPSG:4326 by default - linear in latitude - while
        # Leaflet's imageOverlay stretches the image linearly across the
        # bounds in EPSG:3857, which is not. The two only agree at the top
        # and bottom edges; in between the overlay slides toward the pole.
        # Over this run's 8.5 degrees of latitude that put the water up to
        # 10 km south of the river it was meant to be lying on, worst near
        # -29.65. Rendering in Mercator to begin with makes Leaflet's linear
        # placement the right thing to do.
        url = painted.visualize(min=0, max=1).getThumbURL(
            {"region": region, "dimensions": px, "format": "png",
             "crs": "EPSG:3857"}
        )
        path = out_dir / f"water_{end.isoformat()}.png"
        response = requests.get(url, timeout=300)
        response.raise_for_status()
        path.write_bytes(response.content)
        areas = stretch_areas(stretches, start, end) if with_areas else {}
        frames.append(
            MapFrame(start=start, end=end, path=path, bounds=bounds, km2=areas)
        )
        log.info("rendered %s to %s (%d KB)", start, end, len(response.content) // 1024)

    payload = {
        "schema": 1,
        "note": (
            "Each frame is a 12-day Sentinel-1 composite, not a single "
            "instant. The swath is narrower than this run of river, so a "
            "whole-river picture needs a full repeat cycle."
        ),
        "window_days": window_days,
        "bounds": bounds,
        "generated_for": today.isoformat(),
        "frames": [
            {
                "start": f.start.isoformat(),
                "end": f.end.isoformat(),
                "image": f"media/map/{f.path.name}",
                "km2": {k: round(v, 1) for k, v in f.km2.items()},
                # Only calibrated stretches count toward the total: the two
                # gorge reaches publish a picture, not a number.
                "total_km2": (
                    round(sum(v for k, v in f.km2.items() if k in calibrated), 1)
                    if f.km2 else None
                ),
            }
            for f in frames
        ],
        "stretches": [
            {
                "key": s.key,
                "calibrated": s.calibrated,
                "from_name": s.from_name,
                "to_name": s.to_name,
                "river_km": s.river_km,
                "threshold_db": s.threshold_db,
                "centreline": s.centreline,
            }
            for s in stretches
        ],
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return payload
