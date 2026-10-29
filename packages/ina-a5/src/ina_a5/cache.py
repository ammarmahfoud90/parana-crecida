"""On-disk cache of daily series.

The file format is deliberately unchanged from rios-argentina - one JSON file
per series holding ``[["YYYY-MM-DD", value], ...]`` - so its existing 95 MB
``cache/`` and committed ``cache_recent/`` are readable here with no
migration step.

Two fixes over the original:

* **The cache root is a parameter, not a module global.** ``build_daily.py``
  hardcoded ``CACHE_DIR = "cache"`` and so never looked in ``cache_recent/``:
  a clean CI checkout without the Actions cache silently started every series
  from one year of data. :class:`SeriesCache` resolves reads across an
  ordered list of roots.
* **Trimming is a method, not a script** that deletes and recreates the
  directory on every run.

``n_raw`` is not persisted, because the existing files have no room for it;
values read back from disk report ``n_raw=1``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import DailyValue

log = logging.getLogger(__name__)

#: Re-ask for this much overlap when topping up, so late-arriving
#: corrections to already-cached days are picked up.
BACKFILL_DAYS = 10
#: How far back to reach for a series with no cache at all.
COLD_START_DAYS = 365


def _parse_rows(raw: object) -> list[DailyValue]:
    out: list[DailyValue] = []
    if not isinstance(raw, list):
        return out
    for row in raw:
        try:
            day_text, value = row[0], row[1]
            out.append(
                DailyValue(
                    day=datetime.strptime(str(day_text), "%Y-%m-%d").date(),
                    value=float(value),
                )
            )
        except (ValueError, TypeError, IndexError, KeyError):
            continue  # a corrupt row is skipped, not fatal
    out.sort(key=lambda v: v.day)
    return out


class SeriesCache:
    """Daily series on disk, across one or more roots.

    >>> cache = SeriesCache(["cache", "cache_recent"])     # doctest: +SKIP
    >>> values = cache.read(26261)                         # Corrientes
    """

    def __init__(
        self,
        roots: str | Path | Sequence[str | Path],
        *,
        write_root: str | Path | None = None,
    ) -> None:
        if isinstance(roots, (str, Path)):
            roots = [roots]
        self.roots: list[Path] = [Path(r) for r in roots]
        if not self.roots:
            raise ValueError("SeriesCache needs at least one root")
        self.write_root = Path(write_root) if write_root else self.roots[0]

    # -- locating ----------------------------------------------------------

    def path_for(self, series_id: str | int) -> Path:
        """Where a write for ``series_id`` goes."""
        return self.write_root / f"{series_id}.json"

    def locate(self, series_id: str | int) -> Path | None:
        """First root that actually has this series, or ``None``."""
        name = f"{series_id}.json"
        for root in self.roots:
            candidate = root / name
            if candidate.exists():
                return candidate
        return None

    def series_ids(self) -> list[str]:
        """Every series present in any root.

        Files whose name starts with an underscore are metadata, not series
        (``_catalogue.json``), and are skipped.
        """
        found: set[str] = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in root.glob("*.json"):
                if not path.stem.startswith("_"):
                    found.add(path.stem)
        return sorted(found)

    # -- reading and writing ----------------------------------------------

    def read(self, series_id: str | int) -> list[DailyValue]:
        """Cached values, ascending. Empty when absent or unreadable."""
        path = self.locate(series_id)
        if path is None:
            return []
        try:
            with path.open(encoding="utf-8") as fh:
                return _parse_rows(json.load(fh))
        except (ValueError, OSError) as exc:
            log.warning("unreadable cache file %s: %s", path, exc)
            return []

    def write(self, series_id: str | int, values: Iterable[DailyValue]) -> Path:
        """Replace the cached series. Written compactly, as before."""
        rows = [[v.day.isoformat(), v.value] for v in sorted(values, key=lambda v: v.day)]
        self.write_root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(series_id)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(rows, fh, separators=(",", ":"))
        os.replace(tmp, path)  # never leave a half-written cache file
        return path

    def last_day(self, series_id: str | int) -> date | None:
        values = self.read(series_id)
        return values[-1].day if values else None

    # -- updating ----------------------------------------------------------

    def top_up_window(
        self,
        series_id: str | int,
        today: date,
        *,
        backfill_days: int = BACKFILL_DAYS,
        cold_start_days: int = COLD_START_DAYS,
    ) -> tuple[date, date]:
        """The ``[start, end]`` to request to bring this series up to date."""
        last = self.last_day(series_id)
        if last is None:
            return today - timedelta(days=cold_start_days), today
        return last - timedelta(days=backfill_days), today

    def merge(
        self, series_id: str | int, fresh: Iterable[DailyValue]
    ) -> list[DailyValue]:
        """Merge ``fresh`` over the cached series without writing.

        Fresh values win for days present in both, which is what makes the
        backfill overlap useful: a revised reading replaces the old one.
        """
        by_day = {v.day: v for v in self.read(series_id)}
        by_day.update({v.day: v for v in fresh})
        return [by_day[day] for day in sorted(by_day)]

    def update(
        self, series_id: str | int, fresh: Iterable[DailyValue]
    ) -> list[DailyValue]:
        """:meth:`merge`, then write. Returns the merged series."""
        merged = self.merge(series_id, fresh)
        if merged:
            self.write(series_id, merged)
        return merged

    # -- maintenance -------------------------------------------------------

    def trim(
        self,
        *,
        years: float,
        today: date,
        keep: Iterable[str | int] | None = None,
        dest: str | Path | None = None,
    ) -> dict[str, int]:
        """Write a shortened copy of the cache, for committing to git.

        Replaces ``trim_cache.py``. Keeps only days on or after
        ``today - years``, and only the series in ``keep`` when given.
        Returns ``{series_id: days_kept}``.
        """
        cutoff = today - timedelta(days=int(years * 365.25))
        target = SeriesCache(dest) if dest else self
        wanted = {str(k) for k in keep} if keep is not None else None

        written: dict[str, int] = {}
        for series_id in self.series_ids():
            if wanted is not None and series_id not in wanted:
                continue
            kept = [v for v in self.read(series_id) if v.day >= cutoff]
            if kept:
                target.write(series_id, kept)
                written[series_id] = len(kept)
        log.info("trimmed %d series to %.1f years", len(written), years)
        return written
