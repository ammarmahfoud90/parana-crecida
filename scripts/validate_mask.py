"""Is the radar water where the water is?

Every check this project has run on its water mask compared it against JRC
permanent water during the 2021 bajante. That validates the *channel* at
low flow, which is the one thing the product is not trying to measure. The
mask has never been checked against a flood.

This checks it against one. Sentinel-2 sees water directly in the visible
and near infrared, so on a cloud-free day it is the closest thing to truth
available here. Where a clear optical scene and a radar pass fall within a
few days of each other over a flooded reach, the two can be compared pixel
for pixel.

Reported as the three numbers that matter, because a single accuracy
figure hides which way a mask is wrong:

- **recall** - of the water the optical scene sees, how much the radar
  finds. Low recall means water missed, usually under vegetation.
- **precision** - of what the radar calls water, how much really is.
  Low precision means land called water: cities, radar shadow, dry ground
  that merely darkened.
- **IoU** - the two together, which is what a remote-sensing paper quotes.

A mask that over-detects scores high recall and poor precision, which is
exactly the failure the change-detection trial showed and could not name.

    python scripts/validate_mask.py --project <ee-project>
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from datetime import date, timedelta

LOG = logging.getLogger("validate")

#: Optical and radar never land on the same day. Water moves slowly enough
#: on this river that a few days either side is acceptable, and the script
#: reports the gap so it can be judged.
PAIR_DAYS = 6

#: Scenes cloudier than this are not worth looking at even after masking.
MAX_CLOUD = 20

#: MNDWI above this is water. Zero is the textbook value and it holds up
#: on a river this turbid, where NDWI does not.
MNDWI_WATER = 0.0

SCALE_M = 60


def optical_water(corridor, day, window=6):
    """Water from Sentinel-2 by MNDWI, and where the optical scene can see.

    A single Sentinel-2 tile is about 110 km across and these corridors
    are two hundred, so one scene covers a fraction of one. Taking the
    first scene and counting everything outside it as dry was the first
    version of this, and it reported zero water in a flood.

    So: every clear scene in the window, mosaicked, plus the mask of where
    any of them actually saw the ground. The comparison then happens only
    inside that, which is the only place either sensor has an opinion.
    """
    import ee

    start = (day - timedelta(days=window)).isoformat()
    end = (day + timedelta(days=window)).isoformat()

    def clear(image):
        scl = image.select("SCL")
        ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return image.updateMask(ok)

    col = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(corridor)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", MAX_CLOUD))
        .map(clear)
    )
    size = col.size().getInfo()
    if not size:
        return None, None, 0

    green = col.select("B3").median()
    swir = col.select("B11").median()
    mndwi = green.subtract(swir).divide(green.add(swir)).clip(corridor)
    seen = mndwi.mask()
    return mndwi.gt(MNDWI_WATER).updateMask(seen), seen, size


def agreement(corridor, truth, test, seen):
    """recall, precision and IoU, inside the region the optical scene saw."""
    import ee

    truth = truth.unmask(0).updateMask(seen)
    test = test.unmask(0).updateMask(seen)
    both = truth.And(test)
    counts = (
        ee.Image.cat(
            truth.rename("truth"), test.rename("test"), both.rename("both"),
            seen.rename("seen"),
        )
        .multiply(ee.Image.pixelArea())
        .reduceRegion(
            reducer=ee.Reducer.sum(), geometry=corridor,
            scale=SCALE_M, maxPixels=1e10, bestEffort=True,
        )
        .getInfo()
    )
    t = float(counts.get("truth") or 0) / 1e6
    p = float(counts.get("test") or 0) / 1e6
    b = float(counts.get("both") or 0) / 1e6
    union = t + p - b
    return {
        "seen_km2": round(float(counts.get("seen") or 0) / 1e6),
        "truth_km2": round(t, 1),
        "test_km2": round(p, 1),
        "recall": round(b / t, 2) if t else None,
        "precision": round(b / p, 2) if p else None,
        "iou": round(b / union, 2) if union else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None)
    parser.add_argument("--keys", default="")
    parser.add_argument("--day", default="2026-10-22")
    parser.add_argument("--out", default=None,
                        help="write the table to this JSON file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    sys.path.insert(0, "src")
    import ee

    from parana_crecida import flooded as fl
    from parana_crecida import floodwater as fw
    from parana_crecida import rainfall as rf

    rf.initialize(project=args.project)
    day = date.fromisoformat(args.day)
    wanted = {k for k in args.keys.split(",") if k}

    print(f"optical truth near {day}, MNDWI > {MNDWI_WATER}\n")
    print(f"{'stretch':26s} {'method':22s} {'truth':>7} {'found':>7} "
          f"{'recall':>7} {'prec':>6} {'IoU':>5}")

    table: list[dict] = []
    for stretch in fl.load_corridors():
        if wanted and stretch.key not in wanted:
            continue
        corridor = fl.corridor_geometry(stretch)
        truth, seen, scenes = optical_water(corridor, day)
        if truth is None:
            print(f"{stretch.key:26s} no clear optical scene")
            continue

        start = day - timedelta(days=PAIR_DAYS)
        end = day + timedelta(days=PAIR_DAYS)

        # The method the site shipped with: a fixed threshold on VV,
        # chosen per stretch against the channel at low flow.
        mosaic = fl._collection(corridor, start, end).mosaic().clip(corridor)
        old = fl.water_mask(
            mosaic, threshold_db=stretch.threshold_db, slope_mask=stretch.slope_mask
        )

        # The method in use: one VH threshold for the whole river, bounded
        # by height above nearest drainage.
        new = fw.water_mask(corridor, start, end)

        scored = None
        row = {"key": stretch.key, "from_name": stretch.from_name,
               "to_name": stretch.to_name, "clear_scenes": scenes}
        for label, mask in (("published VV", old), ("VH + HAND", new)):
            try:
                scores = agreement(corridor, truth, mask, seen)
            except Exception as error:  # pragma: no cover
                LOG.warning("  %s %s: %s", stretch.key, label, error)
                continue
            scored = scores
            row["previous" if label == "published VV" else "now"] = {
                k: scores[k] for k in ("recall", "precision", "iou")
            }
            print(
                f"{stretch.key:26s} {label:22s} {scores['truth_km2']:7.0f} "
                f"{scores['test_km2']:7.0f} "
                f"{(scores['recall'] if scores['recall'] is not None else 0):7.2f} "
                f"{(scores['precision'] if scores['precision'] is not None else 0):6.2f} "
                f"{(scores['iou'] if scores['iou'] is not None else 0):5.2f}"
            )
        if scored is None:
            print(f"{stretch.key:26s} no radar pass in the window")
            continue
        row["truth_km2"] = scored["truth_km2"]
        row["seen_km2"] = scored["seen_km2"]
        table.append(row)
        print(f"{'':26s} ({scenes} clear scenes, "
              f"{scored['seen_km2']} km2 of the corridor visible)")

    if not table:
        return
    print(f"\n{'river-wide mean':26s} {'VH + HAND':22s} {'':7} {'':7} "
          f"{_mean(table, 'now', 'recall'):7.2f} {_mean(table, 'now', 'precision'):6.2f} "
          f"{_mean(table, 'now', 'iou'):5.2f}")
    print(f"{'':26s} {'published VV':22s} {'':7} {'':7} "
          f"{_mean(table, 'previous', 'recall'):7.2f} "
          f"{_mean(table, 'previous', 'precision'):6.2f} "
          f"{_mean(table, 'previous', 'iou'):5.2f}")

    if args.out:
        import json

        payload = {
            "schema": 1,
            "note": ("Both water masks scored against Sentinel-2 optical water "
                     "on a flood date, inside the area the optical scene can "
                     "see. Recall is water found, precision is water correctly "
                     "called, IoU is the two together."),
            "measured_against": f"Sentinel-2 MNDWI water, {args.day}",
            "day": args.day,
            "radar_window_days": PAIR_DAYS,
            "mndwi_threshold": MNDWI_WATER,
            "stretches": table,
            "recall": _mean(table, "now", "recall"),
            "precision": _mean(table, "now", "precision"),
            "iou": _mean(table, "now", "iou"),
            "previous": {
                "method": "per-stretch VV threshold",
                "recall": _mean(table, "previous", "recall"),
                "precision": _mean(table, "previous", "precision"),
                "iou": _mean(table, "previous", "iou"),
            },
        }
        pathlib.Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {len(table)} stretches to {args.out}")


def _mean(rows, side, key):
    values = [r[side][key] for r in rows if side in r and r[side][key] is not None]
    return round(sum(values) / len(values), 3) if values else None


if __name__ == "__main__":
    main()
