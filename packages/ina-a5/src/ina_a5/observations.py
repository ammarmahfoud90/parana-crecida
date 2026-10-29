"""Observation download: ``getObservaciones``.

Carries over the defensive response unwrapping and the history-truncation
heuristic from ``build_climatology.py``, and changes two things the flood-wave
work depends on:

* **Timezones are explicit.** The original truncated timestamps with
  ``str(ts)[:10]``, so the calendar day a reading landed on was whatever the
  server's string happened to start with. Here timestamps are parsed and
  converted to a named zone, Buenos Aires by default.
* **Today's partial day is dropped by default.** A day that is still filling
  up averages to a value the river never had, which at a wave front reads as
  a spurious rise or fall. This is the "last-point artefact" the original
  ``check_recent.py`` could not rule out.

Series of different variable families are never merged here: every function
works on one ``series_id``, so an instantaneous series can never be averaged
into a daily-mean one.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .client import A5Client
from .errors import A5Truncated
from .models import DailyValue

log = logging.getLogger(__name__)

OBS_PATH = "getObservaciones"

#: Gauge timestamps are Argentine local time.
DEFAULT_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

#: Suspiciously round row counts: the server appears to cap long requests,
#: but no limit is documented, so treat a round count as probably truncated.
SUSPECT_ROW_COUNTS = frozenset({1000, 5000, 10000, 20000})
#: If the earliest row is this much later than asked for, assume truncation.
TRUNCATION_SLACK_DAYS = 400
CHUNK_YEARS = 5

_TIME_KEYS = ("timestart", "fecha", "time", "date", "timeend")
_VALUE_KEYS = ("valor", "value", "val")
_CONTAINER_KEYS = ("observaciones", "data", "rows", "result", "series", "values")


def extract_observations(payload: Any) -> list[dict[str, Any]]:
    """Dig the observation rows out of whatever shape came back.

    The endpoint returns a bare list, or a list wrapped under any of several
    keys, or that nested one level deeper again.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        if payload and isinstance(payload[0], dict) and any(
            k in payload[0] for k in ("timestart", "valor", "fecha", "timeend")
        ):
            return payload
        for item in payload:
            found = extract_observations(item)
            if found:
                return found
        return []
    if isinstance(payload, dict):
        for key in _CONTAINER_KEYS:
            if key in payload:
                found = extract_observations(payload[key])
                if found:
                    return found
    return []


def parse_timestamp(raw: Any, tz: ZoneInfo = DEFAULT_TZ) -> datetime | None:
    """An a5 timestamp as an aware datetime in ``tz``.

    A naive timestamp is read as already being in ``tz`` rather than UTC:
    that is how the catalogue's own naive date ranges behave, and assuming
    UTC would shift readings onto the wrong day for three hours each night.
    """
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    text = raw.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def rows_to_daily(
    rows: list[dict[str, Any]],
    *,
    tz: ZoneInfo = DEFAULT_TZ,
    skip_days: frozenset[date] | set[date] = frozenset(),
) -> list[DailyValue]:
    """Raw rows to one mean value per calendar day, ascending.

    Rows missing a timestamp or a value are dropped; sub-daily rows are
    averaged, and ``n_raw`` records how many went into each day.
    """
    buckets: dict[date, list[float]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = None
        for key in _TIME_KEYS:
            stamp = parse_timestamp(row.get(key), tz)
            if stamp is not None:
                break
        if stamp is None:
            continue
        value = None
        for key in _VALUE_KEYS:
            if row.get(key) is not None:
                try:
                    value = float(row[key])
                except (TypeError, ValueError):
                    value = None
                break
        if value is None:
            continue
        day = stamp.date()
        if day in skip_days:
            continue
        buckets[day].append(value)
    return [
        DailyValue(day=day, value=statistics.fmean(vals), n_raw=len(vals))
        for day, vals in sorted(buckets.items())
    ]


def fetch_raw(
    client: A5Client, series_id: str | int, start: date, end: date
) -> list[dict[str, Any]]:
    """One ``getObservaciones`` call. Raises on any HTTP or transport failure."""
    payload = client.get_json(
        OBS_PATH,
        params={
            "tipo": "puntual",
            "series_id": series_id,
            "timestart": f"{start.isoformat()}T00:00:00Z",
            "timeend": f"{end.isoformat()}T23:59:59Z",
        },
    )
    return extract_observations(payload)


def fetch_daily(
    client: A5Client,
    series_id: str | int,
    start: date,
    end: date,
    *,
    tz: ZoneInfo = DEFAULT_TZ,
    drop_partial_today: bool = True,
    today: date | None = None,
) -> list[DailyValue]:
    """Daily means for one series over ``[start, end]``.

    An empty list means the server returned no rows for that window, not
    that the request failed - failures raise.
    """
    rows = fetch_raw(client, series_id, start, end)
    skip: set[date] = set()
    if drop_partial_today:
        skip.add(today or datetime.now(tz).date())
    return rows_to_daily(rows, tz=tz, skip_days=skip)


def _looks_truncated(
    rows: list[dict[str, Any]], daily: list[DailyValue], start: date, end: date
) -> bool:
    if not daily:
        # Nothing at all over a long window is more likely a cap than a gauge
        # with no history whatsoever.
        return (end - start).days > TRUNCATION_SLACK_DAYS
    if len(rows) in SUSPECT_ROW_COUNTS:
        return True
    return (daily[0].day - start).days > TRUNCATION_SLACK_DAYS


def fetch_history(
    client: A5Client,
    series_id: str | int,
    start: date,
    end: date,
    *,
    tz: ZoneInfo = DEFAULT_TZ,
    chunk_years: int = CHUNK_YEARS,
    drop_partial_today: bool = True,
    today: date | None = None,
    strict: bool = False,
) -> list[DailyValue]:
    """Full history for one series: one big request, verified, chunked if short.

    The single request is tried first because it is one round trip for a
    century of data. If the result looks capped it is re-fetched in
    ``chunk_years`` windows. With ``strict``, a result that still looks
    truncated after chunking raises :class:`A5Truncated` instead of being
    returned quietly.
    """
    skip: set[date] = set()
    if drop_partial_today:
        skip.add(today or datetime.now(tz).date())

    rows = fetch_raw(client, series_id, start, end)
    daily = rows_to_daily(rows, tz=tz, skip_days=skip)

    if not _looks_truncated(rows, daily, start, end):
        return daily

    log.info(
        "series %s: single request looked truncated (%d rows) - chunking",
        series_id, len(rows),
    )
    chunked: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=365 * chunk_years), end)
        chunked.extend(fetch_raw(client, series_id, cursor, stop))
        cursor = stop + timedelta(days=1)

    daily = rows_to_daily(chunked, tz=tz, skip_days=skip)
    if strict and _looks_truncated(chunked, daily, start, end):
        raise A5Truncated(
            f"series {series_id}: still looks truncated after chunking "
            f"({len(chunked)} rows, {len(daily)} days)"
        )
    return daily
