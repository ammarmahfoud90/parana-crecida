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
from datetime import date, datetime
from pathlib import Path

from ina_a5.observations import DEFAULT_TZ

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


def export_river_today(
    store: Store,
    thresholds: dict[str, Threshold],
    result: ValidationResult,
    *,
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
