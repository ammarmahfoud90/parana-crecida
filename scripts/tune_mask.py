"""Tune the water mask against optical truth, instead of against the channel.

The thresholds this project shipped with were chosen so that the mask
reproduced JRC permanent water during the 2021 bajante. That tunes the
mask on the one thing it is not for. Measured against optical water during
an actual flood, the result scores an IoU of 0.40 to 0.47 - a mask that is
about half right, which is where the scatter in the published series comes
from.

This sweeps the parameters against the right target: Sentinel-2 water on
a flood date, inside the area the optical scene can actually see. IoU is
the score, because it penalises both failures - water missed and land
called water - and a mask can be made to look good on either alone.

    python scripts/tune_mask.py --project <ee-project>
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

LOG = logging.getLogger("tune")

PAIR_DAYS = 4


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None)
    parser.add_argument("--day", default="2026-10-22")
    parser.add_argument(
        "--keys", default="corrientes_goya,parana_villa_constitucion,goya_la_paz")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import ee

    from validate_mask import agreement, optical_water

    from parana_crecida import flooded as fl
    from parana_crecida import floodwater as fw
    from parana_crecida import rainfall as rf

    rf.initialize(project=args.project)
    day = date.fromisoformat(args.day)
    start, end = day - timedelta(days=PAIR_DAYS), day + timedelta(days=PAIR_DAYS)
    keys = {k for k in args.keys.split(",") if k}
    permanent = ee.Image(fl.GSW).select("occurrence").gt(90).unmask(0)

    stretches = [s for s in fl.load_corridors() if s.key in keys]
    prepared = []
    for stretch in stretches:
        corridor = fl.corridor_geometry(stretch)
        truth, seen, scenes = optical_water(corridor, day)
        if truth is None:
            LOG.info("%s: no clear optical scene", stretch.key)
            continue
        prepared.append((stretch, corridor, truth, seen))
        LOG.info("%s: %d clear scenes", stretch.key, scenes)

    def score(label, build):
        total = {"recall": 0.0, "precision": 0.0, "iou": 0.0, "n": 0}
        for stretch, corridor, truth, seen in prepared:
            try:
                s = agreement(corridor, truth, build(stretch, corridor), seen)
            except Exception as error:  # pragma: no cover
                LOG.warning("  %s %s: %s", label, stretch.key, error)
                continue
            if s["iou"] is None:
                continue
            total["recall"] += s["recall"] or 0
            total["precision"] += s["precision"] or 0
            total["iou"] += s["iou"]
            total["n"] += 1
        if not total["n"]:
            return None
        n = total["n"]
        return {k: round(total[k] / n, 3) for k in ("recall", "precision", "iou")}

    print(f"\n{'variant':34s} {'recall':>7} {'prec':>6} {'IoU':>6}")

    # What the site publishes today.
    result = score("published", lambda s, c: fl.water_mask(
        fl._collection(c, start, end).mosaic().clip(c),
        threshold_db=s.threshold_db, slope_mask=s.slope_mask))
    if result:
        print(f"{'published: VV at its threshold':34s} {result['recall']:7.2f} "
              f"{result['precision']:6.2f} {result['iou']:6.2f}")

    # A plain VH threshold, no change detection: how much is the band alone?
    for cut in (-20, -22, -24):
        def build(s, c, cut=cut):
            vh = (fw._vh(c, start, end).mosaic()
                  .focal_median(fl.SPECKLE_RADIUS_M, "circle", "meters").clip(c))
            hand = ee.Image(fw.HAND).select("hnd")
            return vh.lt(cut).And(hand.lte(fw.HAND_LIMIT_M))
        result = score(f"vh{cut}", build)
        if result:
            print(f"{f'VH < {cut} dB + HAND':34s} {result['recall']:7.2f} "
                  f"{result['precision']:6.2f} {result['iou']:6.2f}")

    # Change detection, swept on how big a drop counts.
    references = {s.key: fw.dry_reference(c) for s, c, _, _ in prepared}
    for drop in (3, 5, 7, 9):
        for ceiling in (-18, -20):
            def build(s, c, drop=drop, ceiling=ceiling):
                current = (fw._vh(c, start, end).mosaic()
                           .focal_median(fl.SPECKLE_RADIUS_M, "circle", "meters")
                           .clip(c))
                hand = ee.Image(fw.HAND).select("hnd")
                flood = (references[s.key].subtract(current).gt(drop)
                         .And(current.lt(ceiling))
                         .And(hand.lte(fw.HAND_LIMIT_M)))
                return flood.Or(permanent)
            result = score(f"drop{drop}/{ceiling}", build)
            if result:
                print(f"{f'drop > {drop} dB, VH < {ceiling} + HAND':34s} "
                      f"{result['recall']:7.2f} {result['precision']:6.2f} "
                      f"{result['iou']:6.2f}")


if __name__ == "__main__":
    main()
