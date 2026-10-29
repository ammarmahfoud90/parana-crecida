"""How much land is under water, from Sentinel-1 radar.

A gauge says how high the river is at one point. It does not say how wide.
Between Corrientes and Goya the Parana spreads across a floodplain tens of
kilometres across, and the difference between a metre of stage and the area
it covers is the whole question for anyone living on it.

Radar is used rather than optical imagery because the Litoral is cloudy
exactly when it floods. Sentinel-1 sees through cloud, and in VV
polarisation calm open water is dark: the surface reflects the pulse away
from the satellite instead of scattering it back.

## Why the threshold is fixed, not Otsu

The usual recipe is Otsu's method, which finds the split between two modes
of a histogram. It does not work here, and it fails in a way that looks
like success. Measured over this corridor:

* over water JRC maps as permanent, VV has a median of about -15.9 dB
* over land it is about -11.2 dB

Four decibels apart, with overlapping tails, and water is only about 5% of
the corridor's area. The histogram has no second mode to find, so Otsu
splits *land* into two classes and returns a threshold near -12 dB. That
labelled 4,445 km2 as water during the worst drought in 77 years.

So the threshold is fixed at -16 dB, chosen because it reproduces an
independent measurement: over the 2021 bajante it gives 536 km2 against
JRC Global Surface Water's 521 km2 of permanent water in the same corridor,
a 3% difference. The test suite asserts that agreement, so if the method
drifts the tests say so.

## What this undercounts

Radar misses water under vegetation. A flooded forest scatters the pulse
back brightly from trunks and canopy and reads as land, so in the wooded
islands and wetlands the figure here is too low. It is a floor, not a
measurement, and the page says so.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_CORRIDORS = Path("data/corridors.json")
DEFAULT_TABLE = Path("data/flooded.json")

S1 = "COPERNICUS/S1_GRD"
GSW = "JRC/GSW1_4/GlobalSurfaceWater"

#: Half-width of the corridor. Wide enough to hold the active floodplain
#: between Corrientes and Goya without reaching into neighbouring basins.
BUFFER_M = 20_000
#: Fallback only. Each stretch carries its own calibrated value in
#: data/corridors.json; see the module docstring for why.
WATER_THRESHOLD_DB = -16.0
#: Speckle is the grain in every radar image; a median over this radius
#: removes it without moving a shoreline more than a pixel or two.
SPECKLE_RADIUS_M = 50
#: Area reductions. S1 is 10 m; 60 m is ample for a figure in km2 and far
#: cheaper over a corridor this size.
AREA_SCALE_M = 60

#: The 2021 bajante, the lowest Parana in 77 years: the floor to compare to.
BASELINE_START = date(2021, 8, 1)
BASELINE_END = date(2021, 9, 15)

WINDOW_DAYS = 180
#: Sentinel-1 products land a day or two after acquisition.
LAG_DAYS = 2


@dataclass(frozen=True, slots=True)
class Stretch:
    """One reach of river, between two gauges."""

    key: str
    from_id: str
    to_id: str
    from_name: str
    to_name: str
    river_km: float
    centreline: list[list[float]]
    #: Calibrated for this stretch. It does not transfer between them:
    #: channel width, island cover and surface roughness all differ.
    threshold_db: float = WATER_THRESHOLD_DB
    calibration: dict | None = None


def load_corridors(path: Path | str = DEFAULT_CORRIDORS) -> list[Stretch]:
    """The frozen centrelines, walked from the river topology."""
    path = Path(path)
    if not path.exists():
        log.warning("no corridors at %s", path)
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        Stretch(
            key=s["key"],
            from_id=str(s["from"]),
            to_id=str(s["to"]),
            from_name=s["from_name"],
            to_name=s["to_name"],
            river_km=float(s["river_km"]),
            centreline=s["centreline"],
            threshold_db=float(s.get("threshold_db", WATER_THRESHOLD_DB)),
            calibration=s.get("calibration"),
        )
        for s in payload.get("stretches", [])
    ]


def initialize(project: str | None = None, *, key: str | None = None) -> None:
    """Start Earth Engine. Shares the rainfall module's credential handling."""
    from .rainfall import initialize as _initialize

    _initialize(project, key=key)


def corridor_geometry(stretch: Stretch, *, buffer_m: int = BUFFER_M):
    """The corridor: the centreline, buffered."""
    import ee

    return ee.Geometry.LineString(stretch.centreline).buffer(buffer_m)


def water_mask(image, *, threshold_db: float = WATER_THRESHOLD_DB,
               speckle_m: int = SPECKLE_RADIUS_M):
    """Water where despeckled VV backscatter falls below the threshold."""
    return image.select("VV").focal_median(
        speckle_m, "circle", "meters"
    ).lt(threshold_db)


def _collection(corridor, start: date, end: date):
    import ee

    return (
        ee.ImageCollection(S1)
        .filterBounds(corridor)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filterDate(start.isoformat(), (end + timedelta(days=1)).isoformat())
        .select("VV")
    )


def permanent_water_km2(corridor) -> float:
    """JRC's permanent water in the corridor: the independent anchor."""
    import ee

    mask = ee.Image(GSW).select("occurrence").gt(90)
    value = mask.multiply(ee.Image.pixelArea()).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=corridor,
        scale=AREA_SCALE_M,
        maxPixels=int(1e10),
        bestEffort=True,
    ).values().get(0)
    return float(ee.Number(value).divide(1e6).getInfo())


def water_area_km2(corridor, start: date, end: date, **kwargs) -> float | None:
    """Water area over a window, from the mosaic of its acquisitions."""
    import ee

    collection = _collection(corridor, start, end)
    if collection.size().getInfo() == 0:
        return None
    mask = water_mask(collection.mosaic().clip(corridor), **kwargs)
    value = mask.multiply(ee.Image.pixelArea()).reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=corridor,
        scale=AREA_SCALE_M,
        maxPixels=int(1e10),
        bestEffort=True,
    ).get("VV")
    return round(float(ee.Number(value).divide(1e6).getInfo()), 1)


#: Earth Engine refuses a request that fans out too far ("Too many
#: concurrent aggregations"). Each date is a full reduction over a
#: 9,500 km2 corridor, so they go up a handful at a time.
BATCH_DAYS = 6

#: A pass that crosses only part of the corridor leaves the rest with no
#: data, and unobserved ground reduces to zero water -- which reads as the
#: river shrinking. Coverage per date here is either about 99.7% or about
#: 23-40%, with nothing in between, so the cut is unambiguous. Full passes
#: come round every 12 days or so, which is the real sampling rate.
MIN_COVERAGE = 0.95

#: A pass that cannot even find the permanent river did not measure the
#: flood either. It happens when wind roughens the water surface enough to
#: lift its backscatter above the threshold, and it survives the coverage
#: filter because the ground *was* observed -- just not usefully. Anything
#: below this share of JRC's permanent water is dropped rather than
#: published as a river that shrank to a third of its channel.
MIN_PLAUSIBLE_FRACTION = 0.7


def area_series(
    corridor,
    start: date,
    end: date,
    *,
    threshold_db: float = WATER_THRESHOLD_DB,
    batch: int = BATCH_DAYS,
    min_coverage: float = MIN_COVERAGE,
    floor_km2: float | None = None,
) -> list[tuple[date, float, int]]:
    """Water area per acquisition date.

    Acquisitions are grouped by calendar day: the two relative orbits that
    cover this corridor pass on different days, and a day's images are
    mosaicked so a pass that only clips the corner does not read as a
    sudden drop.
    """
    import ee

    collection = _collection(corridor, start, end)
    all_days = sorted(set(
        collection.aggregate_array("system:time_start")
        .map(lambda t: ee.Date(t).format("YYYY-MM-dd"))
        .distinct().getInfo()
    ))

    def one_day(day):
        day = ee.Date(ee.String(day))
        same = collection.filterDate(day, day.advance(1, "day"))
        mosaic = same.mosaic().clip(corridor)
        mask = water_mask(mosaic, threshold_db=threshold_db)
        km2 = mask.multiply(ee.Image.pixelArea()).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=corridor,
            scale=AREA_SCALE_M,
            maxPixels=int(1e10),
            bestEffort=True,
        ).get("VV")
        seen = mosaic.mask().rename("seen").multiply(
            ee.Image.pixelArea()
        ).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=corridor,
            scale=200,
            maxPixels=int(1e10),
            bestEffort=True,
        ).get("seen")
        return ee.Feature(None, {
            "day": day.format("YYYY-MM-dd"),
            "km2": ee.Number(km2).divide(1e6),
            "seen_km2": ee.Number(seen).divide(1e6),
            "images": same.size(),
        })

    corridor_km2 = float(corridor.area(1000).divide(1e6).getInfo())
    out: list[tuple[date, float, int]] = []
    partial = 0
    for index in range(0, len(all_days), batch):
        chunk = ee.List(all_days[index:index + batch])
        features = ee.FeatureCollection(chunk.map(one_day)).getInfo()["features"]
        for feature in features:
            props = feature["properties"]
            if props.get("km2") is None or props.get("seen_km2") is None:
                continue
            coverage = float(props["seen_km2"]) / max(corridor_km2, 1e-9)
            if coverage < min_coverage:
                partial += 1
                continue
            out.append(
                (date.fromisoformat(props["day"]), round(float(props["km2"]), 1),
                 int(props.get("images") or 0))
            )
        log.debug("flooded area: %d/%d days", min(index + batch, len(all_days)), len(all_days))
    implausible = 0
    if floor_km2 is not None:
        kept = [row for row in out if row[1] >= floor_km2]
        implausible = len(out) - len(kept)
        out = kept

    out.sort(key=lambda row: row[0])
    log.info(
        "%d passes kept, %d partial, %d too dark to be believed",
        len(out), partial, implausible,
    )
    return out


def build(
    project: str | None = None,
    *,
    corridors: list[Stretch] | None = None,
    window_days: int = WINDOW_DAYS,
    today: date | None = None,
    lag_days: int = LAG_DAYS,
    threshold_db: float = WATER_THRESHOLD_DB,
) -> dict[str, object]:
    """Flooded area per stretch. Needs an Earth Engine credential."""
    initialize(project)
    today = today or date.today()
    end = today - timedelta(days=lag_days)
    start = end - timedelta(days=window_days)
    stretches = corridors if corridors is not None else load_corridors()
    if not stretches:
        raise ValueError("no corridors defined; nothing to measure")

    out: list[dict[str, object]] = []
    for stretch in stretches:
        corridor = corridor_geometry(stretch)
        thr = stretch.threshold_db
        area = float(corridor.area(1000).divide(1e6).getInfo())
        permanent = permanent_water_km2(corridor)
        baseline = water_area_km2(
            corridor, BASELINE_START, BASELINE_END, threshold_db=thr
        )
        series = area_series(
            corridor,
            start,
            end,
            threshold_db=thr,
            floor_km2=permanent * MIN_PLAUSIBLE_FRACTION,
        )
        log.info(
            "%s: corridor %.0f km2, baseline %.0f km2, %d observations",
            stretch.key, area, baseline or 0, len(series),
        )
        out.append({
            "key": stretch.key,
            "from_name": stretch.from_name,
            "to_name": stretch.to_name,
            "river_km": stretch.river_km,
            "corridor_km2": round(area),
            # The anchor the threshold was calibrated against. Kept in the
            # payload so the calibration can be re-checked, not just trusted.
            "threshold_db": thr,
            "permanent_water_km2": round(permanent, 1),
            "baseline_km2": baseline,
            "baseline_window": {
                "start": BASELINE_START.isoformat(), "end": BASELINE_END.isoformat(),
            },
            "days": [d.isoformat() for d, _, _ in series],
            "km2": [v for _, v, _ in series],
            "images": [n for _, _, n in series],
        })

    return {
        "schema": 1,
        "source": S1,
        "method": {
            "threshold_db": "per stretch; see each stretch's threshold_db",
            "speckle_radius_m": SPECKLE_RADIUS_M,
            "buffer_m": BUFFER_M,
            "scale_m": AREA_SCALE_M,
        },
        "note": (
            "Open water from Sentinel-1 VV backscatter. Radar does not see "
            "water under vegetation, so flooded forest and wetland read as "
            "land and this is a floor, not a total."
        ),
        "fetched_on": today.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "stretches": out,
    }


def write_table(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def load_table(path: Path | str = DEFAULT_TABLE) -> dict[str, object]:
    path = Path(path)
    if not path.exists():
        log.warning("no flooded area at %s", path)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def summarise(payload: dict[str, object]) -> list[dict[str, object]]:
    """Latest observation and extra water per stretch, for the page."""
    out: list[dict[str, object]] = []
    for stretch in payload.get("stretches", []):
        values = stretch.get("km2") or []
        days = stretch.get("days") or []
        latest = {"day": days[-1], "km2": values[-1]} if values else None
        baseline = stretch.get("baseline_km2")
        extra = (
            None
            if latest is None or baseline is None
            else round(latest["km2"] - baseline, 1)
        )
        out.append({
            **{k: stretch[k] for k in (
                "key", "from_name", "to_name", "river_km", "corridor_km2",
                "baseline_km2", "permanent_water_km2", "days", "km2",
            ) if k in stretch},
            "latest": latest,
            "extra_km2": extra,
            "peak": (
                None if not values
                else {"day": days[values.index(max(values))], "km2": max(values)}
            ),
        })
    return out


def stale_days(payload: dict[str, object], today: date | None = None) -> int | None:
    fetched = payload.get("fetched_on")
    if not fetched:
        return None
    return ((today or date.today()) - date.fromisoformat(str(fetched))).days
