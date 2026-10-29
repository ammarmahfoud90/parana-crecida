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
