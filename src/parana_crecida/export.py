"""Export the site's data files from the store.

The export is the gate. It takes a :class:`~parana_crecida.validate.
ValidationResult` and refuses to write anything unless it passed, so a bad
day cannot reach the published page even by accident - the signature makes
"export without validating" impossible to write.

Writes are atomic (temp file then replace), so an interrupted run leaves the
previous file intact rather than a truncated one.

Nothing in the output is translated. Names are proper nouns and the rest are
stable keys (``branch``, ``flags``, ``status``) that the page turns into
Spanish or English. Keeping language out of the data means adding a third
language never touches the pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from ina_a5.observations import DEFAULT_TZ

from . import gauges as roster
from .store import GaugeToday, Store
from .thresholds import INA_ALERT_URL, Threshold, classify
from .validate import ValidationResult, freshness_note

log = logging.getLogger(__name__)

DEFAULT_SITE_DATA = Path("site/data")
RIVER_TODAY = "river_today.json"
WAVE = "wave.json"
DRIVERS = "drivers.json"
FLOODED = "flooded.json"
RIVER_STAGE = "river_stage.json"
TRACK = "track.json"
CSV_DIR = "csv"
AREA_STAGE = "area_stage.json"

#: Bump when the shape changes, so a cached page can tell.
SCHEMA_VERSION = 1


class ExportBlocked(RuntimeError):
    """Validation failed, so nothing was written and the old data stands."""


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: Path
    gauges: int
    with_official_thresholds: int
    last_data_day: date | None


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


def gauge_payload(gauge: GaugeToday, threshold: Threshold | None) -> dict[str, object]:
    """One gauge as the page needs it."""
    return {
        "series_id": gauge.series_id,
        "name": gauge.name,
        "branch": gauge.branch,
        "tier": gauge.tier,
        "flags": list(gauge.flags),
        "unit": gauge.unit,
        "day": gauge.day.isoformat() if gauge.day else None,
        "value": _round(gauge.value),
        "change": _round(gauge.change),
        "change_days": gauge.change_days,
        "alert": _round(gauge.alert),
        "evacuation": _round(gauge.evacuation),
        "status": classify(gauge.value, threshold),
    }


def build_river_today(
    store: Store,
    thresholds: dict[str, Threshold],
    result: ValidationResult,
    *,
    now: datetime | None = None,
    today: date | None = None,
) -> dict[str, object]:
    """The river-today payload. Pure: builds the dict, writes nothing."""
    now = now or datetime.now(DEFAULT_TZ)
    today = today or now.date()
    gauges = store.river_today()
    last = store.last_data_day()
    source_dates = {t.source_date for t in thresholds.values() if t.source_date}

    return {
        "schema": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "last_data_day": last.isoformat() if last else None,
        "data_age_days": (today - last).days if last else None,
        "freshness": freshness_note(last, today),
        "official_source_url": INA_ALERT_URL,
        "thresholds": {
            "source_date": max(source_dates).isoformat() if source_dates else None,
            "gauges_with_values": len(thresholds),
        },
        "validation": {
            "ok": result.ok,
            "warnings": [c.name for c in result.warnings],
        },
        "gauges": [gauge_payload(g, thresholds.get(g.series_id)) for g in gauges],
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    """Write JSON atomically, pretty enough that a diff is readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def _maybe_json(path: Path) -> dict | None:
    """Read a sibling export if it is there, else None.

    The verdict quotes the wave and the satellite, and both are written by
    exports that can legitimately be absent - a fresh checkout, or a run
    without an Earth Engine credential. A missing one costs a clause, not
    the page.
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("could not read %s for the verdict", path)
        return None


def export_river_today(
    store: Store,
    thresholds: dict[str, Threshold],
    result: ValidationResult,
    *,
    climatology_path: Path | None = None,
    out_dir: Path = DEFAULT_SITE_DATA,
    now: datetime | None = None,
    today: date | None = None,
) -> ExportResult:
    """Validate-then-write. Raises :class:`ExportBlocked` if the tests failed.

    On a block, nothing on disk changes, so whatever is already published
    stays published - with its own last-update date on the page, so a reader
    can see the data has not moved.
    """
    if not result.ok:
        names = ", ".join(c.name for c in result.failures)
        raise ExportBlocked(
            f"{len(result.failures)} data test(s) failed ({names}); "
            "nothing was exported and the last good data stands"
        )

    payload = build_river_today(store, thresholds, result, now=now, today=today)

    # The finding, alongside the readings it came from. It travels in this
    # file rather than its own so the page that leads with it does not have
    # to wait on a second request to know what to say.
    from . import frequency as fq
    from . import verdict as vd
    from .normalize import Climatology

    clim = Climatology.load(climatology_path or Path("data/climatology_parana.json"))
    periods = fq.load()
    built = vd.build(store, clim, periods=periods, today=today)
    payload["verdict"] = vd.payload(
        built,
        track=_maybe_json(out_dir / TRACK),
        flooded=_maybe_json(out_dir / FLOODED),
        rarity=vd.rarest(built, periods, store),
    )

    # How rare each reading is, beside how high it is. A percentile says
    # where today sits among past 29ths of October; a return period says
    # how big the flood is. Both are wanted and they often disagree.
    if periods:
        ladder = {g["series_id"]: g for g in periods["gauges"]}
        for gauge in payload["gauges"]:
            row = ladder.get(gauge["series_id"])
            gauge["return_period"] = fq.return_period_of(
                periods, gauge["series_id"], gauge.get("value")
            )
            gauge["record_max"] = row["record_max"] if row else None
            gauge["record_max_year"] = row["record_max_year"] if row else None
        payload["frequency"] = {
            "method": periods["method"],
            "built_on": periods["built_on"],
            "min_years": periods["min_years"],
            "note": periods["note"],
        }

    path = out_dir / RIVER_TODAY
    write_json(path, payload)
    log.info("exported %d gauges to %s", len(payload["gauges"]), path)  # type: ignore[arg-type]
    return ExportResult(
        path=path,
        gauges=len(payload["gauges"]),  # type: ignore[arg-type]
        with_official_thresholds=len(thresholds),
        last_data_day=store.last_data_day(),
    )


def export_wave(
    store: Store,
    result: ValidationResult,
    *,
    chain_path: Path | None = None,
    climatology_path: Path | None = None,
    travel_times_path: Path | None = None,
    out_dir: Path = DEFAULT_SITE_DATA,
    window_days: int | None = None,
    now: datetime | None = None,
    today: date | None = None,
) -> ExportResult:
    """Validate-then-write the wave view, behind the same gate.

    Returns a result whose ``gauges`` counts the gauges on the chain, which
    is fewer than the river-today view: a gauge with no trustworthy river
    position cannot be placed on a distance axis.
    """
    # Imported here: the wave analysis pulls in scipy, and the river-today
    # export should not pay for that.
    from . import chain as ch
    from .lag import DEFAULT_TABLE, load_table
    from .normalize import Climatology
    from .wave import WINDOW_DAYS, analyse, build_payload

    if not result.ok:
        names = ", ".join(c.name for c in result.failures)
        raise ExportBlocked(
            f"{len(result.failures)} data test(s) failed ({names}); "
            "nothing was exported and the last good data stands"
        )

    nodes = ch.load_chain(chain_path or ch.DEFAULT_CHAIN)
    if not nodes:
        raise ExportBlocked(
            "no river chain, so nothing can be placed by distance; "
            "run `crecida chain --rederive`"
        )

    clim = Climatology.load(climatology_path or Path("data/climatology_parana.json"))
    # The frozen table, not a fresh measurement: the scheduled run's cache
    # holds about a year, which is plenty for the crests the plot shows and
    # nowhere near enough for a lag distribution.
    analysis = analyse(
        store,
        clim,
        nodes,
        window_days=window_days or WINDOW_DAYS,
        today=today,
        travel_times=load_table(travel_times_path or DEFAULT_TABLE),
    )
    payload = build_payload(analysis, now=now)
    path = out_dir / WAVE
    write_json(path, payload)
    log.info("exported %d chain gauges to %s", len(analysis.gauges), path)
    return ExportResult(
        path=path,
        gauges=len(analysis.gauges),
        with_official_thresholds=0,
        last_data_day=store.last_data_day(),
    )


def export_drivers(
    store: Store,
    result: ValidationResult,
    *,
    climatology_path: Path | None = None,
    oni_path: Path | None = None,
    snapshot_path: Path | None = None,
    rainfall_path: Path | None = None,
    out_dir: Path = DEFAULT_SITE_DATA,
    now: datetime | None = None,
    today: date | None = None,
) -> ExportResult:
    """Validate-then-write the upstream drivers, behind the same gate."""
    from .drivers import DEFAULT_ONI, build_payload, load_oni
    from .normalize import Climatology
    from .rainfall import DEFAULT_TABLE as RAIN_TABLE
    from .rainfall import load_table as load_rain
    from .thresholds import DEFAULT_SNAPSHOT, load_snapshot

    if not result.ok:
        names = ", ".join(c.name for c in result.failures)
        raise ExportBlocked(
            f"{len(result.failures)} data test(s) failed ({names}); "
            "nothing was exported and the last good data stands"
        )

    clim = Climatology.load(climatology_path or Path("data/climatology_parana.json"))
    payload = build_payload(
        store,
        clim,
        oni=load_oni(oni_path or DEFAULT_ONI),
        snapshot=load_snapshot(snapshot_path or DEFAULT_SNAPSHOT),
        rain=load_rain(rainfall_path or RAIN_TABLE),
        now=now,
        today=today,
    )
    path = out_dir / DRIVERS
    write_json(path, payload)
    log.info("exported %d driver gauges to %s", len(payload["gauges"]), path)
    return ExportResult(
        path=path,
        gauges=len(payload["gauges"]),  # type: ignore[arg-type]
        with_official_thresholds=0,
        last_data_day=store.last_data_day(),
    )


def export_river_stage(
    store: Store,
    result: ValidationResult,
    *,
    climatology_path: Path | None = None,
    out_dir: Path = DEFAULT_SITE_DATA,
    now: datetime | None = None,
    today: date | None = None,
) -> ExportResult:
    """Validate-then-write the daily river colour, behind the same gate.

    This is the one layer on the map that moves every day. The radar
    overlays are twelve-day composites that need Earth Engine, so they are
    rendered when a credential is present; this is built from the store on
    every run, which is why it is the map's default.
    """
    from .normalize import Climatology
    from . import riverstage as rs

    if not result.ok:
        names = ", ".join(c.name for c in result.failures)
        raise ExportBlocked(
            f"{len(result.failures)} data test(s) failed ({names}); "
            "nothing was exported and the last good data stands"
        )

    clim = Climatology.load(climatology_path or Path("data/climatology_parana.json"))
    payload = rs.build(store, clim, today=today)
    payload["generated_at"] = (now or datetime.now(DEFAULT_TZ)).isoformat(
        timespec="seconds"
    )
    if not payload["anchors"]:
        raise ExportBlocked(
            "no gauge on the river has a usable series, so the daily river "
            "colour would be blank"
        )
    path = out_dir / RIVER_STAGE
    write_json(path, payload)
    log.info(
        "exported %d anchors over %d days to %s",
        len(payload["anchors"]), len(payload["days"]), path,
    )
    return ExportResult(
        path=path,
        gauges=len(payload["anchors"]),
        with_official_thresholds=0,
        last_data_day=store.last_data_day(),
    )


def export_track(
    store: Store,
    result: ValidationResult,
    *,
    climatology_path: Path | None = None,
    skill_path: Path | None = None,
    out_dir: Path = DEFAULT_SITE_DATA,
    now: datetime | None = None,
    today: date | None = None,
) -> ExportResult:
    """Validate-then-write the arrival projections, behind the same gate.

    The measured accuracy travels with the projection, always. This is the
    one forward-looking thing on the site, and a projection published
    without its own track record invites the reader to supply a confidence
    nobody measured. On the record since 1998 the actual crest landed
    inside the published window about a quarter of the time.
    """
    from . import backtest as bt
    from . import track as tr
    from .normalize import Climatology

    if not result.ok:
        names = ", ".join(c.name for c in result.failures)
        raise ExportBlocked(
            f"{len(result.failures)} data test(s) failed ({names}); "
            "nothing was exported and the last good data stands"
        )

    clim = Climatology.load(climatology_path or Path("data/climatology_parana.json"))
    payload = tr.payload(tr.build(store, clim, today=today), today=today)
    payload["generated_at"] = (now or datetime.now(DEFAULT_TZ)).isoformat(
        timespec="seconds"
    )
    skill = bt.load(skill_path or bt.DEFAULT_OUT)
    if not skill:
        raise ExportBlocked(
            "no backtest to publish beside the projections; run "
            "`crecida backtest`. A projection without its measured accuracy "
            "is not publishable here"
        )
    payload["skill"] = skill
    path = out_dir / TRACK
    write_json(path, payload)
    log.info(
        "exported %d projections to %s", len(payload["projections"]), path
    )
    return ExportResult(
        path=path,
        gauges=len(payload["projections"]),
        with_official_thresholds=0,
        last_data_day=store.last_data_day(),
    )


def _write_csv(path: Path, header: list[str], rows) -> int:
    """Write a CSV atomically, the way the JSON writers do."""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
            count += 1
    os.replace(tmp, path)
    return count


def export_csv(
    store: Store,
    result: ValidationResult,
    *,
    climatology_path: Path | None = None,
    out_dir: Path = DEFAULT_SITE_DATA,
    window_days: int = 180,
    today: date | None = None,
) -> dict[str, int]:
    """The same measurements as the site, in the format people analyse in.

    The pages read JSON because a browser does. Anyone who wants to check
    the work, or use it in their own basin, wants columns - and asking a
    hydrologist to parse nested JSON to get a stage series is a way of not
    really publishing it.

    Three files, each the whole of one thing:

    - ``river_daily.csv`` - every gauge, every day, level and percentile.
    - ``flooded_area.csv`` - every stretch, every satellite pass.
    - ``travel_times.csv`` - every leg, with the spread and how many floods
      it was measured over.

    The stage file covers the published window rather than the full record.
    The record runs to 1884 and is 950,000 rows; that belongs in the
    repository's DuckDB store, not in a file a page links to.
    """
    from . import chain as ch
    from .lag import DEFAULT_TABLE as LAG_TABLE
    from .lag import load_table as load_lags
    from .flooded import DEFAULT_TABLE as FLOOD_TABLE
    from .flooded import load_table as load_flood
    from .normalize import Climatology

    if not result.ok:
        names = ", ".join(c.name for c in result.failures)
        raise ExportBlocked(
            f"{len(result.failures)} data test(s) failed ({names}); "
            "nothing was exported and the last good data stands"
        )

    today = today or datetime.now(DEFAULT_TZ).date()
    start = today - timedelta(days=window_days)
    clim = Climatology.load(climatology_path or Path("data/climatology_parana.json"))
    nodes = {n.series_id: n for n in ch.load_chain()}
    out = out_dir / CSV_DIR
    written: dict[str, int] = {}

    def river_rows():
        for gauge in roster.GAUGES:
            node = nodes.get(gauge.series_id)
            for value in store.series(gauge.series_id, since=start):
                pct = clim.percentile_of(gauge.series_id, value.day, value.value)
                yield [
                    value.day.isoformat(),
                    gauge.series_id,
                    gauge.name,
                    gauge.branch,
                    "" if node is None else round(node.river_km, 1),
                    round(value.value, 2),
                    "" if pct is None else round(pct, 1),
                    " ".join(sorted(gauge.flags)),
                ]

    written["river_daily.csv"] = _write_csv(
        out / "river_daily.csv",
        ["day", "series_id", "gauge", "branch", "river_km", "level_m",
         "percentile_for_day_of_year", "flags"],
        river_rows(),
    )

    flood = load_flood(FLOOD_TABLE)

    def flood_rows():
        for stretch in flood.get("stretches", []):
            # An uncalibrated stretch publishes no number here either. The
            # file would otherwise be the one place the gorge and the Delta
            # still quote an area.
            if stretch.get("calibrated") is False:
                continue
            for day, km2 in zip(stretch.get("days", []), stretch.get("km2", [])):
                yield [
                    day, stretch["key"], stretch["from_name"], stretch["to_name"],
                    round(km2, 1), stretch.get("baseline_km2"),
                    stretch.get("permanent_water_km2"), stretch.get("threshold_db"),
                ]

    written["flooded_area.csv"] = _write_csv(
        out / "flooded_area.csv",
        ["day", "stretch", "from_gauge", "to_gauge", "water_km2",
         "bajante_2021_km2", "jrc_permanent_km2", "threshold_db"],
        flood_rows(),
    )

    def lag_rows():
        for row in load_lags(LAG_TABLE):
            yield [
                row["from_name"], row["to_name"], row["km"],
                row.get("median_days"), row.get("p25_days"), row.get("p75_days"),
                row.get("correlation_days"), row.get("n"),
                row.get("resolution_limited"),
            ]

    written["travel_times.csv"] = _write_csv(
        out / "travel_times.csv",
        ["from_gauge", "to_gauge", "km", "median_days", "p25_days", "p75_days",
         "cross_correlation_days", "floods_matched", "resolution_limited"],
        lag_rows(),
    )

    log.info("csv: %s", ", ".join(f"{k} {v} rows" for k, v in written.items()))
    return written


def _stage_correlation(
    store: Store, raw: dict, key: str
) -> float | None:
    """Correlation between a stretch's water area and its downstream gauge.

    ``None`` where there are too few passes with a matching reading to say
    anything - which is different from zero, and is published as such.

    Computed from the raw table rather than the summary, so a stretch that
    publishes no area still publishes this. That is deliberate: it is the
    evidence for the refusal. The Delta's -0.44 is the reason it shows a
    picture and claims no number, and a reader should be able to see the
    number that decided it.
    """
    from . import flooded as fl

    corridor = next((c for c in fl.load_corridors() if c.key == key), None)
    source = next((s for s in raw.get("stretches", []) if s["key"] == key), None)
    if corridor is None or source is None or not source.get("km2"):
        return None
    levels = {v.day.isoformat(): v.value for v in store.series(corridor.to_id)}
    pairs = [
        (levels[day], km2)
        for day, km2 in zip(source["days"], source["km2"])
        if day in levels
    ]
    if len(pairs) < 8:
        return None
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy is a runtime dependency
        return None
    matrix = np.corrcoef([p[0] for p in pairs], [p[1] for p in pairs])
    value = float(matrix[0, 1])
    return None if value != value else round(value, 2)


def export_area_stage(
    result: ValidationResult,
    *,
    source_path: Path | None = None,
    out_dir: Path = DEFAULT_SITE_DATA,
) -> ExportResult | None:
    """Publish how well a river level predicts flooded area.

    Frozen input: it needs Earth Engine and ten years of satellite passes,
    so it is built by ``scripts/area_stage_history.py`` and committed, the
    way the travel times and the backtest are. Absent, the page simply does
    not show the section.
    """
    from . import areastage

    if not result.ok:
        names = ", ".join(c.name for c in result.failures)
        raise ExportBlocked(
            f"{len(result.failures)} data test(s) failed ({names}); "
            "nothing was exported and the last good data stands"
        )

    payload = areastage.build(areastage.load(source_path or areastage.DEFAULT_SOURCE))
    if not payload:
        log.info("no area-against-stage record; the section stays hidden")
        return None
    path = out_dir / AREA_STAGE
    write_json(path, payload)
    log.info(
        "exported %d area-against-stage observations (r=%s) to %s",
        payload["n"], payload["r"], path,
    )
    return ExportResult(
        path=path,
        gauges=payload["n"],
        with_official_thresholds=0,
        last_data_day=None,
    )


def export_flooded(
    store: Store,
    result: ValidationResult,
    *,
    flooded_path: Path | None = None,
    out_dir: Path = DEFAULT_SITE_DATA,
    now: datetime | None = None,
    today: date | None = None,
) -> ExportResult:
    """Validate-then-write the flooded-area view, behind the same gate.

    Like rainfall, the measurement itself is frozen: it needs Earth Engine.
    The gauge levels alongside it come from the store, so the page can put
    area and stage on the same dates - which is the comparison that makes
    either number mean anything.
    """
    from .flooded import DEFAULT_TABLE as FLOOD_TABLE
    from .flooded import load_table, stale_days, summarise

    if not result.ok:
        names = ", ".join(c.name for c in result.failures)
        raise ExportBlocked(
            f"{len(result.failures)} data test(s) failed ({names}); "
            "nothing was exported and the last good data stands"
        )

    raw = load_table(flooded_path or FLOOD_TABLE)
    stretches = summarise(raw) if raw else []

    # How well each stretch's measured area moves with the river, published
    # rather than left for a reader to assume.
    #
    # Calibration asks whether the threshold reproduces JRC over low water.
    # Every stretch here passed that, La Paz to Parana best of all at 1.2%.
    # It does not ask whether the series that follows carries a flood, and
    # on that stretch it does not: the area at stage 2.86 m and at 2.82 m
    # differs by 57%, which is scatter, and its true response is the
    # smallest on the river - 1.6 times the bajante against 3.2 at
    # Corrientes to Goya. Small signal, large noise, no correlation.
    #
    # The number goes on the page beside the area so that "this stretch
    # barely moved" can be read as "we cannot tell here" rather than as a
    # statement about the flood.
    for stretch in stretches:
        stretch["stage_correlation"] = _stage_correlation(store, raw, stretch["key"])

    # Attach the stage at each gauge on each pass date. Radar says how wide,
    # the gauge says how high, and neither is much use without the other.
    for stretch in stretches:
        levels: dict[str, list[float | None]] = {}
        for role in ("from", "to"):
            series_id = None
            for s in raw.get("stretches", []):
                if s["key"] == stretch["key"]:
                    series_id = s.get(role) or _corridor_gauge(stretch, role)
            if series_id is None:
                continue
            by_day = {v.day.isoformat(): round(v.value, 2) for v in store.series(series_id)}
            levels[role] = [by_day.get(d) for d in stretch.get("days", [])]
        stretch["levels"] = levels

    payload = {
        "schema": 1,
        "generated_at": (now or datetime.now(DEFAULT_TZ)).isoformat(timespec="seconds"),
        "source": raw.get("source"),
        "method": raw.get("method"),
        "note": raw.get("note"),
        "fetched_on": raw.get("fetched_on"),
        "stale_days": stale_days(raw, today) if raw else None,
        "window": raw.get("window"),
        "stretches": stretches,
    }
    path = out_dir / FLOODED
    write_json(path, payload)
    log.info("exported %d stretches to %s", len(stretches), path)
    return ExportResult(
        path=path,
        gauges=len(stretches),
        with_official_thresholds=0,
        last_data_day=store.last_data_day(),
    )


def _corridor_gauge(stretch: dict, role: str) -> str | None:
    """The series id behind a stretch end, read from the frozen corridors."""
    from .flooded import load_corridors

    for corridor in load_corridors():
        if corridor.key == stretch.get("key"):
            return corridor.from_id if role == "from" else corridor.to_id
    return None


#: Static reference layers for the map. They change only when the gauge
#: roster or the chain does, so they are built once and published, not
#: regenerated nightly.
MAP_LAYERS = ("gauges.geojson", "departamentos.geojson")


def map_layers_present(out_dir: Path = DEFAULT_SITE_DATA) -> list[str]:
    """Which map layers are missing from the published directory.

    The map fetches these by relative path from the site, so one written to
    the repository's data/ directory instead is simply never found -- and
    the map renders without gauges or departamento lines while otherwise
    looking entirely healthy.
    """
    return [name for name in MAP_LAYERS if not (out_dir / name).exists()]
