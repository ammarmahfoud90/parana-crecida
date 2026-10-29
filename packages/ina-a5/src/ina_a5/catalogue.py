"""The a5 series catalogue: ``obs/puntual/series``.

Consolidates the four near-copies of catalogue paging that grew across the
rios-argentina scripts. Two quirks of the endpoint are load-bearing here and
must not be "simplified" away:

* **The unparameterised call silently caps at 5000 features.** You have to
  page with ``limit``/``offset`` to see the whole thing (~9.5k series).
* **Pages overlap.** An offset-4000 page of 1000 rows was once observed to
  contain only 80 ids not already seen. So "a short page means the end" is
  wrong on its own; the stop condition is *no new ids*.

``date_range`` on a feature is trustworthy where present - a 60-series sample
matched per-series probing at both ends - but only about 70% of series carry
one.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from .client import A5Client
from .models import Series, var_family

log = logging.getLogger(__name__)

CATALOGUE_PATH = "obs/puntual/series"
PAGE_SIZE = 1000
#: Safety rail, well above the ~9.5k series the catalogue actually holds.
HARD_STOP = 60_000

#: Argentina and a margin. Rejects the (0, 0) rows the catalogue contains.
AR_BBOX = (-74.0, -56.0, -52.0, -20.0)


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested properties to dotted keys.

    a5 moves fields between the top level and nested objects between
    endpoints, so every read goes through this and :func:`pick`.
    """
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}{k}"
            if isinstance(v, (dict, list)):
                out.update(flatten(v, f"{key}."))
            else:
                out[key] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            out.update(flatten(v, f"{prefix}{i}."))
    return out


def pick(props: dict[str, Any], *candidates: str, default: Any = "") -> Any:
    """First non-empty value among ``candidates``, matching on key suffix."""
    for cand in candidates:
        for key, value in props.items():
            if (key == cand or key.endswith("." + cand)) and value not in (None, ""):
                return value
    return default


def as_date(value: Any) -> date | None:
    """Leading ``YYYY-MM-DD`` of a catalogue date string, or ``None``.

    Catalogue timestamps are naive (``2001-05-01T09:00:00``). Only the
    calendar day is used, so there is nothing to convert.
    """
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_feature(feature: dict[str, Any]) -> Series:
    """One GeoJSON catalogue feature to a :class:`Series`."""
    props = flatten(feature.get("properties", feature))
    coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
    try:
        lon: float | None = float(coords[0])
        lat: float | None = float(coords[1])
    except (TypeError, ValueError, IndexError):
        lon = lat = None
    return Series(
        series_id=str(pick(props, "id", "series_id")),
        station_id=str(pick(props, "estacion_id", "unid")),
        station=str(pick(props, "nombre", "estacion", "nombre_estacion")),
        river=str(pick(props, "rio", "curso", "nombre_rio")),
        var_id=_as_int(pick(props, "var_id", default=None)),
        network=str(pick(props, "red", "red_nombre", "fuente")),
        lon=lon,
        lat=lat,
        cat_start=as_date(
            pick(props, "date_range.timestart", "timestart", "fecha_inicio", "start_date")
        ),
        cat_end=as_date(
            pick(props, "date_range.timeend", "timeend", "fecha_fin", "end_date")
        ),
    )


def coords_ok(series: Series, bbox: tuple[float, float, float, float] = AR_BBOX) -> bool:
    """True when the coordinates are present, non-null-island and in ``bbox``."""
    if series.lon is None or series.lat is None:
        return False
    if abs(series.lon) < 0.01 and abs(series.lat) < 0.01:
        return False
    return bbox[0] <= series.lon <= bbox[2] and bbox[1] <= series.lat <= bbox[3]


def iter_features(
    client: A5Client,
    *,
    page_size: int = PAGE_SIZE,
    hard_stop: int = HARD_STOP,
) -> Iterator[dict[str, Any]]:
    """Page the catalogue, yielding each feature once.

    Stops on an empty page, a short page, or a page with no ids not already
    seen - the last of those is the one that matters, because the server
    sometimes serves overlapping windows.
    """
    seen: set[str] = set()
    offset = 0
    while offset < hard_stop:
        body = client.get_json(
            CATALOGUE_PATH,
            params={"format": "geojson", "limit": page_size, "offset": offset},
        )
        page = (body or {}).get("features") or []
        if not page:
            break
        fresh = 0
        for feature in page:
            series_id = str(parse_feature(feature).series_id)
            if series_id and series_id not in seen:
                seen.add(series_id)
                fresh += 1
                yield feature
        log.debug("catalogue offset %d: %d returned, %d new", offset, len(page), fresh)
        if fresh == 0:
            log.info("catalogue offset %d returned no new ids; stopping", offset)
            break
        if len(page) < page_size:
            break
        offset += page_size


def fetch_catalogue(
    client: A5Client,
    *,
    hydro_only: bool = True,
    in_bbox: bool = True,
    page_size: int = PAGE_SIZE,
) -> list[Series]:
    """The full catalogue as :class:`Series` records.

    ``hydro_only`` keeps only level and flow series - the catalogue also
    carries rainfall, temperature and a long tail of other variables.
    """
    out: list[Series] = []
    for feature in iter_features(client, page_size=page_size):
        series = parse_feature(feature)
        if hydro_only and var_family(series.var_id) is None:
            continue
        if in_bbox and not coords_ok(series):
            continue
        out.append(series)
    log.info("catalogue: %d series kept", len(out))
    return out


def live_series(
    catalogue: list[Series], *, today: date, max_age_days: int = 7
) -> list[Series]:
    """Series whose catalogue ``cat_end`` is recent enough to count as live.

    Filtering locally rather than with the endpoint's ``timestart``/
    ``timeend`` params, which the server has been seen to ignore.
    """
    return [
        s
        for s in catalogue
        if s.cat_end is not None and (today - s.cat_end).days <= max_age_days
    ]
