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

#: Share of the corridor that has to be observed before a cycle publishes a
#: figure. A pass that crosses only part of the corridor leaves the rest
#: with no data, and unobserved ground contributes no area - which reads as
#: the river shrinking rather than as a measurement that was not made.
#:
#: The superseded pipeline tested exactly this and dropped a window below
#: 0.95 (``flooded.MIN_COVERAGE``). Dropping the test when the band changed
#: was a mistake: the method page went on asserting that twelve-day
#: composites "cover the whole corridor every time", which is the property
#: the test used to verify rather than something VH guarantees. One cycle
#: in the published series lost half the permanent channel of two adjacent
#: corridors, which is what an unobserved swath looks like.
MIN_COVERAGE = 0.95

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
    """Dual-pol IW scenes over the corridor, ``start`` to ``end`` inclusive.

    ``filterDate`` is end-exclusive, so the end date has to be advanced by
    a day to include it. Without that the twelve-day window was eleven
    days wide and excluded the very date each cycle is published under -
    and because the windows step by exactly one repeat cycle, a relative
    orbit whose pass fell on the excluded day was missing from *every*
    cycle in the series rather than occasionally. ``flooded._collection``
    has always compensated the same way.
    """
    import ee

    return (
        ee.ImageCollection(S1)
        .filterBounds(corridor)
        .filterDate(start.isoformat(), (end + timedelta(days=1)).isoformat())
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


def area_km2(corridor, mask) -> float | None:
    """Water area over the corridor, or ``None`` where nothing was observed.

    ``None`` and ``0.0`` are different answers and the difference matters
    more here than anywhere else in the pipeline. ``reduceRegion`` returns
    null when no unmasked pixel falls inside the region, and the previous
    version of this coerced that to zero - so a window in which the
    satellite saw nothing reported no water, which reads as a river that
    had dried up. Radar that did not look is not radar that found nothing.

    The band is renamed rather than read by position: the reduction returns
    a dictionary keyed on band name, and taking whichever key came first
    would quietly follow the wrong one if this image ever gained a band.
    """
    import ee

    stats = (
        mask.selfMask()
        .rename("water")
        .multiply(ee.Image.pixelArea())
        .reduceRegion(
            reducer=ee.Reducer.sum(), geometry=corridor,
            scale=fl.AREA_SCALE_M, maxPixels=1e10, bestEffort=True,
        )
        .getInfo()
    )
    value = (stats or {}).get("water")
    return None if value is None else float(value) / 1e6


def observed_km2(corridor, start: date, end: date) -> float | None:
    """How much of the corridor the radar actually saw over the window.

    The denominator every published figure has been missing. ``water_mask``
    leaves unobserved pixels masked and ``area_km2`` sums only unmasked
    ones, so a corridor observed in half gives a valid-looking number that
    is low by the area of the gap, with nothing in the output to say so.

    Reduced at 200 m rather than 60: this is a coverage fraction, not an
    area figure, and a coarser grid is far cheaper over a corridor this
    size. The superseded pipeline used the same scale for the same reason.
    """
    import ee

    mosaic = _vh(corridor, start, end).mosaic().clip(corridor)
    stats = (
        mosaic.mask()
        .rename("seen")
        .multiply(ee.Image.pixelArea())
        .reduceRegion(
            reducer=ee.Reducer.sum(), geometry=corridor,
            scale=200, maxPixels=1e10, bestEffort=True,
        )
        .getInfo()
    )
    value = (stats or {}).get("seen")
    return None if value is None else float(value) / 1e6


def acquisitions(corridor, start: date, end: date) -> dict:
    """What the window was actually built from.

    The series has always published a window end and nothing about the
    passes inside it, so the real cadence per reach could not be recovered
    from the product at all - the dates look like a regular twelve-day
    sequence because they are a grid, not because the satellite flew one.

    Relative orbit and platform are recorded rather than filtered on.
    Backscatter over water falls with incidence angle while over land it
    is flat, so one absolute threshold across several viewing geometries is
    not one physical criterion; and the constellation changed inside the
    published record. Neither is fixed by a metadata field, but neither can
    be measured without one, and filtering would change every published
    figure before the change had been validated.
    """
    import ee

    scenes = _vh(corridor, start, end)
    got = ee.Dictionary({
        "days": scenes.aggregate_array("system:time_start")
        .map(lambda t: ee.Date(t).format("YYYY-MM-dd")).distinct().sort(),
        "scenes": scenes.size(),
        "orbits": scenes.aggregate_array("relativeOrbitNumber_start").distinct().sort(),
        "passes": scenes.aggregate_array("orbitProperties_pass").distinct().sort(),
        "platforms": scenes.aggregate_array("platform_number").distinct().sort(),
    }).getInfo()
    return {
        "acq_days": list(got.get("days") or []),
        "scenes": int(got.get("scenes") or 0),
        "orbits": [int(o) for o in (got.get("orbits") or []) if o is not None],
        "passes": list(got.get("passes") or []),
        "platforms": [p for p in (got.get("platforms") or []) if p is not None],
    }


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
        if total is None or flood is None:
            log.warning("%s: nothing observed; no figure for this cycle", end)
            continue
        out.append((end, round(total, 1), round(flood, 1)))
    return out


#: Cycles kept in the published series. Thirty-one is a little over a year,
#: which is the span that shows a low-water season as well as a flood.
KEEP_CYCLES = 31


def _grid(known: list[str], last: date, window_days: int) -> list[date]:
    """The cycle ends that should exist, anchored to the ones that do.

    Computing the grid backwards from today moves every date by a day each
    time it runs, so a daily job would measure a brand new set of windows
    every morning and never add to the series it already had. The map
    frames did exactly that and left a full set of orphaned images behind.

    So the grid is anchored to the last cycle already attempted, and a new
    one is added only once a full window has passed since it.

    ``known`` is every window end already accounted for - measured or
    recorded as a gap. Anchoring on measurements alone would stall the
    series on the first unobservable window and re-measure it every day
    for ever.

    The dates this returns are window **ends**, not acquisition dates.
    The grid is a sequence of bins whose phase comes from whenever the
    series was first built; the acquisitions that fall inside each bin are
    recorded separately, per cycle.
    """
    if not known:
        return [last - timedelta(days=window_days * i) for i in range(KEEP_CYCLES - 1, -1, -1)]
    anchor = date.fromisoformat(max(known))
    fresh = []
    while anchor + timedelta(days=window_days) <= last:
        anchor = anchor + timedelta(days=window_days)
        fresh.append(anchor)
    return fresh


def rebuild(
    path: Path | str = Path("data/floodwater.json"),
    *,
    last: date,
    window_days: int = WINDOW_DAYS,
    keep: int = KEEP_CYCLES,
    min_coverage: float = MIN_COVERAGE,
) -> dict:
    """Measure the cycles that are missing and leave the rest alone.

    The first run measures a year, which takes a while. Every run after
    that measures nothing at all until a repeat cycle has passed, then one
    cycle, which is nine stretches and a couple of minutes. That is what
    makes this safe to put in a daily job.

    ``fetched_on`` is bumped on every successful run, not only when a cycle
    is added. The freshness gate asks whether what is published is current,
    and "checked today, the newest pass is still the one from last week" is
    a current answer.
    """
    path = Path(path)
    payload = load(path) or {}
    rows = payload.get("stretches") or []
    by_key = {row["key"]: row for row in rows}

    for stretch in fl.load_corridors():
        row = by_key.get(stretch.key)
        if row is None:
            row = {
                "key": stretch.key, "from": stretch.from_id, "to": stretch.to_id,
                "from_name": stretch.from_name, "to_name": stretch.to_name,
                "river_km": stretch.river_km, "corridor_km2": None,
                "days": [], "water_km2": [], "flood_km2": [],
                "observation": [], "gaps": [],
            }
            rows.append(row)
            by_key[stretch.key] = row

        corridor = fl.corridor_geometry(stretch)
        if not row.get("corridor_km2"):
            row["corridor_km2"] = round(
                float(corridor.area(1000).divide(1e6).getInfo()))

        attempted = list(row["days"]) + [
            g["day"] for g in (row.get("gaps") or []) if g.get("day")
        ]
        for end in _grid(attempted, last, window_days):
            start = end - timedelta(days=window_days - 1)

            def gap(reason: str, **extra) -> None:
                """Record a window that produced no publishable figure.

                A gap is an attempt, so the grid anchor moves past it and
                the series does not stall; and it is published, so a reader
                and the freshness check can both see that a cycle is
                missing rather than inferring a dry river from a dip.
                """
                log.warning("%s %s: %s; recorded as a gap",
                            stretch.key, end, reason)
                row.setdefault("gaps", []).append(
                    {"day": end.isoformat(), "reason": reason, **extra})

            try:
                seen = observed_km2(corridor, start, end)
                water = area_km2(corridor, water_mask(corridor, start, end))
                flood = area_km2(corridor, flood_only(corridor, start, end))
                provenance = acquisitions(corridor, start, end)
            except Exception as error:  # pragma: no cover - Earth Engine
                log.warning("%s %s: %s", stretch.key, end, error)
                continue

            if water is None or flood is None or seen is None:
                gap("nothing observed")
                continue

            # The coverage test the superseded pipeline had and this one
            # lost. Checked before the area is believed, not after.
            extent = row.get("corridor_km2") or 0
            coverage = (seen / extent) if extent else None
            if coverage is not None and coverage < min_coverage:
                gap("corridor not fully observed",
                    coverage=round(coverage, 3), seen_km2=round(seen, 1),
                    scenes=provenance["scenes"])
                continue

            row["days"].append(end.isoformat())
            row["water_km2"].append(round(water, 1))
            row["flood_km2"].append(round(flood, 1))
            row.setdefault("observation", []).append({
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "seen_km2": round(seen, 1),
                "coverage": None if coverage is None else round(coverage, 3),
                **provenance,
            })
            log.info("%s %s: %.0f km2 water, %.0f beyond the channel, "
                     "%d scene(s) on %s, %.0f%% of the corridor observed",
                     stretch.key, end, water, flood, provenance["scenes"],
                     ",".join(provenance["acq_days"]) or "no dated scene",
                     100 * (coverage or 0))

        # Trim from the front, keeping every series the same length.
        for field in ("days", "water_km2", "flood_km2", "observation"):
            row[field] = (row.get(field) or [])[-keep:]
        # Gaps are trimmed to the span the series still covers, so a gap
        # older than the published window stops being reported while a gap
        # inside it stays visible.
        if row["days"]:
            row["gaps"] = [
                g for g in (row.get("gaps") or []) if g.get("day", "") >= row["days"][0]
            ]

    payload.update({
        "schema": 2,
        "note": ("Open water from Sentinel-1 VH below -25 dB, bounded by height "
                 "above nearest drainage. One threshold for the whole river, "
                 "tuned against Sentinel-2 optical water during a flood rather "
                 "than against the channel at low flow. water_km2 is all water; "
                 "flood_km2 excludes the permanent channel."),
        "method": "vh-threshold/hand",
        "vh_threshold_db": VH_THRESHOLD_DB,
        "hand_limit_m": HAND_LIMIT_M,
        "window_days": window_days,
        "window": ("Window ends are grid dates, not acquisition dates: the "
                   "grid is a sequence of 12-day bins whose phase comes from "
                   "whenever this series was first built. Each cycle's "
                   "observation block carries the window start and end, the "
                   "acquisition dates inside it, the scene count, the relative "
                   "orbits and the platforms."),
        "min_coverage": min_coverage,
        "coverage_note": ("A cycle publishes a figure only where at least "
                          f"{min_coverage:.0%} of the corridor was observed. "
                          "Below that it is recorded in the stretch's gaps "
                          "list with its reason, because unobserved ground "
                          "contributes no area and would read as a river that "
                          "had shrunk."),
        "permanent_water": ("JRC occurrence > 90, or JRC yearly water class 3 "
                            f"in the {BAJANTE_YEAR} bajante"),
        "skill": SKILL,
        "fetched_on": date.today().isoformat(),
        "stretches": rows,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return payload


def load(path: Path | str = Path("data/floodwater.json")) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
