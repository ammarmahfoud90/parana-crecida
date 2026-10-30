"""How many people the water has reached.

**This does not publish, and here is the measurement that stopped it.**

Tried three times, on three different water masks, and failed the same way
each time. On the validated VH mask, over 31 repeat cycles from November
2025 to October 2026:

    2025-11-02       755          2026-06-06    48,959
    2026-01-13       460          2026-07-12     3,452
    2026-03-26     1,270          2026-09-22     3,635
    2026-05-01     2,132          2026-10-28     2,971

Two things are wrong with that column, and either alone is fatal.

The 6 June spike is a city. It is 23,482 people in the Libertador-Posadas
stretch and 23,433 in the one below it - the two reaches that meet at
Posadas and Encarnacion. On that one pass the urban surface fell below the
water threshold and a city of some half a million read as a lake. The old
VV mask did this too, at Posadas, at 97,498 people. A different band, the
same city, the same failure.

And the series does not follow the flood. Flood area roughly tripled
between June and October, from 2,217 to 5,918 km2; the people figure ends
*lower* in October than it was in July. What is being measured varies with
the incidence angle and the wetness of built surfaces, not with the river.

So the number is not published, and the reason is worth more than the
number would have been: it is the clearest demonstration on this project
that a plausible figure can be entirely spurious, and that radar water
detection over inhabited ground needs an explicit settlement mask before
it can say anything about people at all. The series stays in the store and
the code stays here, because the next attempt should start from the
measurement that defeated this one.


Everything else here measures a river. This is the only thing that
measures a flood, because a flood is a river that has arrived somewhere
people are. "2,401 km2 of water near Posadas" is a fact about hydrology;
"the water covers ground where N thousand people live" is the same
measurement turned into the thing anyone actually wanted to know.

**Flood water, not river.** The population counted is under water that the
radar found *and* that the JRC does not map as permanent. Counting the
channel would add the people who live beside a river that is exactly where
it always is, which is not news and would swamp the signal.

**Three things this is not, stated plainly because the number invites
being quoted without them.**

It is not a count of people flooded. It is the modelled residential
population of ground now under water. Someone on a raised bank inside a
flooded hectare is in this number and dry; someone whose road is cut but
whose house is dry is not.

It is a floor, like every radar figure here. Water under trees is invisible
to the sensor, so hamlets in flooded woodland count as untouched.

And the population is modelled, not counted. GHSL distributes census
totals over a built-surface grid, so it is good over towns and weak over
scattered rural settlement - which is most of this floodplain.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from . import flooded as fl

log = logging.getLogger(__name__)

DEFAULT_TABLE = Path("data/exposure.json")

#: Modelled residential population, 100 m, from the Global Human
#: Settlement Layer. The most recent epoch that is a published raster
#: rather than a projection.
POPULATION = "JRC/GHSL/P2023A/GHS_POP/2020"
#: Built surface over the same grid, used to say how much of the flooded
#: ground is built on at all.
BUILT = "JRC/GHSL/P2023A/GHS_BUILT_S/2020"

#: Permanent water is excluded above this occurrence, the same threshold
#: the area measurement uses for its baseline.
PERMANENT_OCCURRENCE = 90

SCALE_M = 100


def flood_only_mask(corridor, start: date, end: date, stretch):
    """Water the radar found that is not the river's own channel."""
    import ee

    mosaic = fl._collection(corridor, start, end).mosaic().clip(corridor)
    water = fl.water_mask(
        mosaic, threshold_db=stretch.threshold_db, slope_mask=stretch.slope_mask
    )
    permanent = ee.Image(fl.GSW).select("occurrence").gt(PERMANENT_OCCURRENCE).unmask(0)
    return water.And(permanent.Not())


def under_water(corridor, mask) -> dict[str, float]:
    """Population and built surface under a mask."""
    import ee

    # Both products carry more than one band - GHS_BUILT_S splits
    # residential from non-residential - so the band is named rather than
    # assumed.
    people = ee.Image(POPULATION).select("population_count").updateMask(mask)
    built = ee.Image(BUILT).select("built_surface").updateMask(mask)
    stats = (
        people.rename("people")
        .addBands(built.rename("built_m2"))
        .reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=corridor,
            scale=SCALE_M,
            maxPixels=1e10,
            bestEffort=True,
        )
        .getInfo()
    )
    return {
        "people": round(float(stats.get("people") or 0)),
        "built_km2": round(float(stats.get("built_m2") or 0) / 1e6, 1),
    }


def build(
    *,
    window_days: int = 12,
    cycles: int = 15,
    today: date | None = None,
) -> dict[str, object]:
    """Population under flood water, per stretch, per composite."""
    stretches = [s for s in fl.load_corridors() if s.calibrated]
    if not stretches:
        raise ValueError("no calibrated corridors")

    today = today or date.today()
    last = today - timedelta(days=fl.LAG_DAYS)

    rows: list[dict] = []
    totals: dict[str, dict] = {}
    for stretch in stretches:
        corridor = fl.corridor_geometry(stretch)
        for index in range(cycles - 1, -1, -1):
            end = last - timedelta(days=window_days * index)
            start = end - timedelta(days=window_days - 1)
            try:
                counts = under_water(
                    corridor, flood_only_mask(corridor, start, end, stretch)
                )
            except Exception as error:  # pragma: no cover - Earth Engine
                log.warning("%s %s: %s", stretch.key, end, error)
                continue
            rows.append({
                "key": stretch.key,
                "from_name": stretch.from_name,
                "to_name": stretch.to_name,
                "day": end.isoformat(),
                **counts,
            })
            totals.setdefault(end.isoformat(), {"people": 0, "built_km2": 0.0})
            totals[end.isoformat()]["people"] += counts["people"]
            totals[end.isoformat()]["built_km2"] += counts["built_km2"]
        log.info("%s done", stretch.key)

    series = [
        {"day": day, "people": v["people"], "built_km2": round(v["built_km2"], 1)}
        for day, v in sorted(totals.items())
    ]
    peak = max(series, key=lambda r: r["people"]) if series else None
    return {
        "schema": 1,
        "note": (
            "Modelled residential population (GHSL 2020, 100 m) on ground "
            "the radar found under water and the JRC does not map as "
            "permanent. Not a count of people flooded: it is who lives "
            "where the water now is. A floor, because radar cannot see "
            "water under trees, and modelled, because GHSL spreads census "
            "totals over a built-surface grid."
        ),
        "population_source": POPULATION,
        "built_source": BUILT,
        "built_on": today.isoformat(),
        "stretches": sorted({r["key"] for r in rows}),
        "latest": series[-1] if series else None,
        "peak": peak,
        "series": series,
        "by_stretch": rows,
    }


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def load(path: Path | str = DEFAULT_TABLE) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
