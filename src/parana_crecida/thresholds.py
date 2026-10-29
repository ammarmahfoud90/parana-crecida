"""INA alert and evacuation levels, from the daily report PDF.

The a5 API does not publish thresholds. They appear only in INA's daily
report, `alerta.ina.gob.ar/ina/06-INFORMES/diario/`, in two tables: stage
gauges in metres, and dam and confluence flows in m3/s. Both carry an
``Alerta`` and an ``Evacuacion`` column.

This module parses that PDF into a CSV in the repo, which is then the one
source for thresholds. Rules it follows, because a wrong threshold here
would mislabel a real flood:

* **Nothing is invented.** A cell that is absent or unparseable becomes an
  empty field with a status saying so, never a guess and never a value
  carried over from another gauge.
* **Nothing is dropped silently.** Report rows that do not match a gauge in
  scope are written out too, marked ``unmatched_report_row``, so the CSV can
  be checked line by line against the PDF.
* **Matching is on a normalised name only** - accents stripped, case folded,
  whitespace collapsed. No fuzzy matching, because "Parana" the city, the
  river and the province all appear in these tables.
"""

from __future__ import annotations

import csv
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import gauges as roster

log = logging.getLogger(__name__)

REPORT_BASE = "https://alerta.ina.gob.ar/ina/06-INFORMES/diario/pdf"
REPORT_NAME = "reporte_diario_{day}.pdf"

#: Public page the site links to, so a reader can check the official alert.
INA_ALERT_URL = "https://alerta.ina.gob.ar/"

UNIT_LEVEL = "m"
UNIT_FLOW = "m3/s"

STATUS_OFFICIAL = "official"
STATUS_NO_VALUE = "no_official_value"
STATUS_NOT_IN_REPORT = "not_in_report"
STATUS_UNMATCHED = "unmatched_report_row"

CSV_FIELDS = (
    "series_id",
    "gauge",
    "tier",
    "ina_station",
    "ina_river",
    "unit",
    "alert",
    "evacuation",
    "status",
    "source_pdf",
    "source_date",
)

# Arrows, warning glyphs and stray marks that sit inside the value cells.
_DECORATION = re.compile(r"[↑↓→↔⚠️\s]+")
_THOUSANDS = re.compile(r"^\d{1,3}(\.\d{3})+$")


def report_url(day: date) -> str:
    """URL of the daily report for ``day``."""
    return f"{REPORT_BASE}/{REPORT_NAME.format(day=day.isoformat())}"


def normalise_name(text: str) -> str:
    """Fold a station name for matching: no accents, lower case, one space."""
    stripped = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    return " ".join(stripped.lower().split())


def parse_number(text: str | None) -> float | None:
    """A number from a report cell, or ``None`` if there is not one.

    Cells carry trend arrows and warning glyphs alongside the figure. The
    decimal separator is a comma; a dot is a thousands separator only when
    it groups digits in threes, which is the one unambiguous case.
    """
    if text is None:
        return None
    cleaned = _DECORATION.sub("", str(text))
    if not cleaned or cleaned in {"-", "--", "s/d", "S/D"}:
        return None
    if _THOUSANDS.match(cleaned):
        cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        log.warning("unparseable number in report cell: %r", text)
        return None


def parse_trend(text: str | None) -> str:
    """``rising``, ``falling`` or ``unknown`` from the arrow in a value cell."""
    raw = str(text or "")
    if "↑" in raw:
        return "rising"
    if "↓" in raw:
        return "falling"
    return "unknown"


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One row of one of the daily report's two threshold tables."""

    station: str
    river: str
    unit: str
    value: float | None
    trend: str
    alert: float | None
    evacuation: float | None
    outlook: str

    @property
    def key(self) -> str:
        return normalise_name(self.station)


@dataclass(frozen=True, slots=True)
class Threshold:
    """What the CSV holds for one gauge."""

    series_id: str
    unit: str
    alert: float | None
    evacuation: float | None
    status: str
    ina_station: str = ""
    source_date: date | None = None

    @property
    def has_values(self) -> bool:
        return self.alert is not None or self.evacuation is not None


def _header_unit(header: list[str | None]) -> str | None:
    """Which table this is, from its value column, or ``None`` if neither."""
    cells = [normalise_name(c or "") for c in header]
    if not cells or not cells[0].startswith("estacion"):
        return None
    if not any("alerta" in c for c in cells):
        return None
    joined = " ".join(cells)
    if "caudal" in joined:
        return UNIT_FLOW
    if "nivel" in joined:
        return UNIT_LEVEL
    return None


def parse_report(pdf_path: Path | str) -> list[ReportRow]:
    """Every row of both threshold tables in a daily report PDF.

    Reads the structured tables rather than the page text: the text layer
    wraps the outlook column across lines, which would misalign the numbers.
    """
    import pdfplumber  # imported here so the CLI starts without it

    rows: list[ReportRow] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table:
                    continue
                unit = _header_unit(table[0])
                if unit is None:
                    continue
                for raw in table[1:]:
                    cells = [(c or "").replace("\n", " ").strip() for c in raw]
                    if len(cells) < 5 or not cells[0]:
                        continue
                    station = " ".join(cells[0].split())
                    if normalise_name(station).startswith("estacion"):
                        continue
                    rows.append(
                        ReportRow(
                            station=station,
                            river=" ".join(cells[1].split()),
                            unit=unit,
                            value=parse_number(cells[2]),
                            trend=parse_trend(cells[2]),
                            alert=parse_number(cells[3]),
                            evacuation=parse_number(cells[4]),
                            outlook=" ".join(cells[5].split()) if len(cells) > 5 else "",
                        )
                    )
    log.info("parsed %d threshold rows from %s", len(rows), pdf_path)
    return rows


def match_to_roster(
    rows: list[ReportRow],
) -> tuple[dict[str, ReportRow], list[ReportRow]]:
    """Split report rows into those that are a gauge in scope and those not.

    Matching is exact on the normalised name. A report row that matches
    nothing is returned rather than discarded.
    """
    by_name = {normalise_name(g.name): g for g in roster.GAUGES}
    matched: dict[str, ReportRow] = {}
    unmatched: list[ReportRow] = []
    for row in rows:
        gauge = by_name.get(row.key)
        if gauge is None:
            unmatched.append(row)
            continue
        # A name can match while the quantity does not. INA lists a stage
        # for Puerto Pilcomayo, but the series we hold under that name is
        # discharge; attaching a 5.35 m alert to a 1,800 m3/s reading would
        # mislabel every day as catastrophic. Refuse on unit, do not guess.
        gauge_is_flow = "flow" in gauge.flags
        if gauge_is_flow != (row.unit == UNIT_FLOW):
            log.warning(
                "%s (%s) is %s but the report row is in %s; not attaching",
                gauge.name, gauge.series_id,
                "discharge" if gauge_is_flow else "stage", row.unit,
            )
            unmatched.append(row)
            continue
        # A gauge appearing twice would make the CSV ambiguous; keep the
        # first and report the collision rather than overwrite.
        if gauge.series_id in matched:
            log.warning(
                "gauge %s (%s) matched twice in the report; keeping the first",
                gauge.series_id, gauge.name,
            )
            unmatched.append(row)
            continue
        matched[gauge.series_id] = row
    return matched, unmatched


def build_rows(
    rows: list[ReportRow], *, source_pdf: str, source_date: date
) -> list[dict[str, str]]:
    """The CSV rows: every gauge in scope, then every unmatched report row."""
    matched, unmatched = match_to_roster(rows)
    out: list[dict[str, str]] = []

    ordered = [*roster.main_stem_sequence(), *roster.UPSTREAM, *roster.TRIBUTARIES]
    seen: set[str] = set()
    for gauge in ordered:
        if gauge.series_id in seen:
            continue
        seen.add(gauge.series_id)
        row = matched.get(gauge.series_id)
        if row is None:
            status, unit, alert, evac, station = STATUS_NOT_IN_REPORT, "", "", "", ""
        else:
            alert = "" if row.alert is None else f"{row.alert:g}"
            evac = "" if row.evacuation is None else f"{row.evacuation:g}"
            unit, station = row.unit, row.station
            status = STATUS_OFFICIAL if (alert or evac) else STATUS_NO_VALUE
        out.append(
            {
                "series_id": gauge.series_id,
                "gauge": gauge.name,
                "tier": str(gauge.tier),
                "ina_station": station,
                "ina_river": row.river if row else "",
                "unit": unit,
                "alert": alert,
                "evacuation": evac,
                "status": status,
                "source_pdf": source_pdf if row else "",
                "source_date": source_date.isoformat() if row else "",
            }
        )

    for row in unmatched:
        out.append(
            {
                "series_id": "",
                "gauge": "",
                "tier": "",
                "ina_station": row.station,
                "ina_river": row.river,
                "unit": row.unit,
                "alert": "" if row.alert is None else f"{row.alert:g}",
                "evacuation": "" if row.evacuation is None else f"{row.evacuation:g}",
                "status": STATUS_UNMATCHED,
                "source_pdf": source_pdf,
                "source_date": source_date.isoformat(),
            }
        )
    return out


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write the thresholds CSV, newline-stable so diffs stay readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_FIELDS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def load_csv(path: Path | str) -> dict[str, Threshold]:
    """Thresholds by series id, for gauges that have an official value.

    Rows with no value are skipped rather than loaded as zero: a missing
    threshold must read as "unknown" everywhere downstream, never as "0 m".
    """
    path = Path(path)
    if not path.exists():
        log.warning("no thresholds CSV at %s; levels will show as unknown", path)
        return {}
    out: dict[str, Threshold] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            series_id = (row.get("series_id") or "").strip()
            if not series_id or row.get("status") != STATUS_OFFICIAL:
                continue
            source = (row.get("source_date") or "").strip()
            out[series_id] = Threshold(
                series_id=series_id,
                unit=(row.get("unit") or "").strip(),
                alert=parse_number(row.get("alert")),
                evacuation=parse_number(row.get("evacuation")),
                status=STATUS_OFFICIAL,
                ina_station=(row.get("ina_station") or "").strip(),
                source_date=date.fromisoformat(source) if source else None,
            )
    return out


def classify(
    value: float | None, threshold: Threshold | None
) -> str:
    """Where a level sits against its official lines.

    ``unknown`` when there is no reading or no official threshold - never a
    guess, because the whole point of these lines is that they are official.
    """
    if value is None or threshold is None:
        return "unknown"
    if threshold.evacuation is not None and value >= threshold.evacuation:
        return "evacuation"
    if threshold.alert is not None and value >= threshold.alert:
        return "alert"
    if threshold.alert is None and threshold.evacuation is None:
        return "unknown"
    return "below"
