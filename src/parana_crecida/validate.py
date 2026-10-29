"""Data tests that run before anything is exported.

A failed error-level check stops the export. Nothing is written, so the
previously exported JSON - and therefore the published site - stays exactly
as it was. A bad day from INA never reaches the public page; it just means
the page keeps saying what it said yesterday, with its own last-update date
visible.

Warnings are recorded and do not block. The distinction matters most for
staleness: if INA is down for three days, the honest thing is to keep
showing the last good reading *with its date*, not to take the site down.

Checks deliberately do not include "the level looks like a flood". Floods
are the subject, not an error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from .store import Store

log = logging.getLogger(__name__)

ERROR = "error"
WARN = "warn"

#: Plausible bounds per unit. These cannot be tight, because every gauge has
#: its own zero: Puerto Iguazu reads about 29 m where San Fernando reads
#: about 1 m. The job here is to catch sentinels (-9999), unit mix-ups and
#: corruption, not to second-guess the river. Per-gauge plausibility is the
#: separate record-range check below.
SANE_RANGES: dict[str, tuple[float, float]] = {
    "m": (-10.0, 60.0),
    "m3/s": (0.0, 200_000.0),
}

#: Above this share of a series being out of range, the series itself is
#: wrong rather than merely carrying a bad reading, and the export is blocked.
OUT_OF_RANGE_SHARE = 1.0

#: Warn if the newest reading anywhere is older than this.
FRESH_DAYS = 7
#: Warn if the latest reading sits this many IQRs outside the gauge's record.
RECORD_MARGIN = 3.0


@dataclass(frozen=True, slots=True)
class Check:
    """One data test and what it found."""

    name: str
    severity: str
    passed: bool
    detail: str
    offenders: tuple[str, ...] = ()

    @property
    def blocking(self) -> bool:
        return not self.passed and self.severity == ERROR


@dataclass
class ValidationResult:
    """Every check from one validation pass."""

    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing error-level failed, so exporting is allowed."""
        return not any(c.blocking for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.blocking]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == WARN]

    def lines(self) -> list[str]:
        out = []
        for check in self.checks:
            mark = "ok  " if check.passed else ("FAIL" if check.severity == ERROR else "warn")
            out.append(f"  [{mark}] {check.name}: {check.detail}")
        return out


def _unit_expr() -> str:
    """SQL for a gauge's unit, since the observation table does not store it."""
    return "CASE WHEN g.flags LIKE '%flow%' THEN 'm3/s' ELSE 'm' END"


def validate(
    store: Store,
    *,
    today: date | None = None,
    fresh_days: int = FRESH_DAYS,
    required_tier: int = 1,
) -> ValidationResult:
    """Run every data test against the store."""
    today = today or date.today()
    result = ValidationResult()
    con = store.con

    # -- there is data at all ---------------------------------------------
    rows = store.row_count()
    result.checks.append(
        Check(
            "rows_present",
            ERROR,
            rows > 0,
            f"{rows} observation rows",
        )
    )
    if rows == 0:
        # Every other check would pass vacuously on an empty table, which
        # would be the most dangerous possible green light.
        return result

    # -- no nulls ----------------------------------------------------------
    nulls = con.execute(
        "SELECT count(*) FROM observation"
        " WHERE series_id IS NULL OR day IS NULL OR value IS NULL"
    ).fetchone()[0]
    result.checks.append(
        Check("no_null_fields", ERROR, nulls == 0, f"{nulls} rows with a null field")
    )

    # NaN and infinity survive a DOUBLE column and a NOT NULL constraint,
    # and would serialise into the site JSON as invalid values.
    nonfinite = con.execute(
        "SELECT count(*) FROM observation WHERE isnan(value) OR isinf(value)"
    ).fetchone()[0]
    result.checks.append(
        Check(
            "values_are_finite",
            ERROR,
            nonfinite == 0,
            f"{nonfinite} rows that are NaN or infinite",
        )
    )

    # -- no duplicate days per gauge --------------------------------------
    dupes = con.execute(
        "SELECT series_id, day, count(*) AS n FROM observation"
        " GROUP BY series_id, day HAVING n > 1 ORDER BY n DESC LIMIT 10"
    ).fetchall()
    result.checks.append(
        Check(
            "no_duplicate_days",
            ERROR,
            not dupes,
            "no gauge has two rows for one day"
            if not dupes
            else f"{len(dupes)} duplicated (gauge, day) pairs",
            tuple(f"{r[0]}@{r[1]}" for r in dupes),
        )
    )

    # -- no future days ----------------------------------------------------
    # A reading dated tomorrow means a timezone bug, not a fast river.
    future = con.execute(
        "SELECT series_id, max(day) FROM observation WHERE day > ?"
        " GROUP BY series_id ORDER BY 2 DESC LIMIT 10",
        [today],
    ).fetchall()
    result.checks.append(
        Check(
            "no_future_days",
            ERROR,
            not future,
            f"newest reading is not after {today}"
            if not future
            else f"{len(future)} gauges have readings dated after {today}",
            tuple(f"{r[0]}@{r[1]}" for r in future),
        )
    )

    # -- values in a sane range -------------------------------------------
    # Two very different faults look alike in a naive bounds check, so they
    # are separated here. A whole series outside its unit's range means the
    # series is not what we think it is -- discharge filed as stage, say --
    # and must block the export. One bad row in twenty thousand is a glitch
    # in INA's archive; it should be reported, not take the site down.
    blocking: list[str] = []
    isolated: list[str] = []
    for unit, (low, high) in SANE_RANGES.items():
        found = con.execute(
            f"""
            WITH scoped AS (
                SELECT o.series_id, o.value,
                       row_number() OVER (
                           PARTITION BY o.series_id ORDER BY o.day DESC
                       ) AS rn
                FROM observation o JOIN gauge g USING (series_id)
                WHERE {_unit_expr()} = ?
            )
            SELECT series_id,
                   sum(CASE WHEN value < ? OR value > ? THEN 1 ELSE 0 END) AS bad,
                   count(*) AS n,
                   max(CASE WHEN rn = 1 AND (value < ? OR value > ?)
                            THEN 1 ELSE 0 END) AS latest_bad
            FROM scoped
            GROUP BY series_id
            HAVING bad > 0
            ORDER BY bad DESC
            """,
            [unit, low, high, low, high],
        ).fetchall()
        for series_id, bad, total, latest_bad in found:
            share = 100.0 * bad / total
            label = f"{series_id} ({unit}): {bad}/{total} rows ({share:.2g}%)"
            if latest_bad or share > OUT_OF_RANGE_SHARE:
                blocking.append(label + (", including the latest" if latest_bad else ""))
            else:
                isolated.append(label)

    result.checks.append(
        Check(
            "values_in_sane_range",
            ERROR,
            not blocking,
            "no series is systematically outside its unit's range"
            if not blocking
            else f"{len(blocking)} series are mostly or currently out of range",
            tuple(blocking),
        )
    )
    result.checks.append(
        Check(
            "no_isolated_outliers",
            WARN,
            not isolated,
            "no isolated out-of-range readings"
            if not isolated
            else f"{len(isolated)} series carry isolated out-of-range readings",
            tuple(isolated),
        )
    )

    # -- thresholds match the quantity they are attached to ---------------
    # A stage threshold on a discharge series, or the reverse, would
    # mislabel every single day.
    mismatched = con.execute(
        f"""
        SELECT g.series_id, g.name, t.unit, {_unit_expr()} AS gauge_unit
        FROM threshold t JOIN gauge g USING (series_id)
        WHERE t.unit <> {_unit_expr()}
        ORDER BY g.series_id
        """
    ).fetchall()
    result.checks.append(
        Check(
            "threshold_units_match",
            ERROR,
            not mismatched,
            "every threshold is in the same unit as its gauge"
            if not mismatched
            else f"{len(mismatched)} thresholds are in the wrong unit for their gauge",
            tuple(f"{r[0]} {r[1]}: threshold {r[2]} vs gauge {r[3]}" for r in mismatched),
        )
    )

    # -- gauges that should be there ---------------------------------------
    missing = con.execute(
        "SELECT g.series_id, g.name FROM gauge g"
        " LEFT JOIN (SELECT DISTINCT series_id FROM observation) o USING (series_id)"
        " WHERE g.tier = ? AND o.series_id IS NULL ORDER BY g.river_order",
        [required_tier],
    ).fetchall()
    expected = con.execute(
        "SELECT count(*) FROM gauge WHERE tier = ?", [required_tier]
    ).fetchone()[0]
    result.checks.append(
        Check(
            "required_gauges_present",
            ERROR if len(missing) == expected else WARN,
            not missing,
            f"all {expected} tier-{required_tier} gauges have data"
            if not missing
            else f"{len(missing)} of {expected} tier-{required_tier} gauges have none",
            tuple(f"{r[0]} {r[1]}" for r in missing),
        )
    )

    # -- freshness (a warning, on purpose) --------------------------------
    last = store.last_data_day()
    age = (today - last).days if last else None
    result.checks.append(
        Check(
            "data_is_fresh",
            WARN,
            age is not None and age <= fresh_days,
            f"newest reading is {last} ({age} days old)"
            if last
            else "no readings at all",
        )
    )

    # -- latest value against the gauge's own record (a warning) ----------
    suspicious = con.execute(
        """
        WITH stats AS (
            SELECT series_id,
                   min(value) AS lo, max(value) AS hi,
                   quantile_cont(value, 0.25) AS q1,
                   quantile_cont(value, 0.75) AS q3,
                   count(*) AS n
            FROM observation GROUP BY series_id HAVING n >= 400
        ),
        latest AS (
            SELECT series_id, day, value FROM (
                SELECT series_id, day, value,
                       row_number() OVER (PARTITION BY series_id ORDER BY day DESC) rn
                FROM observation
            ) WHERE rn = 1
        )
        SELECT l.series_id, l.day, l.value, s.lo, s.hi
        FROM latest l JOIN stats s USING (series_id)
        WHERE greatest(s.q3 - s.q1, 1e-6) * ? <
              greatest(l.value - s.hi, s.lo - l.value)
        ORDER BY l.series_id
        """,
        [RECORD_MARGIN],
    ).fetchall()
    result.checks.append(
        Check(
            "latest_within_record",
            WARN,
            not suspicious,
            "every latest reading sits within its gauge's own record"
            if not suspicious
            else f"{len(suspicious)} gauges have a latest reading well outside their record",
            tuple(f"{r[0]}@{r[1]} {r[2]:g}" for r in suspicious),
        )
    )

    # -- thresholds --------------------------------------------------------
    official = con.execute("SELECT count(*) FROM threshold").fetchone()[0]
    result.checks.append(
        Check(
            "thresholds_loaded",
            WARN,
            official > 0,
            f"{official} gauges have official alert or evacuation levels",
        )
    )

    return result


def freshness_note(last: date | None, today: date | None = None) -> str:
    """A short human line about how current the data is."""
    today = today or date.today()
    if last is None:
        return "no data"
    age = (today - last).days
    if age <= 0:
        return "current"
    if age == 1:
        return "1 day behind"
    return f"{age} days behind"
