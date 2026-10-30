"""The river as it looks from orbit, under everything else.

The map draws water over OpenStreetMap, which is honest but abstract: a
street map says where the towns are and nothing about what the ground is
doing. This renders the same corridors as a false-colour Sentinel-2
composite, so the islands, the channels, the flooded fields and the dry
ones are all visible as themselves.

**The band choice is the whole thing.** Red takes SWIR1, green takes the
near infrared, blue takes the visible blue:

- vegetation reflects strongly in the near infrared and weakly in SWIR,
  so it lands bright green
- water absorbs both infrared bands and, on a river this muddy, keeps a
  good deal of visible red, so it reads deep navy rather than black -
  which is what separates a channel from a shadow
- ploughed ground reflects strongly in SWIR and moderately in red, so it
  comes out pink
- towns reflect moderately across all three and stay grey

That is why a flooded field is distinguishable from a dry one here and not
on a natural-colour image, where both are brown.

**Rendered per stretch, not as one mosaic.** The whole run is 1,440 km of
river inside a bounding box mostly made of land it does not touch. One
image covering all of it at a useful resolution would be enormous and
almost entirely wasted; nine images, each following its own corridor, put
the pixels where the river is.

Like the water overlays these are georeferenced in EPSG:3857, because
Leaflet places an image overlay linearly in Web Mercator and an image
drawn in anything else lands beside the river rather than on it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import flooded as fl

log = logging.getLogger(__name__)

DEFAULT_DIR = Path("site/media/basemap")
DEFAULT_INDEX = Path("site/data/basemap.json")

S2 = "COPERNICUS/S2_SR_HARMONIZED"

#: How far back to gather scenes. Long enough that the cloud mask leaves
#: something over every pixel, short enough that it is this season's river
#: and not an average of two.
WINDOW_DAYS = 90
#: Scenes cloudier than this are not worth compositing even after masking.
MAX_CLOUD_PCT = 60
#: Longest edge of each stretch's image. Each corridor is roughly 200 km of
#: river in a 40 km band, so this lands near 120 m per pixel - the detail
#: at which an island reads as an island.
FRAME_PX = 2048

#: Earth Engine returns PNG, which for photographic imagery is enormous:
#: nine stretches came to 29 MB, which is not a map page, it is a
#: download. WebP keeps the alpha channel the corridor edge needs and
#: costs about an eighth of that.
WEBP_QUALITY = 80

#: Red: SWIR2. Green: near infrared. Blue: the visible red band.
#:
#: The blue channel is the surprise, and it is the Parana's doing. The
#: obvious choice is the blue band, and it cannot work here: measured over
#: this corridor, bare ground reflects *less* blue than water does (344
#: against 420), so no linear stretch can make soil pink and water navy at
#: the same time. The visible red band can, because the Parana carries so
#: much sediment that water is the brightest of the three in it - 695,
#: against 568 for ploughed ground and 348 for vegetation. A clear river
#: would not behave this way.
BANDS = ("B12", "B8", "B4")

#: Reflectance is scaled by 10,000 in the surface-reflectance product.
#: Set from the medians actually measured over the corridor rather than by
#: eye - water 110/269/695, vegetation 604/2224/356, bare 1456/1583/631 -
#: so that water lands near rgb(0, 6, 126), vegetation near rgb(71, 191,
#: 30) and ploughed ground near rgb(192, 131, 108). Bright dry fields clip
#: the red channel on purpose: that is where the pink comes from.
VIS_MIN = (100, 200, 250)
VIS_MAX = (1900, 2900, 1150)


@dataclass(frozen=True, slots=True)
class Tile:
    key: str
    path: Path
    bounds: list[list[float]]
    scenes: int


def _masked(image):
    """One scene with cloud, shadow, snow and cirrus taken out.

    Uses the scene classification the product already carries rather than
    a separate cloud model: it is good enough over a river valley and
    costs nothing extra to read.
    """
    import ee

    scl = image.select("SCL")
    # 3 shadow, 8 medium-probability cloud, 9 high, 10 cirrus, 11 snow.
    bad = (
        scl.eq(3).Or(scl.eq(8)).Or(scl.eq(9)).Or(scl.eq(10)).Or(scl.eq(11))
    )
    return image.updateMask(bad.Not())


def composite(corridor, start: date, end: date):
    """A cloud-free median of the corridor over the window."""
    import ee

    scenes = (
        ee.ImageCollection(S2)
        .filterBounds(corridor)
        .filterDate(start.isoformat(), end.isoformat())
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", MAX_CLOUD_PCT))
        .map(_masked)
    )
    return scenes.select(list(BANDS)).median().clip(corridor), scenes.size()


def _to_webp(png: bytes, path: Path, quality: int = WEBP_QUALITY) -> None:
    """Re-encode, keeping the transparency outside the corridor."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        image.convert("RGBA").save(path, "WEBP", quality=quality, method=6)


def render(
    *,
    out_dir: Path = DEFAULT_DIR,
    index_path: Path = DEFAULT_INDEX,
    window_days: int = WINDOW_DAYS,
    px: int = FRAME_PX,
    today: date | None = None,
) -> dict[str, object]:
    """One false-colour image per stretch, plus an index for the page."""
    import ee
    import requests

    stretches = fl.load_corridors()
    if not stretches:
        raise ValueError("no corridors defined")

    today = today or date.today()
    end = today - timedelta(days=fl.LAG_DAYS)
    start = end - timedelta(days=window_days)
    out_dir.mkdir(parents=True, exist_ok=True)

    tiles: list[Tile] = []
    for stretch in stretches:
        corridor = fl.corridor_geometry(stretch)
        image, count = composite(corridor, start, end)
        scenes = int(count.getInfo())
        if not scenes:
            log.warning("%s: no usable scenes, skipped", stretch.key)
            continue

        region = corridor.bounds()
        coords = region.getInfo()["coordinates"][0]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]

        url = image.visualize(
            bands=list(BANDS), min=list(VIS_MIN), max=list(VIS_MAX)
        ).getThumbURL(
            {"region": region, "dimensions": px, "format": "png",
             "crs": "EPSG:3857"}
        )
        response = requests.get(url, timeout=600)
        response.raise_for_status()
        path = out_dir / f"{stretch.key}.webp"
        _to_webp(response.content, path)
        tiles.append(Tile(key=stretch.key, path=path, bounds=bounds, scenes=scenes))
        log.info(
            "%s: %d scenes, %d KB (from %d KB of PNG)",
            stretch.key, scenes, path.stat().st_size // 1024,
            len(response.content) // 1024,
        )

    payload = {
        "schema": 1,
        "note": (
            "Sentinel-2 surface reflectance, cloud-masked and composited "
            "over the window. Red is SWIR1, green is the near infrared, "
            "blue is visible blue: vegetation reads bright green, water "
            "deep navy, bare ground pink, towns grey."
        ),
        "source": S2,
        "bands": {"red": BANDS[0], "green": BANDS[1], "blue": BANDS[2]},
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "built_on": today.isoformat(),
        "tiles": [
            {
                "key": t.key,
                "image": f"media/basemap/{t.path.name}",
                "bounds": t.bounds,
                "scenes": t.scenes,
            }
            for t in tiles
        ],
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    log.info("%d stretches -> %s", len(tiles), index_path)
    return payload


def load(path: Path | str = DEFAULT_INDEX) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
