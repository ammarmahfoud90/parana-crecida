"""Build the area-against-stage record that the finding rests on.

The published series is six months long, which is enough to draw a chart
and nowhere near enough to say how water area responds to the river. Over
those six months only two observations fell above an official alert level,
and the interesting part of the relationship is all above it.

So this samples the whole Sentinel-1 era instead. One twelve-day composite
every few weeks since 2016, for each stretch that has both a calibrated
threshold and an official alert level at its downstream gauge, paired with
the stage that day.

It needs Earth Engine and the long record, so it is a local script rather
than part of the daily run, and its output is frozen into the repository
like the travel times and the backtest.

    python scripts/area_stage_history.py --project <ee-project>
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, timedelta
from pathlib import Path

LOG = logging.getLogger("area_stage")

OUT = Path("data/area_stage.json")
#: One Sentinel-1 repeat cycle, the same window the map frames use.
WINDOW_DAYS = 12
#: How far apart to place the windows. Every cycle for ten years is far
#: more Earth Engine than the question needs; this still lands hundreds of
#: observations across the whole range of stages.
STEP_DAYS = 36
FIRST = date(2016, 1, 1)


def windows(first: date, last: date, step: int, length: int):
    end = last
    while end > first:
        yield end - timedelta(days=length - 1), end
        end -= timedelta(days=step)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None)
    parser.add_argument("--first-year", type=int, default=FIRST.year)
    parser.add_argument("--step-days", type=int, default=STEP_DAYS)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import sys

    sys.path.insert(0, "src")
    from parana_crecida import flooded as fl
    from parana_crecida import rainfall as rf
    from parana_crecida.store import DEFAULT_DB, Store

    rf.initialize(project=args.project)

    corridors = [c for c in fl.load_corridors() if c.calibrated]
    table = json.loads(Path("data/flooded.json").read_text(encoding="utf-8"))
    baselines = {s["key"]: s.get("baseline_km2") for s in table["stretches"]}

    last = date.today() - timedelta(days=fl.LAG_DAYS)
    first = date(args.first_year, 1, 1)
    rows: list[dict] = []

    with Store(DEFAULT_DB, read_only=True) as store:
        alerts = {
            r[0]: (r[1], r[2])
            for r in store.con.execute(
                "SELECT series_id, alert, evacuation FROM threshold"
            ).fetchall()
        }
        for corridor in corridors:
            alert, evacuation = alerts.get(corridor.to_id, (None, None))
            baseline = baselines.get(corridor.key)
            if alert is None or not baseline:
                LOG.info("%s: no alert level at %s, skipped",
                         corridor.key, corridor.to_name)
                continue
            levels = {v.day: v.value for v in store.series(corridor.to_id)}
            geometry = fl.corridor_geometry(corridor)
            kept = 0
            for start, end in windows(first, last, args.step_days, WINDOW_DAYS):
                stage = levels.get(end)
                if stage is None:
                    continue
                km2 = fl.water_area_km2(
                    geometry, start, end,
                    threshold_db=corridor.threshold_db,
                    slope_mask=corridor.slope_mask,
                )
                if km2 is None:
                    continue
                rows.append({
                    "key": corridor.key,
                    "gauge": corridor.to_name,
                    "day": end.isoformat(),
                    "km2": round(km2, 1),
                    "stage_m": round(stage, 2),
                    "alert_m": alert,
                    "evacuation_m": evacuation,
                    "above_alert_m": round(stage - alert, 2),
                    "ratio_to_bajante": round(km2 / baseline, 3),
                })
                kept += 1
            LOG.info("%s: %d observations", corridor.key, kept)

    rows.sort(key=lambda r: (r["key"], r["day"]))
    payload = {
        "schema": 1,
        "note": (
            "One 12-day Sentinel-1 composite every few weeks since "
            f"{args.first_year}, paired with the stage at the stretch's "
            "downstream gauge. Stage is given as metres above that gauge's "
            "official INA alert level, which is the only way to pool "
            "gauges: every gauge has its own zero, and metres above alert "
            "mean the same thing everywhere."
        ),
        "built_on": date.today().isoformat(),
        "window_days": WINDOW_DAYS,
        "step_days": args.step_days,
        "observations": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    above = sum(1 for r in rows if r["above_alert_m"] >= 0)
    LOG.info("%d observations, %d of them at or above alert -> %s",
             len(rows), above, args.out)


if __name__ == "__main__":
    main()
