"""Water from radar, tuned against water.

This replaces the method the site shipped with, and the reason is a number
nobody had measured: checked against Sentinel-2 optical water during an
actual flood, the old mask scored an IoU of 0.43. It was about half right,
and that is where the scatter in the published series came from.

The old mask was a fixed VV threshold, chosen per stretch so that it
reproduced JRC permanent water during the 2021 bajante. That tunes a flood
mask on the one thing it is not for - the channel, at low flow - and it
needed nine different numbers to do even that, two of which never worked
at all.

**What replaced it, and what each part is worth.** Measured over every
stretch with a clear optical scene - eight of the nine - against
Sentinel-2 water on 22 October 2026:

    published: VV at nine thresholds      recall 0.70  precision 0.77  IoU 0.59
    VH < -25 dB, HAND <= 10 m             recall 0.80  precision 0.82  IoU 0.69

Per stretch, and this is the part a single figure hides:

    Puerto Iguazu - Libertador   0.83     Corrientes - Goya            0.48
    Libertador - Posadas         0.90     Goya - La Paz                0.53
    Posadas - Ita Ibate          0.87     La Paz - Parana              0.67
    Ita Ibate - Corrientes       0.64     Parana - Villa Constitucion  0.61

**The gorge was never the problem.** The two stretches above Posadas were
withheld from every total this project published, on the grounds that no
per-stretch VV threshold could be fitted there: the two neighbouring
reaches wanted thresholds 6.5 dB apart, which was read as radar shadow on
the valley walls defeating the measurement. Scored against optical water
they are the two best reaches on the river, at 0.83 and 0.90. The shadow
was real; the threshold was the wrong instrument for it, and HAND handles
it directly. What failed the old test was the old test.

The weak end is the floodplain below Corrientes - wide, vegetated, and
exactly where radar at this wavelength loses water under canopy.

**VH rather than VV.** Wind roughens a water surface and lifts its
backscatter toward the land value; cross-polarised VH is far less
sensitive to that. This is the single biggest gain and it is why the old
series needed a filter that threw away whole passes.

**Height above nearest drainage rather than slope.** Ground ten metres
above the water it would drain to cannot hold standing water, whatever it
looks like to radar - and a radar shadow on a valley wall is exactly such
ground, read as dark.

**One threshold, not nine.** Tuned against optical water rather than
against the channel, a single value works the length of the river. The
per-stretch calibration existed to paper over a badly chosen band.

**Change detection was tried and rejected.** Comparing each pixel against
its own 2021 value scored 0.38 to 0.45 - better recall, much worse
precision, because a 3 dB drop catches vegetation change, soil moisture
and a different look angle along with the water. The gain the trial saw
came from VH, not from the differencing.

**What 0.69 means, and what it does not.** Four fifths of the water is
found and four fifths of what is called water is water. Published
flood-mapping methods reach 0.7 to 0.9: the upper half of the river sits
inside that range and the lower half does not. What is missing is mostly
water under vegetation, which radar at this wavelength cannot see and no
threshold will recover, so every figure here is a floor.

A stretch publishes a number only if it was scored against optical water
and cleared the floor in export.SKILL_FLOOR. The Delta had no clear
optical scene on the validation date, so it publishes nothing - not
because it is known to be wrong, but because it is not known to be right.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path

from . import flooded as fl

log = logging.getLogger(__name__)

S1 = "COPERNICUS/S1_GRD"
HAND = "MERIT/Hydro/v1_0_1"
GSW_YEARLY = "JRC/GSW1_4/YearlyHistory"

#: The 2021 bajante, the lowest Parana in 77 years. Water present then is
#: channel, not flood.
BAJANTE_YEAR = 2021

#: Backscatter below which VH is water. One number for the whole river,
#: tuned against Sentinel-2 water on a flood date rather than against the
#: channel at low flow.
VH_THRESHOLD_DB = -25.0

#: Metres above the nearest drainage beyond which standing water is not
#: credible. Ten was the best of 10, 15 and 25 and the difference between
#: them is small: HAND is doing its work on the valley walls, where the
#: numbers are far larger than any of these.
HAND_LIMIT_M = 10.0

#: One Sentinel-1 repeat cycle.
WINDOW_DAYS = 12

#: The score this was tuned to, kept here so the page can quote it and a
#: future change has to beat it.
#: A fallback only. The real table is measured by scripts/validate_mask.py
#: and committed to data/mask_skill.json, per stretch, and that is what the
#: page quotes and what decides which stretches publish a figure at all.
SKILL = {
    "measured_against": "Sentinel-2 MNDWI water, 22 October 2026",
    "stretches": 8,
    "recall": 0.80,
    "precision": 0.82,
    "iou": 0.69,
    "previous": {"method": "per-stretch VV threshold", "recall": 0.70,
                 "precision": 0.77, "iou": 0.59},
}


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


def water_mask(corridor, start: date, end: date):
    """Open water over the window: dark in VH, low above the drainage."""
    import ee

    vh = (
        _vh(corridor, start, end)
        .mosaic()
        .focal_median(fl.SPECKLE_RADIUS_M, "circle", "meters")
        .clip(corridor)
    )
    hand = ee.Image(HAND).select("hnd")
    return vh.lt(VH_THRESHOLD_DB).And(hand.lte(HAND_LIMIT_M))


def permanent_water():
    """Water that is always there, by two definitions rather than one.

    JRC's ``occurrence`` band is the share of the 1984-2021 record in which
    a pixel held water, and a reservoir impounded part-way through that
    record can never reach 90% however permanent it now is. Yacyreta filled
    in 2011, so it scores somewhere near half, and the published mask read
    353 km2 of standing reservoir as flood water - in every frame, low
    water included, in the most conspicuous place on the river. The
    whole-river animation is what made it obvious: a flood that is still
    there in the driest week of the year is not a flood.

    So permanent means permanent in the 1984-2021 average *or* permanent
    in 2021 itself, the lowest Parana in 77 years. Anything under water in
    that year is in the channel by any reasonable definition.
    """
    import ee

    occurrence = ee.Image(fl.GSW).select("occurrence").gt(90).unmask(0)
    recent = (
        ee.ImageCollection(GSW_YEARLY)
        .filter(ee.Filter.eq("year", BAJANTE_YEAR))
        .first()
        .select("waterClass")
        .eq(3)
        .unmask(0)
    )
    return occurrence.Or(recent)


def flood_only(corridor, start: date, end: date):
    """Water that is not the river's own permanent channel."""
    return water_mask(corridor, start, end).And(permanent_water().Not())


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
    *,
    last: date,
    cycles: int,
    window_days: int = WINDOW_DAYS,
) -> list[tuple[date, float, float]]:
    """``(window end, all water km2, flood-only km2)`` per repeat cycle."""
    out: list[tuple[date, float, float]] = []
    for index in range(cycles - 1, -1, -1):
        end = last - timedelta(days=window_days * index)
        start = end - timedelta(days=window_days - 1)
        try:
            total = area_km2(corridor, water_mask(corridor, start, end))
            flood = area_km2(corridor, flood_only(corridor, start, end))
        except Exception as error:  # pragma: no cover - Earth Engine
            log.warning("%s: %s", end, error)
            continue
        out.append((end, round(total, 1), round(flood, 1)))
    return out


def load(path: Path | str = Path("data/floodwater.json")) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
