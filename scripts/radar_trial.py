"""Does a better radar method actually do better?

Three changes are on trial against the thresholded VV the site has used
since the start, and each is here for a stated reason:

**VH instead of VV.** Wind roughens a water surface and lifts its
backscatter toward the land value, which is the documented source of the
scatter in the published series - the 70% plausibility filter exists
because of it. Cross-polarised VH is markedly less sensitive to that
roughening, so the same flood should look steadier.

**HAND instead of slope.** Height Above Nearest Drainage says how far a
pixel sits above the water it would drain to. Ground thirty metres above
the nearest channel cannot hold standing water, whatever its backscatter -
and a radar shadow on a valley wall is exactly such ground, read as dark.
That is the failure that defeated both gorge stretches and that slope
masking only half fixed.

**Change detection instead of an absolute threshold.** Nine hand-tuned
thresholds exist because backscatter is not comparable between stretches.
A drop relative to each pixel's own dry-season value is comparable
everywhere, needs no per-stretch calibration, and cannot be fooled by a
reach that is simply darker than its neighbours.

Scored on the two things that matter: whether the measurement tracks the
gauge, and how much it scatters at a given river level.

    python scripts/radar_trial.py --project <ee-project>
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import date, timedelta

LOG = logging.getLogger("radar")

#: Dry reference: the 2021 bajante, the lowest Parana in 77 years, which
#: the thresholds were already calibrated against.
DRY = (date(2021, 8, 1), date(2021, 9, 15))

#: Metres above the nearest drainage beyond which standing water is not
#: credible. Generous: the Parana floodplain is flat and a tight limit
#: would cut real water on the far bank.
HAND_LIMIT_M = 25.0

#: Backscatter drop, in dB, below a pixel's own dry-season value that
#: counts as water having arrived.
DROP_DB = 3.0

#: And a ceiling, so a dry pixel that merely got a little darker does not
#: qualify.
VH_CEILING_DB = -18.0

WINDOW_DAYS = 12
CYCLES = 15


def collection(corridor, start, end, band):
    import ee

    return (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(corridor)
        .filterDate(start.isoformat(), end.isoformat())
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", band))
        .select(band)
    )


def water_new(corridor, start, end, dry_reference):
    """Water by drop against the dry reference, bounded by HAND."""
    import ee

    from parana_crecida import flooded as fl

    current = collection(corridor, start, end, "VH").mosaic().clip(corridor)
    smoothed = current.focal_median(fl.SPECKLE_RADIUS_M, "circle", "meters")
    drop = dry_reference.subtract(smoothed)
    hand = ee.Image("MERIT/Hydro/v1_0_1").select("hnd")
    return (
        drop.gt(DROP_DB)
        .And(smoothed.lt(VH_CEILING_DB))
        .And(hand.lte(HAND_LIMIT_M))
    )


def area_km2(corridor, mask):
    import ee

    value = (
        mask.selfMask()
        .multiply(ee.Image.pixelArea())
        .reduceRegion(
            reducer=ee.Reducer.sum(), geometry=corridor,
            scale=60, maxPixels=1e10, bestEffort=True,
        )
        .getInfo()
    )
    total = next(iter(value.values()), 0) or 0
    return float(total) / 1e6


def correlate(xs, ys):
    n = len(xs)
    if n < 6:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(
        sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)
    )
    return None if den == 0 else num / den


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None)
    parser.add_argument("--keys", default="")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    sys.path.insert(0, "src")
    import ee

    from parana_crecida import flooded as fl
    from parana_crecida import rainfall as rf
    from parana_crecida.store import DEFAULT_DB, Store

    rf.initialize(project=args.project)
    wanted = {k for k in args.keys.split(",") if k}
    stretches = [s for s in fl.load_corridors() if not wanted or s.key in wanted]

    last = date.today() - timedelta(days=fl.LAG_DAYS)
    with Store(DEFAULT_DB, read_only=True) as store:
        print(f"{'stretch':28s} {'n':>3} {'min':>7} {'max':>7} {'range':>6} "
              f"{'r(stage)':>9} {'spread':>7}")
        for stretch in stretches:
            corridor = fl.corridor_geometry(stretch)
            dry = (
                collection(corridor, *DRY, "VH")
                .median()
                .focal_median(fl.SPECKLE_RADIUS_M, "circle", "meters")
                .clip(corridor)
            )
            levels = {v.day: v.value for v in store.series(stretch.to_id)}

            days, areas, stages = [], [], []
            for index in range(CYCLES - 1, -1, -1):
                end = last - timedelta(days=WINDOW_DAYS * index)
                start = end - timedelta(days=WINDOW_DAYS - 1)
                try:
                    km2 = area_km2(corridor, water_new(corridor, start, end, dry))
                except Exception as error:  # pragma: no cover
                    LOG.warning("  %s %s: %s", stretch.key, end, error)
                    continue
                if km2 <= 0:
                    continue
                days.append(end)
                areas.append(km2)
                if end in levels:
                    stages.append((levels[end], km2))

            if not areas:
                print(f"{stretch.key:28s}  no usable composites")
                continue
            r = correlate([s for s, _ in stages], [a for _, a in stages])
            mean = sum(areas) / len(areas)
            sd = (sum((a - mean) ** 2 for a in areas) / len(areas)) ** 0.5
            print(
                f"{stretch.key:28s} {len(areas):3d} {min(areas):7.0f} "
                f"{max(areas):7.0f} {max(areas)/min(areas):6.2f} "
                f"{(f'{r:+.2f}' if r is not None else '    -'):>9} "
                f"{100*sd/mean:6.0f}%"
            )


if __name__ == "__main__":
    main()
