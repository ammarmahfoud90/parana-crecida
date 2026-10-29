"""The flood as a loop: one frame per satellite pass.

Twenty radar observations of the same 212 km of river, played in order.
Nothing here is modelled or interpolated - each frame is a Sentinel-1 pass
that actually happened, on the date printed on it.

Two choices make it readable rather than merely true:

**The backdrop is static.** It is the median backscatter over the whole
period, not each pass's own image. Radar speckle and soil moisture change
between passes, and a live backdrop flickers so badly that the water is
hard to follow. Holding the land still means the only thing moving is the
thing being measured.

**Water is split in two.** The channel the river always occupies is drawn
in blue, taken from JRC's permanent water; everything beyond it is orange.
Without that split a flood just looks like a slightly fatter blue line. With
it, the orange is the flood and nothing else.

The gaps are real. The satellite covers this stretch every twelve days, so
the loop skips forward twelve days at a time and a crest between passes is
simply not seen. Frames are labelled with their date for that reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import flooded as fl

log = logging.getLogger(__name__)

DEFAULT_OUT = Path("site/media/flood.gif")

#: Longest edge of each frame, in pixels. The corridor is about 2.6:1
#: portrait, which suits a phone.
FRAME_PX = 1000
#: Milliseconds per frame, and a longer hold on the last one so the loop
#: does not snap away from the flood the moment it arrives.
FRAME_MS = 420
LAST_FRAME_MS = 2200

#: Backscatter range for the land backdrop, in dB. Narrower than the full
#: range so the land reads as texture rather than glare.
LAND_MIN_DB = -20.0
LAND_MAX_DB = -6.0
#: Dim the land so the water colours carry, with a floor so the darkest
#: ground does not go to pure black and read as a hole.
LAND_DIM = 0.62
LAND_FLOOR = 0.11

COLOUR_CHANNEL = (0.17, 0.42, 0.69)   # the river as it always is
COLOUR_FLOOD = (0.91, 0.35, 0.05)     # water beyond it


@dataclass(frozen=True, slots=True)
class Frame:
    """One pass, rendered."""

    day: date
    path: Path
    km2: float | None


def _thumb_url(image, region, px: int) -> str:
    return image.visualize(min=0, max=1).getThumbURL(
        {"region": region, "dimensions": px, "format": "png"}
    )


def render_frames(
    stretch: fl.Stretch,
    days: list[date],
    areas: dict[date, float],
    out_dir: Path,
    *,
    px: int = FRAME_PX,
    window: tuple[date, date] | None = None,
) -> list[Frame]:
    """Download one PNG per pass. Earth Engine must already be initialised."""
    import ee
    import requests

    corridor = fl.corridor_geometry(stretch)
    region = corridor.bounds()
    permanent = ee.Image(fl.GSW).select("occurrence").gt(90).unmask(0)

    # One backdrop for every frame: the median of the period, so the land
    # holds still and only the water moves.
    first, last = window or (days[0], days[-1])
    base_collection = fl._collection(corridor, first, last)
    base = base_collection.median().clip(corridor)
    # A floor on the land keeps the darkest ground off pure black, so the
    # channel reads as water rather than as a hole in the image.
    land = (
        base.unitScale(LAND_MIN_DB, LAND_MAX_DB)
        .clamp(0, 1)
        .multiply(LAND_DIM)
        .max(LAND_FLOOR)
    )
    backdrop = ee.Image.rgb(land, land, land)

    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Frame] = []
    for day in days:
        mosaic = fl._collection(corridor, day, day).mosaic().clip(corridor)
        water = fl.water_mask(mosaic)
        # Blue is JRC's permanent channel, drawn every frame because it is a
        # constant, not a per-pass measurement. Orange is what this pass
        # actually detected beyond it -- the flood, and only the flood.
        image = backdrop.where(permanent, ee.Image.rgb(*COLOUR_CHANNEL))
        image = image.where(
            water.And(permanent.Not()), ee.Image.rgb(*COLOUR_FLOOD)
        )
        path = out_dir / f"frame_{day.isoformat()}.png"
        response = requests.get(_thumb_url(image, region, px), timeout=240)
        response.raise_for_status()
        path.write_bytes(response.content)
        frames.append(Frame(day=day, path=path, km2=areas.get(day)))
        log.info("rendered %s", day)
    return frames


def _font(size: int):
    from PIL import ImageFont

    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def label_frames(
    frames: list[Frame],
    *,
    baseline_km2: float | None,
    title: str,
    locale_month: tuple[str, ...] = (
        "ene", "feb", "mar", "abr", "may", "jun",
        "jul", "ago", "sep", "oct", "nov", "dic",
    ),
) -> list["object"]:
    """Burn the date, the area and a short legend onto each frame.

    The date has to be on the image itself: the loop skips twelve days at a
    time and without it a viewer reads a smooth progression.
    """
    from PIL import Image, ImageDraw

    out = []
    for frame in frames:
        base = Image.open(frame.path).convert("RGB")
        width, height = base.size

        # Lay the panel out in rows rather than columns. The corridor is a
        # narrow portrait strip, so anything placed to the right of the date
        # lands on top of it.
        scale = max(width / 420.0, 0.62)
        big = _font(int(34 * scale))
        mid = _font(int(21 * scale))
        small = _font(int(15 * scale))
        pad = int(16 * scale)
        panel = int(132 * scale)

        canvas = Image.new("RGB", (width, height + panel), (26, 26, 24))
        canvas.paste(base, (0, 0))
        draw = ImageDraw.Draw(canvas)

        y = height + pad
        draw.text((pad, y), title, font=small, fill=(150, 148, 143))
        y += int(21 * scale)
        label = f"{frame.day.day} {locale_month[frame.day.month - 1]} {frame.day.year}"
        draw.text((pad, y), label, font=big, fill=(240, 238, 233))
        y += int(40 * scale)

        if frame.km2 is not None:
            area = f"{frame.km2:,.0f} km2".replace(",", ".")
            draw.text((pad, y), area, font=mid, fill=(232, 89, 12))
            if baseline_km2:
                draw.text(
                    (pad + draw.textlength(area, font=mid) + int(12 * scale), y + int(4 * scale)),
                    f"x{frame.km2 / baseline_km2:.1f} vs bajante 2021",
                    font=small,
                    fill=(150, 148, 143),
                )
            y += int(27 * scale)

        swatch = int(12 * scale)
        x = pad
        for colour, text in (
            (tuple(int(c * 255) for c in COLOUR_CHANNEL), "cauce permanente"),
            (tuple(int(c * 255) for c in COLOUR_FLOOD), "agua de la crecida"),
        ):
            draw.rectangle([x, y + int(2 * scale), x + swatch, y + swatch + int(2 * scale)],
                           fill=colour)
            x += swatch + int(6 * scale)
            draw.text((x, y), text, font=small, fill=(180, 178, 173))
            x += int(draw.textlength(text, font=small)) + int(18 * scale)

        out.append(canvas)
    return out


def write_gif(
    images: list["object"],
    out: Path,
    *,
    frame_ms: int = FRAME_MS,
    last_frame_ms: int = LAST_FRAME_MS,
) -> Path:
    """Assemble the loop. Palette-quantised so it stays small enough to serve."""
    if not images:
        raise ValueError("no frames to write")
    out.parent.mkdir(parents=True, exist_ok=True)
    durations = [frame_ms] * len(images)
    durations[-1] = last_frame_ms

    quantised = [
        image.quantize(colors=128, method=2, dither=0) for image in images
    ]
    quantised[0].save(
        out,
        save_all=True,
        append_images=quantised[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=2,
    )
    return out


def build(
    project: str | None = None,
    *,
    out: Path = DEFAULT_OUT,
    frames_dir: Path | None = None,
    px: int = FRAME_PX,
    title: str = "Parana, Corrientes - Goya",
) -> dict[str, object]:
    """Render the loop from the committed flooded-area series."""
    table = fl.load_table()
    if not table.get("stretches"):
        raise ValueError("no flooded-area series; run `crecida flooded` first")
    stretch_data = table["stretches"][0]
    corridors = {c.key: c for c in fl.load_corridors()}
    stretch = corridors[stretch_data["key"]]

    days = [date.fromisoformat(d) for d in stretch_data["days"]]
    areas = {date.fromisoformat(d): v for d, v in
             zip(stretch_data["days"], stretch_data["km2"])}
    if not days:
        raise ValueError("the flooded-area series has no passes")

    fl.initialize(project)
    frames = render_frames(
        stretch, days, areas,
        frames_dir or Path(".reports/frames"),
        px=px,
        window=(days[0] - timedelta(days=1), days[-1] + timedelta(days=1)),
    )
    images = label_frames(
        frames, baseline_km2=stretch_data.get("baseline_km2"), title=title
    )
    write_gif(images, out)

    return {
        "path": str(out),
        "frames": len(images),
        "first": days[0].isoformat(),
        "last": days[-1].isoformat(),
        "bytes": out.stat().st_size,
        "peak_km2": max(v for v in areas.values() if v is not None),
    }
