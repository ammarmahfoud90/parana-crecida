"""Flood water, by what changed since the river was low.

This replaces the thresholded VV the site measured with for its first
weeks. That method asked "is this pixel dark enough to be water", which
needs a different answer in every reach - nine hand-tuned thresholds, two
reaches where no threshold worked at all, and a city that on one June day
was read as a lake because smooth tarmac is dark to radar.

This asks a different question: **has this pixel got darker than it is
when the river is low?** Three changes, each for a measured reason.

**VH rather than VV.** Wind roughens water and lifts its backscatter
toward the land value. That is the documented source of the scatter in the
old series - the filter that threw away any pass finding less than 70% of
the permanent channel exists because of it. Cross-polarised VH is far less
sensitive to roughening.

**Change against each pixel's own dry-season value, not an absolute
threshold.** Backscatter is not comparable between reaches, which is why
the thresholds had to be tuned one at a time and why the gorge defeated
them. A *drop* is comparable everywhere. It also disposes of the city: a
street is dark in August and dark in June, so it never drops, so it is
never flooded.

**HAND rather than slope.** Height Above Nearest Drainage is how far a
pixel stands above the water it would drain to. Ground twenty-five metres
above the nearest channel cannot hold standing water whatever its
backscatter, and a radar shadow on a valley wall is exactly that.

**What the number now means, which is not what it meant before.** The old
figure was all water in the corridor, channel included, compared against a
low-water baseline. This is flood water only: ground that is dry when the
river is low and is not now. The channel is excluded by construction
rather than by subtraction, so there is no baseline to quote and no
"times the bajante" - just square kilometres of ground under water that
normally is not.

Measured against the gauge, on the trial that decided the switch:

    stretch                      old r    new r
    corrientes_goya              +0.43    +0.71
    la_paz_parana                +0.01    +0.47

La Paz to Parana is the one that matters. The site had published that it
could not be measured - a small signal under large scatter. It was the
method.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from . import flooded as fl

log = logging.getLogger(__name__)

S1 = "COPERNICUS/S1_GRD"
HAND = "MERIT/Hydro/v1_0_1"

#: The 2021 bajante, the lowest Parana in 77 years: the driest reference
#: the satellite record offers.
DRY_START = date(2021, 8, 1)
DRY_END = date(2021, 9, 15)

#: Metres above the nearest drainage beyond which standing water is not
#: credible. Generous, because this floodplain is flat and a tight limit
#: would cut real water on the far bank.
HAND_LIMIT_M = 25.0

#: Drop below a pixel's own dry-season backscatter, in dB, that counts as
#: water having arrived.
DROP_DB = 3.0

#: And a ceiling, so a dry pixel that merely darkened a little does not
#: qualify as flooded.
VH_CEILING_DB = -18.0

#: One Sentinel-1 repeat cycle.
WINDOW_DAYS = 12


def _vh(corridor, start: date, end: date):
    import ee

    return (
        ee.ImageCollection(S1)
        .filterBounds(corridor)
        .filterDate(start.isoformat(), end.isoformat())
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select("VH")
    )


def dry_reference(corridor):
    """Each pixel's backscatter when the river was at its lowest."""
    return (
        _vh(corridor, DRY_START, DRY_END)
        .median()
        .focal_median(fl.SPECKLE_RADIUS_M, "circle", "meters")
        .clip(corridor)
    )


def flood_mask(corridor, start: date, end: date, reference):
    """Ground under water now that is dry when the river is low."""
    import ee

    current = (
        _vh(corridor, start, end)
        .mosaic()
        .focal_median(fl.SPECKLE_RADIUS_M, "circle", "meters")
        .clip(corridor)
    )
    drop = reference.subtract(current)
    hand = ee.Image(HAND).select("hnd")
    return (
        drop.gt(DROP_DB)
        .And(current.lt(VH_CEILING_DB))
        .And(hand.lte(HAND_LIMIT_M))
    )


def area_km2(corridor, mask) -> float:
    import ee

    stats = (
        mask.selfMask()
        .multiply(ee.Image.pixelArea())
        .reduceRegion(
            reducer=ee.Reducer.sum(), geometry=corridor,
            scale=fl.AREA_SCALE_M, maxPixels=1e10, bestEffort=True,
        )
        .getInfo()
    )
    return float(next(iter(stats.values()), 0) or 0) / 1e6


def series(
    corridor,
    reference,
    *,
    last: date,
    cycles: int,
    window_days: int = WINDOW_DAYS,
) -> list[tuple[date, float]]:
    """``(window end, flood km2)`` per repeat cycle, newest last."""
    out: list[tuple[date, float]] = []
    for index in range(cycles - 1, -1, -1):
        end = last - timedelta(days=window_days * index)
        start = end - timedelta(days=window_days - 1)
        try:
            out.append((end, round(area_km2(corridor, flood_mask(
                corridor, start, end, reference)), 1)))
        except Exception as error:  # pragma: no cover - Earth Engine
            log.warning("%s: %s", end, error)
    return out
