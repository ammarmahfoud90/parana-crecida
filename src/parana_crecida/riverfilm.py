"""The whole river, a year, one picture that moves.

Everything else this project publishes is a number or a chart of numbers.
This is the measurement itself, drawn: twelve hundred kilometres of the
Paraná, one frame per Sentinel-1 repeat cycle, from one low-water season
into the flood. It exists because a flood on a river this size is a shape,
and a shape is the one thing a table cannot show.

**Why a whole-river frame works at all.** The run from Puerto Iguazú to
Villa Constitución spans about 950 km north to south and 630 km east to
west, so the bounding box is close to three by two - not the thin ribbon
the river's own width suggests. At the size rendered here a pixel is under
a kilometre, the 40 km corridor is some fifty pixels across, and the flood
reads as that band thickening. Close up it is coarse. It is not meant to
be read close up.

**What is drawn.**

- The backdrop is a Sentinel-2 false-colour composite over the dry months,
  so the normal river is the baseline the flood is seen against. SWIR2,
  near infrared and red: vegetation green, water near black, bare ground
  pink, towns grey.
- The permanent channel, in blue: water the JRC maps as always present,
  or present through the 2021 bajante. It is a constant rather than a
  measurement, and drawing it keeps the river continuous where a given
  cycle's radar was ambiguous. The second half of that definition exists
  because of this film - see floodwater.permanent_water.
- Everything the radar found beyond that channel, in orange. That is the
  measurement, and it is the only thing that moves.

**Only stretches that were scored.** A picture carries more conviction than
a number and deserves at least as much evidence behind it, so a stretch is
drawn only where the mask was validated against optical water and cleared
``export.SKILL_FLOOR``. That rule now admits the gorge above Posadas, which
scores best on the river, and excludes the Delta, which had no clear
optical scene to be scored against.

**Mercator, deliberately.** ``getThumbURL`` renders in EPSG:4326 unless
told otherwise, which is linear in latitude; the frames are composited and
later overlaid in Web Mercator, which is not. Over 8.5 degrees of latitude
the disagreement put water up to 10 km from the river it belonged to. Every
render here asks for EPSG:3857 so that every layer agrees.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date, timedelta
from pathlib import Path

from . import basemap as bm
from . import flooded as fl
from . import floodwater as fw

log = logging.getLogger(__name__)

DEFAULT_GIF = Path("site/media/river.gif")
DEFAULT_FRAME_DIR = Path("site/media/river")
DEFAULT_INDEX = Path("site/data/river_film.json")

#: One Sentinel-1 repeat cycle.
WINDOW_DAYS = 12
#: Cycles in the film. Thirty-one is a little over a year, which is the
#: span that shows a low-water season as well as the flood.
CYCLES = 31
#: Longest edge of the rendered frames, in pixels.
FRAME_PX = 1400
#: Months of Sentinel-2 behind the backdrop composite.
BACKDROP_DAYS = 120
#: Milliseconds per frame, and the pause on the last one so the final
#: state is readable before it loops.
FRAME_MS = 420
LAST_MS = 2200

COLOUR_CHANNEL = (0.10, 0.32, 0.64)
COLOUR_FLOOD = (1.00, 0.38, 0.00)

#: The backdrop is dimmed before the water goes on top of it. Sentinel-2
#: false colour puts vegetation at full green, which at this scale is most
#: of the frame and louder than the thing being measured: a flood four
#: kilometres wide on a corridor forty kilometres wide is a few pixels, and
#: it has to win. The backdrop is context, so it is lit like context.
BACKDROP_DIM = 0.72

#: Palette size for the GIF. The backdrop is a satellite composite and
#: therefore noisy, so this is the main lever on file size.
GIF_COLOURS = 128

#: The individual frames are written beside the GIF, so one date can be
#: pulled out as a still without re-rendering the year. They are
#: gitignored; the GIF is what ships.
KEEP_FRAMES = True


def published_keys(skill_path: Path | None = None) -> set[str]:
    """Stretches whose mask was scored and cleared the floor."""
    from .export import SKILL_FLOOR

    path = skill_path or Path("data/mask_skill.json")
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing; run scripts/validate_mask.py --out {path} "
            "before drawing anything"
        )
    table = json.loads(path.read_text(encoding="utf-8"))
    return {
        row["key"] for row in table.get("stretches", [])
        if (row.get("now") or {}).get("iou") is not None
        and row["now"]["iou"] >= SKILL_FLOOR
    }


def union_bounds(stretches: list[fl.Stretch]):
    """``(region, [[south, west], [north, east]])`` over every corridor."""
    import ee

    union = ee.Geometry.MultiPolygon(
        [fl.corridor_geometry(s) for s in stretches]
    ).dissolve(maxError=1000)
    region = union.bounds()
    coords = region.getInfo()["coordinates"][0]
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return region, [[min(lats), min(lons)], [max(lats), max(lons)]]


def _fetch(url: str) -> bytes:
    import requests

    response = requests.get(url, timeout=600)
    response.raise_for_status()
    return response.content


def _mercator_y(lat: float) -> float:
    """Web Mercator northing, in the same units as longitude."""
    import math

    lat = max(min(lat, 85.05112878), -85.05112878)
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def backdrop(bounds, *, px: int = FRAME_PX, tiles_path: Path | None = None):
    """The false-colour backdrop, stitched from the per-stretch tiles.

    Asking Earth Engine for one Sentinel-2 composite over the whole run
    returns a 400: the request covers roughly 950 by 630 kilometres and the
    median is computed at the source resolution before anything is scaled
    down, which is more pixels than a thumbnail will serve.

    The per-stretch tiles are already rendered, already false-colour and
    already in EPSG:3857, so the picture exists; it only has to be laid
    out. Each tile is placed by its own bounds converted to Mercator,
    which is exactly the projection it was rendered in, so the seams line
    up rather than merely looking as though they do.
    """
    from PIL import Image

    index = bm.load(tiles_path or bm.DEFAULT_INDEX)
    tiles = index.get("tiles") or []
    if not tiles:
        raise FileNotFoundError(
            "no basemap tiles; run the basemap render before the film")

    (south, west), (north, east) = bounds
    y0, y1 = _mercator_y(south), _mercator_y(north)
    span_x, span_y = east - west, y1 - y0
    if span_x >= span_y:
        width = px
        height = max(1, round(px * span_y / span_x))
    else:
        height = px
        width = max(1, round(px * span_x / span_y))

    canvas = Image.new("RGBA", (width, height), (8, 10, 12, 255))
    root = (tiles_path or bm.DEFAULT_INDEX).parent.parent
    placed = 0
    for tile in tiles:
        path = root / tile["image"]
        if not path.exists():
            log.warning("missing basemap tile %s", path)
            continue
        (ts, tw), (tn, te) = tile["bounds"]
        left = round((tw - west) / span_x * width)
        right = round((te - west) / span_x * width)
        # Screen y runs the other way from northing.
        top = round((y1 - _mercator_y(tn)) / span_y * height)
        bottom = round((y1 - _mercator_y(ts)) / span_y * height)
        if right <= left or bottom <= top:
            continue
        piece = Image.open(path).convert("RGBA").resize(
            (right - left, bottom - top), Image.LANCZOS)
        canvas.paste(piece, (left, top), piece)
        placed += 1
    log.info("backdrop %dx%d from %d tiles", width, height, placed)
    if BACKDROP_DIM != 1.0:
        from PIL import ImageEnhance

        dimmed = ImageEnhance.Brightness(canvas.convert("RGB")).enhance(BACKDROP_DIM)
        dimmed = dimmed.convert("RGBA")
        dimmed.putalpha(canvas.getchannel("A"))
        canvas = dimmed
    return canvas


def water_layer(stretches: list[fl.Stretch], start: date, end: date):
    """Water over the whole run, painted, with land left transparent."""
    import ee

    permanent = fw.permanent_water()
    masks, corridors = [], []
    for stretch in stretches:
        corridor = fl.corridor_geometry(stretch)
        corridors.append(corridor)
        masks.append(fw.water_mask(corridor, start, end).unmask(0).clip(corridor))

    detected = ee.ImageCollection(masks).mosaic().unmask(0)
    inside = ee.Image.constant(1).clip(
        ee.Geometry.MultiPolygon(corridors).dissolve(maxError=1000)
    ).mask()
    visible = detected.Or(permanent).And(inside)
    painted = ee.Image.rgb(*COLOUR_FLOOD).where(permanent, ee.Image.rgb(*COLOUR_CHANNEL))
    return painted.updateMask(visible)


def _font(size: int):
    from PIL import ImageFont

    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _label(frame, day: date, km2: float | None, *, locale_month: tuple[str, ...]):
    """Burn the date, and the area if there is one, into the frame.

    The caption is part of the image on purpose. This file will be posted,
    screenshotted and re-posted away from the page that explains it, and a
    frame with no date on it is a picture of nothing in particular.
    """
    from PIL import ImageDraw

    draw = ImageDraw.Draw(frame, "RGBA")
    width, height = frame.size
    band = max(52, height // 18)
    draw.rectangle([0, height - band, width, height], fill=(0, 0, 0, 190))

    big = _font(int(band * 0.42))
    small = _font(int(band * 0.27))
    text = f"{day.day} {locale_month[day.month - 1]} {day.year}"
    draw.text((16, height - band + band * 0.14), text, font=big, fill=(255, 255, 255))
    if km2 is not None:
        right = f"{km2:,.0f} km²".replace(",", ".")
        draw.text((16, height - band + band * 0.60), right, font=small,
                  fill=(250, 180, 120))
    return frame


MONTHS_ES = ("ene", "feb", "mar", "abr", "may", "jun",
             "jul", "ago", "sep", "oct", "nov", "dic")


def render(
    *,
    gif_path: Path = DEFAULT_GIF,
    frame_dir: Path = DEFAULT_FRAME_DIR,
    index_path: Path = DEFAULT_INDEX,
    cycles: int = CYCLES,
    window_days: int = WINDOW_DAYS,
    px: int = FRAME_PX,
    today: date | None = None,
    skill_path: Path | None = None,
    keep_frames: bool = KEEP_FRAMES,
) -> dict:
    """Render the film, write the GIF, and return what was drawn."""
    from PIL import Image

    keys = published_keys(skill_path)
    stretches = [s for s in fl.load_corridors() if s.key in keys]
    if not stretches:
        raise ValueError("no stretch cleared the skill floor; nothing to draw")
    log.info("drawing %d stretches: %s", len(stretches),
             ", ".join(s.key for s in stretches))

    today = today or date.today()
    last = today - timedelta(days=fl.LAG_DAYS)
    region, bounds = union_bounds(stretches)

    base = backdrop(bounds, px=px)

    frame_dir.mkdir(parents=True, exist_ok=True)
    frames, rows = [], []
    for index in range(cycles - 1, -1, -1):
        end = last - timedelta(days=window_days * index)
        start = end - timedelta(days=window_days - 1)
        try:
            url = water_layer(stretches, start, end).visualize(min=0, max=1).getThumbURL(
                {"region": region,
                 "dimensions": f"{base.size[0]}x{base.size[1]}",
                 "format": "png", "crs": "EPSG:3857"}
            )
            layer = Image.open(io.BytesIO(_fetch(url))).convert("RGBA")
        except Exception as error:  # pragma: no cover - Earth Engine
            log.warning("%s: %s", end, error)
            continue

        if layer.size != base.size:
            layer = layer.resize(base.size, Image.NEAREST)
        frame = Image.alpha_composite(base, layer)

        total = 0.0
        for stretch in stretches:
            corridor = fl.corridor_geometry(stretch)
            try:
                total += fw.area_km2(corridor, fw.water_mask(corridor, start, end))
            except Exception as error:  # pragma: no cover - Earth Engine
                log.warning("  area %s %s: %s", stretch.key, end, error)
                total = None
                break

        _label(frame, end, total, locale_month=MONTHS_ES)
        if keep_frames:
            frame.convert("RGB").save(frame_dir / f"river_{end.isoformat()}.png")
        frames.append(frame.convert("RGB"))
        rows.append({"start": start.isoformat(), "end": end.isoformat(),
                     "water_km2": round(total, 1) if total is not None else None})
        log.info("frame %s  %s km2", end, "–" if total is None else f"{total:.0f}")

    if not frames:
        raise RuntimeError("every frame failed; nothing was written")

    palette = [f.quantize(colors=GIF_COLOURS, method=Image.MEDIANCUT) for f in frames]
    durations = [FRAME_MS] * len(palette)
    durations[-1] = LAST_MS
    gif_path.parent.mkdir(parents=True, exist_ok=True)
    palette[0].save(
        gif_path, save_all=True, append_images=palette[1:],
        duration=durations, loop=0, optimize=True, disposal=2,
    )
    log.info("wrote %s (%.1f MB, %d frames)",
             gif_path, gif_path.stat().st_size / 1e6, len(palette))

    payload = {
        "schema": 1,
        "note": ("One frame per 12-day Sentinel-1 repeat cycle. The blue is "
                 "the permanent channel from JRC, a constant rather than a "
                 "measurement; the orange is the water the radar found "
                 "beyond it. Only stretches whose mask was validated "
                 "against optical water are drawn."),
        "image": f"media/{gif_path.name}",
        "bounds": bounds,
        "window_days": window_days,
        "px": px,
        "generated_for": today.isoformat(),
        "stretches": [{"key": s.key, "from_name": s.from_name,
                       "to_name": s.to_name, "river_km": s.river_km}
                      for s in stretches],
        "river_km": round(sum(s.river_km for s in stretches), 1),
        "frames": rows,
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return payload


def load(path: Path | str = DEFAULT_INDEX) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
