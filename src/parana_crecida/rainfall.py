"""Rain over the basins that feed the upper Parana.

The gauges say what is already in the river. This says what fell on the
ground that will be in it shortly, which is the one upstream signal that
leads the dam releases rather than following them.

Two basins, and they are the two arms that meet at Puerto Iguazu:

``alto_parana``
    The Parana above the Iguazu confluence, 834,000 km2 across Brazil and
    Paraguay. What Itaipu eventually passes through.
``iguazu``
    The Iguazu, 68,000 km2. Small by comparison and much flashier - the
    88 mm basin-average day on 24 October 2026 arrived at Andresito within
    a couple of days.

Both are built by walking HydroSHEDS' basin topology upstream from a seed
sub-basin, not hand-drawn. The walk is checked against HydroSHEDS' own
published upstream area: it reproduces 834,184 km2 and 67,537 km2 to within
a rounding error, and the test suite asserts that. They are disjoint
siblings draining to the same junction, so adding them together is sound.

Rainfall is a basin *average*. A number like "12 mm" means 12 mm spread
over an area the size of France, which is a very different thing from 12 mm
falling on one town, and the page says so.

**This needs a Google Earth Engine credential**, which a GitHub Actions run
does not have. So the series is derived locally and committed, like the
chain and the travel times. The page shows the date it was last refreshed,
because unlike those two this one does go stale.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_TABLE = Path("data/rainfall.json")

#: Half-hourly global precipitation, mm/hr, about 11 km.
IMERG = "NASA/GPM_L3/IMERG_V07"
IMERG_BAND = "precipitation"
#: IMERG's native resolution. Reducing at a finer scale buys nothing but time.
SCALE_M = 11132

HYBAS_LEVEL = 6
HYBAS = f"WWF/HydroSHEDS/v1/Basins/hybas_{HYBAS_LEVEL}"
#: The La Plata system, which both basins belong to.
MAIN_BASIN = 6060016970


@dataclass(frozen=True, slots=True)
class Basin:
    """One contributing basin, defined by the sub-basin at its outlet."""

    key: str
    seed: int
    #: HydroSHEDS' own upstream area, for checking the walk against.
    published_km2: float


BASINS = (
    Basin("alto_parana", 6060793610, 834_184.0),
    Basin("iguazu", 6060793620, 67_537.0),
)

#: IMERG's near-real-time product lags reality by about half a day; asking
#: for today would return a part-day total that reads as a dry spell.
LAG_DAYS = 2
WINDOW_DAYS = 180


def initialize(project: str) -> None:
    """Start Earth Engine. Raises if the credential or project is missing."""
    import ee

    ee.Initialize(project=project)


def upstream_ids(seed: int, *, main_basin: int = MAIN_BASIN) -> set[int]:
    """Every sub-basin draining through ``seed``, including it.

    The topology table is small (a few hundred rows for La Plata), so it
    comes back to Python and the walk happens here. Doing it server-side
    would need an iterate loop for no gain.
    """
    import ee

    collection = ee.FeatureCollection(HYBAS).filter(
        ee.Filter.eq("MAIN_BAS", main_basin)
    )
    rows = collection.select(
        ["HYBAS_ID", "NEXT_DOWN", "SUB_AREA"], None, False
    ).getInfo()["features"]

    children: dict[int, list[int]] = {}
    for row in rows:
        props = row["properties"]
        children.setdefault(int(props["NEXT_DOWN"]), []).append(int(props["HYBAS_ID"]))

    seen = {seed}
    stack = [seed]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def basin_area_km2(ids: set[int], *, main_basin: int = MAIN_BASIN) -> float:
    """Summed sub-basin area, for checking a walk against the published one."""
    import ee

    rows = (
        ee.FeatureCollection(HYBAS)
        .filter(ee.Filter.eq("MAIN_BAS", main_basin))
        .filter(ee.Filter.inList("HYBAS_ID", list(ids)))
        .select(["SUB_AREA"], None, False)
        .getInfo()["features"]
    )
    return sum(r["properties"]["SUB_AREA"] for r in rows)


def basin_geometry(ids: set[int]):
    """The dissolved outline of a set of sub-basins."""
    import ee

    return (
        ee.FeatureCollection(HYBAS)
        .filter(ee.Filter.inList("HYBAS_ID", list(ids)))
        .geometry()
        .dissolve(maxError=1000)
    )


def daily_rainfall(geometry, start: date, end: date) -> list[tuple[date, float | None]]:
    """Basin-average rainfall per day, in mm.

    IMERG is half-hourly in mm/hr, so a day's total is the mean rate over
    that day times 24. Done server-side across the whole range and returned
    in one call.
    """
    import ee

    collection = ee.ImageCollection(IMERG).select(IMERG_BAND)
    span = (end - start).days
    if span < 0:
        return []

    start_ee = ee.Date(start.isoformat())

    def one_day(offset):
        day = start_ee.advance(ee.Number(offset), "day")
        total = (
            collection.filterDate(day, day.advance(1, "day")).mean().multiply(24)
        )
        value = total.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=geometry,
            scale=SCALE_M,
            maxPixels=int(1e9),
            bestEffort=True,
        ).get(IMERG_BAND)
        return ee.Feature(None, {"day": day.format("YYYY-MM-dd"), "mm": value})

    features = ee.FeatureCollection(
        ee.List.sequence(0, span).map(one_day)
    ).getInfo()["features"]

    out: list[tuple[date, float | None]] = []
    for feature in features:
        props = feature["properties"]
        millimetres = props.get("mm")
        out.append(
            (
                date.fromisoformat(props["day"]),
                None if millimetres is None else round(float(millimetres), 2),
            )
        )
    return out


def build(
    project: str,
    *,
    window_days: int = WINDOW_DAYS,
    today: date | None = None,
    lag_days: int = LAG_DAYS,
) -> dict[str, object]:
    """Fetch rainfall for every basin. Needs an Earth Engine credential."""
    initialize(project)
    today = today or date.today()
    end = today - timedelta(days=lag_days)
    start = end - timedelta(days=window_days)

    basins: list[dict[str, object]] = []
    for basin in BASINS:
        ids = upstream_ids(basin.seed)
        walked = basin_area_km2(ids)
        drift = abs(walked - basin.published_km2) / basin.published_km2
        if drift > 0.01:
            raise ValueError(
                f"{basin.key}: walked {walked:,.0f} km2 against a published "
                f"{basin.published_km2:,.0f} km2 - the topology walk is wrong"
            )
        series = daily_rainfall(basin_geometry(ids), start, end)
        log.info("%s: %d sub-basins, %d days", basin.key, len(ids), len(series))
        basins.append(
            {
                "key": basin.key,
                "sub_basins": len(ids),
                "area_km2": round(walked),
                "days": [d.isoformat() for d, _ in series],
                "mm": [v for _, v in series],
            }
        )

    return {
        "schema": 1,
        "source": IMERG,
        "note": (
            "Basin-average daily rainfall in mm. An average over an area this "
            "large is not the same as rain falling on one town."
        ),
        "fetched_on": today.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "lag_days": lag_days,
        "basins": basins,
    }


def write_table(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def load_table(path: Path | str = DEFAULT_TABLE) -> dict[str, object]:
    """The frozen rainfall series, or an empty dict if never fetched."""
    path = Path(path)
    if not path.exists():
        log.warning("no rainfall at %s; the drivers view will show none", path)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def summarise(payload: dict[str, object], *, days: int = 7) -> list[dict[str, object]]:
    """Recent totals per basin, for the page's headline numbers."""
    out: list[dict[str, object]] = []
    for basin in payload.get("basins", []):
        values = [v for v in basin["mm"][-days:] if v is not None]
        all_values = [v for v in basin["mm"] if v is not None]
        out.append(
            {
                "key": basin["key"],
                "area_km2": basin["area_km2"],
                "recent_days": days,
                "recent_mm": round(sum(values), 1) if values else None,
                "wettest_day": (
                    None
                    if not all_values
                    else {
                        "day": basin["days"][basin["mm"].index(max(all_values))],
                        "mm": max(all_values),
                    }
                ),
                "days": basin["days"],
                "mm": basin["mm"],
            }
        )
    return out


def stale_days(payload: dict[str, object], today: date | None = None) -> int | None:
    """How old the frozen series is. It does go stale; the chain does not."""
    fetched = payload.get("fetched_on")
    if not fetched:
        return None
    return ((today or date.today()) - date.fromisoformat(str(fetched))).days
