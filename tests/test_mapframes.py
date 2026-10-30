"""The georeferencing of the radar overlays.

An overlay that is drawn beautifully and placed wrongly is worse than no
overlay, because it looks like a measurement. This caught a real one: the
frames were rendered in EPSG:4326, which is linear in latitude, and laid
over the map by Leaflet's ``imageOverlay``, which stretches an image
linearly across its bounds in EPSG:3857, which is not. The two agree only
at the top and bottom edges. In between the water slid toward the pole -
up to 10 km over this run's 8.5 degrees of latitude - so the flood was
drawn next to the river rather than on it.

Nothing in the pipeline noticed. The areas were right, the calibration was
right, the colours were right, and the picture was wrong. A reader spotted
it by looking at the map.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

SITE = Path("site")
INDEX = SITE / "data/map_frames.json"
CORRIDORS = Path("data/corridors.json")

#: How far from a centreline vertex to look for drawn water. The channel is
#: narrow and the walked centreline is a simplification of it, so a
#: kilometre is generous without being meaningless - the misprojection this
#: guards against was ten times that.
RADIUS_KM = 1.0

#: The whole river, and every band of it. The overall figure alone would
#: hide the failure, because a misprojection is perfect at the edges.
MIN_OVERALL_PCT = 95
MIN_BAND_PCT = 85

pytestmark = pytest.mark.skipif(
    not INDEX.exists() or not CORRIDORS.exists(),
    reason="no rendered frames to check",
)


def merc(lat: float) -> float:
    """Web Mercator y, the way Leaflet places an image overlay."""
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def test_the_frames_are_rendered_in_the_projection_they_are_drawn_in():
    """Stated at the call site, so a future edit has to mean it."""
    source = Path("src/parana_crecida/mapframes.py").read_text(encoding="utf-8")
    assert '"crs": "EPSG:3857"' in source, (
        "getThumbURL defaults to EPSG:4326, which Leaflet will place wrongly"
    )


def water_coverage():
    """``(overall_pct, {latitude_band: pct})`` for the newest frame."""
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")

    index = json.loads(INDEX.read_text(encoding="utf-8"))
    (south, west), (north, east) = index["bounds"]
    frame = index["frames"][-1]
    path = SITE / frame["image"]
    if not path.exists():
        pytest.skip(f"{frame['image']} is not committed")

    image = Image.open(path).convert("RGBA")
    width, height = image.size
    pixels = image.load()
    y_north, y_south = merc(north), merc(south)
    km_per_px = (east - west) * 98.0 / width
    radius = max(1, round(RADIUS_KM / km_per_px))

    # Only the stretches this frame actually draws. Sampling every
    # corridor asks whether water was drawn on a reach the overlay
    # deliberately leaves blank, and scores the refusal as a failure: with
    # the Delta withheld, its centreline dragged one latitude band to 55%
    # while every other band was at 100%.
    bands: dict[int, list[bool]] = {}
    drawn = {s["key"] for s in index["stretches"]}
    corridors = json.loads(CORRIDORS.read_text(encoding="utf-8"))
    for stretch in corridors["stretches"]:
        if stretch["key"] not in drawn:
            continue
        for lon, lat in stretch["centreline"][::3]:
            if not (south <= lat <= north and west <= lon <= east):
                continue
            x = round((lon - west) / (east - west) * width)
            y = round((y_north - merc(lat)) / (y_north - y_south) * height)
            found = any(
                0 <= x + dx < width and 0 <= y + dy < height
                and pixels[x + dx, y + dy][3] > 0
                for dx in range(-radius, radius + 1)
                for dy in range(-radius, radius + 1)
            )
            bands.setdefault(int(lat), []).append(found)

    assert bands, "no centreline vertex fell inside the frame"
    flat = [hit for v in bands.values() for hit in v]
    return (
        sum(flat) / len(flat) * 100,
        {b: sum(v) / len(v) * 100 for b, v in bands.items()},
        {b: len(v) for b, v in bands.items()},
    )


def test_the_drawn_water_lies_on_the_river():
    overall, _, _ = water_coverage()
    assert overall >= MIN_OVERALL_PCT, (
        f"only {overall:.0f}% of the river's centreline has water drawn on "
        f"it; the overlay is not where the river is"
    )


def test_no_stretch_of_the_river_is_left_dry():
    """The shape of the failure, not just its size.

    A misprojection is perfect at the top and bottom edges and worst in the
    middle, so an overall figure can look respectable while the centre of
    the map is badly wrong. Before this was fixed the overall was 67% and
    two latitude bands sat near 47%.
    """
    _, by_band, counts = water_coverage()
    bad = {
        band: round(pct)
        for band, pct in by_band.items()
        if pct < MIN_BAND_PCT and counts[band] >= 10
    }
    assert not bad, f"latitude bands with the water off the river: {bad}"
