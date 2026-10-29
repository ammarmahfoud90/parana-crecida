"""The DuckDB store: the clean layer the site is exported from.

Two layers on purpose. The JSON cache under ``cache/`` stays the raw layer -
one file per series, the format rios-argentina already writes, cheap to top
up and safe to delete. DuckDB is the clean layer: typed, keyed, queryable,
and the only thing the exporter reads.

Keeping them separate means a bad load can always be rebuilt from the raw
files without going back to the API, and the API is a public agency server
we should ask as little as possible.

The ``(series_id, day)`` primary key makes duplicate days structurally
impossible rather than merely tested for. The validator still checks, since
a constraint only guards what reaches the table.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import TracebackType

import duckdb
import pyarrow as pa
from ina_a5 import SeriesCache
from ina_a5.observations import DEFAULT_TZ

from . import gauges as roster
from .thresholds import Threshold

log = logging.getLogger(__name__)

DEFAULT_DB = Path("data/crecida.duckdb")

SCHEMA = """
CREATE TABLE IF NOT EXISTS gauge (
    series_id   VARCHAR PRIMARY KEY,
    name        VARCHAR NOT NULL,
    display     VARCHAR NOT NULL,
    branch      VARCHAR NOT NULL,
    tier        INTEGER NOT NULL,
    flags       VARCHAR NOT NULL,
    note        VARCHAR NOT NULL,
    river_order INTEGER
);

CREATE TABLE IF NOT EXISTS observation (
    series_id VARCHAR NOT NULL,
    day       DATE    NOT NULL,
    value     DOUBLE  NOT NULL,
    PRIMARY KEY (series_id, day)
);

CREATE TABLE IF NOT EXISTS threshold (
    series_id   VARCHAR PRIMARY KEY,
    unit        VARCHAR NOT NULL,
    alert       DOUBLE,
    evacuation  DOUBLE,
    ina_station VARCHAR,
    source_date DATE
);

CREATE TABLE IF NOT EXISTS load_run (
    run_id     BIGINT PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    series     INTEGER NOT NULL,
    rows       INTEGER NOT NULL,
    note       VARCHAR
);
"""


@dataclass(frozen=True, slots=True)
class GaugeToday:
    """One gauge's current state, as the river-today view needs it."""

    series_id: str
    name: str
    branch: str
    tier: int
    flags: tuple[str, ...]
    river_order: int
    unit: str
    day: date | None
    value: float | None
    previous_day: date | None
    previous_value: float | None
    alert: float | None
    evacuation: float | None

    @property
    def change(self) -> float | None:
        """Change since the previous reading, or ``None``.

        ``None`` rather than zero when there is no previous day: a gap in
        the feed is not the same as a river that did not move.
        """
        if self.value is None or self.previous_value is None:
            return None
        return self.value - self.previous_value

    @property
    def change_days(self) -> int | None:
        if self.day is None or self.previous_day is None:
            return None
        return (self.day - self.previous_day).days


class Store(AbstractContextManager["Store"]):
    """A DuckDB database of gauges, observations and thresholds.

    >>> with Store(":memory:") as store:          # doctest: +SKIP
    ...     store.load_gauges()
    ...     store.load_observations(SeriesCache("cache"))
    """

    def __init__(self, path: Path | str = DEFAULT_DB, *, read_only: bool = False) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(self.path, read_only=read_only)
        if not read_only:
            self.migrate()

    def migrate(self) -> None:
        self.con.execute(SCHEMA)
        # The store is a derived artifact, rebuilt from the raw cache in
        # seconds, so migrations only need to keep an existing file usable
        # rather than preserve anything.
        self.con.execute(
            "ALTER TABLE gauge ADD COLUMN IF NOT EXISTS display VARCHAR DEFAULT ''"
        )

    def close(self) -> None:
        self.con.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- loading -----------------------------------------------------------

    def load_gauges(self, gauges: Sequence[roster.Gauge] | None = None) -> int:
        """Replace the gauge table from the roster, carrying river order."""
        gauges = list(gauges if gauges is not None else roster.GAUGES)
        order = {g.series_id: i for i, g in enumerate(roster.main_stem_sequence())}
        self.con.execute("DELETE FROM gauge")
        self.con.executemany(
            "INSERT INTO gauge (series_id, name, display, branch, tier, flags, note,"
            " river_order) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    g.series_id,
                    g.name,
                    g.label,
                    g.branch,
                    g.tier,
                    ",".join(sorted(g.flags)),
                    g.note,
                    order.get(g.series_id),
                )
                for g in gauges
            ],
        )
        return len(gauges)

    def load_observations(
        self,
        cache: SeriesCache,
        series_ids: Iterable[str] | None = None,
        *,
        note: str = "",
    ) -> tuple[int, int]:
        """Load the raw JSON cache into the store. Returns (series, rows).

        Idempotent: a series is replaced wholesale, so re-running after a
        top-up cannot double-count and cannot leave a stale tail behind.
        """
        wanted = list(series_ids) if series_ids is not None else roster.series_ids()
        started = datetime.now(DEFAULT_TZ)

        # Built as three column arrays and inserted in one statement. A
        # per-row executemany takes minutes for the ~900k rows a century of
        # daily stage at 40 gauges comes to; this takes well under a second.
        ids: list[str] = []
        days: list[date] = []
        values: list[float] = []
        loaded_series = 0

        for series_id in wanted:
            series = cache.read(series_id)
            if not series:
                log.warning("series %s has nothing cached; skipping", series_id)
                continue
            for v in series:
                ids.append(series_id)
                days.append(v.day)
                values.append(float(v.value))
            loaded_series += 1

        total_rows = len(ids)
        if ids:
            self.con.execute(
                "DELETE FROM observation WHERE series_id IN "
                f"({','.join('?' * loaded_series)})",
                sorted({*ids}),
            )
            incoming = pa.table(
                {
                    "series_id": pa.array(ids, pa.string()),
                    "day": pa.array(days, pa.date32()),
                    "value": pa.array(values, pa.float64()),
                }
            )
            self.con.register("incoming_observation", incoming)
            try:
                self.con.execute(
                    "INSERT INTO observation (series_id, day, value)"
                    " SELECT series_id, day, value FROM incoming_observation"
                )
            finally:
                self.con.unregister("incoming_observation")

        run_id = int(started.timestamp() * 1000)
        self.con.execute(
            "INSERT OR REPLACE INTO load_run (run_id, started_at, series, rows, note)"
            " VALUES (?, ?, ?, ?, ?)",
            [run_id, started, loaded_series, total_rows, note],
        )
        log.info("loaded %d series, %d rows", loaded_series, total_rows)
        return loaded_series, total_rows

    def load_thresholds(self, thresholds: dict[str, Threshold]) -> int:
        """Replace the threshold table. Only official values ever get here."""
        self.con.execute("DELETE FROM threshold")
        self.con.executemany(
            "INSERT INTO threshold (series_id, unit, alert, evacuation, ina_station,"
            " source_date) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (t.series_id, t.unit, t.alert, t.evacuation, t.ina_station, t.source_date)
                for t in thresholds.values()
            ],
        )
        return len(thresholds)

    # -- reading -----------------------------------------------------------

    def row_count(self) -> int:
        return int(self.con.execute("SELECT count(*) FROM observation").fetchone()[0])

    def last_data_day(self) -> date | None:
        row = self.con.execute("SELECT max(day) FROM observation").fetchone()
        return row[0] if row and row[0] else None

    def series_present(self) -> list[str]:
        return [
            r[0]
            for r in self.con.execute(
                "SELECT DISTINCT series_id FROM observation ORDER BY series_id"
            ).fetchall()
        ]

    def river_today(self) -> list[GaugeToday]:
        """The latest two readings per gauge, with thresholds, in river order.

        The previous reading is the one immediately before the latest in the
        record, not "yesterday": gauges have gaps, and labelling a two-day
        gap as a one-day change would overstate how fast the river moved.
        """
        rows = self.con.execute(
            """
            WITH ranked AS (
                SELECT series_id, day, value,
                       row_number() OVER (PARTITION BY series_id ORDER BY day DESC) AS rn
                FROM observation
            ),
            latest AS (SELECT * FROM ranked WHERE rn = 1),
            previous AS (SELECT * FROM ranked WHERE rn = 2)
            SELECT g.series_id, coalesce(nullif(g.display, ''), g.name),
                   g.branch, g.tier, g.flags, g.river_order,
                   l.day, l.value, p.day, p.value,
                   t.unit, t.alert, t.evacuation
            FROM gauge g
            LEFT JOIN latest   l ON l.series_id = g.series_id
            LEFT JOIN previous p ON p.series_id = g.series_id
            LEFT JOIN threshold t ON t.series_id = g.series_id
            WHERE g.river_order IS NOT NULL
            ORDER BY g.river_order
            """
        ).fetchall()
        return [
            GaugeToday(
                series_id=r[0],
                name=r[1],
                branch=r[2],
                tier=int(r[3]),
                flags=tuple(f for f in (r[4] or "").split(",") if f),
                river_order=int(r[5]),
                unit=r[10] or "m",
                day=r[6],
                value=None if r[7] is None else float(r[7]),
                previous_day=r[8],
                previous_value=None if r[9] is None else float(r[9]),
                alert=None if r[11] is None else float(r[11]),
                evacuation=None if r[12] is None else float(r[12]),
            )
            for r in rows
        ]

    def series_range(self, series_id: str) -> tuple[date, date, float, float] | None:
        """``(first, last, min, max)`` for one series, or ``None`` if empty."""
        row = self.con.execute(
            "SELECT min(day), max(day), min(value), max(value) FROM observation"
            " WHERE series_id = ?",
            [series_id],
        ).fetchone()
        if not row or row[0] is None:
            return None
        return row[0], row[1], float(row[2]), float(row[3])
