"""Parsing INA's thresholds, and refusing to guess at them."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from parana_crecida import thresholds as th
from parana_crecida.thresholds import ReportRow, Threshold

REPORTS = Path(".reports")


def level_row(station, alert=None, evac=None, river="Paraná", value=1.0):
    return ReportRow(station, river, th.UNIT_LEVEL, value, "rising", alert, evac, "")


def flow_row(station, alert=None, evac=None):
    return ReportRow(station, "Paraná", th.UNIT_FLOW, 100.0, "rising", alert, evac, "")


# -- numbers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("7,39", 7.39),
        ("7,39 ↑", 7.39),          # trend arrow in the same cell
        ("25,00", 25.0),
        ("20000", 20000.0),
        ("12.500", 12500.0),            # dot grouping threes is thousands
        ("4,5 ⚠", 4.5),            # warning glyph
        (" 6,00 ", 6.0),
    ],
)
def test_parse_number(text, expected):
    assert th.parse_number(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [None, "", "-", "s/d", "no data", "↑"])
def test_parse_number_gives_up_rather_than_guessing(text):
    assert th.parse_number(text) is None


def test_parse_trend():
    assert th.parse_trend("7,39 ↑") == "rising"
    assert th.parse_trend("7,39 ↓") == "falling"
    assert th.parse_trend("7,39") == "unknown"


def test_normalise_name_strips_accents_and_case():
    assert th.normalise_name("Puerto Iguazú") == "puerto iguazu"
    assert th.normalise_name("  Paraná ") == "parana"
    assert th.normalise_name("Concepción del\nUruguay") == "concepcion del uruguay"


# -- matching --------------------------------------------------------------


def test_match_finds_gauges_by_normalised_name():
    matched, unmatched = th.match_to_roster(
        [level_row("Puerto Iguazú", 25.0, 28.0), level_row("Corrientes", 6.5, 7.0)]
    )
    assert set(matched) == {"26251", "26261"}
    assert unmatched == []


def test_rows_for_gauges_out_of_scope_are_kept_not_dropped():
    """The CSV has to be checkable against the PDF line by line."""
    _, unmatched = th.match_to_roster([level_row("Concordia", 11.0, 12.5, river="Uruguay")])
    assert [r.station for r in unmatched] == ["Concordia"]


def test_a_level_threshold_is_refused_for_a_discharge_gauge():
    """Puerto Pilcomayo: the series we hold under that name is m3/s.

    Attaching INA's 5.35 m alert to a 1,800 m3/s reading would mark every
    day of the record as catastrophic.
    """
    matched, unmatched = th.match_to_roster([level_row("Puerto Pilcomayo", 5.35, 6.0)])
    assert matched == {}
    assert [r.station for r in unmatched] == ["Puerto Pilcomayo"]


def test_a_discharge_threshold_is_accepted_for_a_discharge_gauge():
    matched, _ = th.match_to_roster([flow_row("Yacyretá efluente", 25000.0, 30000.0)])
    assert "26685" in matched


def test_a_gauge_listed_twice_keeps_the_first_and_reports_the_second():
    matched, unmatched = th.match_to_roster(
        [level_row("Corrientes", 6.5, 7.0), level_row("Corrientes", 1.0, 2.0)]
    )
    assert matched["26261"].alert == 6.5
    assert len(unmatched) == 1


# -- CSV -------------------------------------------------------------------


def test_build_rows_covers_every_gauge_in_scope():
    from parana_crecida import gauges as roster

    rows = th.build_rows(
        [level_row("Corrientes", 6.5, 7.0)],
        source_pdf="x.pdf",
        source_date=date(2026, 10, 28),
    )
    with_id = {r["series_id"] for r in rows if r["series_id"]}
    assert with_id == {g.series_id for g in roster.GAUGES}


def test_a_gauge_ina_does_not_list_is_left_empty():
    rows = th.build_rows(
        [level_row("Corrientes", 6.5, 7.0)],
        source_pdf="x.pdf",
        source_date=date(2026, 10, 28),
    )
    diamante = next(r for r in rows if r["series_id"] == "26273")
    assert diamante["alert"] == ""
    assert diamante["evacuation"] == ""
    assert diamante["status"] == th.STATUS_NOT_IN_REPORT


def test_unmatched_rows_are_appended_with_no_series_id():
    rows = th.build_rows(
        [level_row("Concordia", 11.0, 12.5, river="Uruguay")],
        source_pdf="x.pdf",
        source_date=date(2026, 10, 28),
    )
    extra = [r for r in rows if r["status"] == th.STATUS_UNMATCHED]
    assert len(extra) == 1
    assert extra[0]["series_id"] == ""
    assert extra[0]["ina_station"] == "Concordia"


def test_csv_round_trip(tmp_path):
    rows = th.build_rows(
        [level_row("Corrientes", 6.5, 7.0), level_row("Concordia", 11.0, 12.5)],
        source_pdf="x.pdf",
        source_date=date(2026, 10, 28),
    )
    path = tmp_path / "t.csv"
    th.write_csv(path, rows)
    loaded = th.load_csv(path)
    assert set(loaded) == {"26261"}
    assert loaded["26261"].alert == 6.5
    assert loaded["26261"].evacuation == 7.0
    assert loaded["26261"].source_date == date(2026, 10, 28)


def test_load_csv_skips_gauges_with_no_official_value(tmp_path):
    """A missing threshold must read as unknown, never as 0 m."""
    rows = th.build_rows([], source_pdf="x.pdf", source_date=date(2026, 10, 28))
    path = tmp_path / "t.csv"
    th.write_csv(path, rows)
    assert th.load_csv(path) == {}


def test_load_csv_of_a_missing_file_is_empty_not_an_error(tmp_path):
    assert th.load_csv(tmp_path / "nope.csv") == {}


# -- classify --------------------------------------------------------------


def threshold(alert=None, evac=None):
    return Threshold("26261", "m", alert, evac, th.STATUS_OFFICIAL)


def test_classify_against_both_lines():
    t = threshold(6.5, 7.0)
    assert th.classify(7.39, t) == "evacuation"
    assert th.classify(7.0, t) == "evacuation"   # at the line counts as at it
    assert th.classify(6.6, t) == "alert"
    assert th.classify(6.5, t) == "alert"
    assert th.classify(3.0, t) == "below"


def test_classify_without_a_threshold_is_unknown_not_below():
    assert th.classify(7.39, None) == "unknown"
    assert th.classify(7.39, threshold()) == "unknown"


def test_classify_without_a_reading_is_unknown():
    assert th.classify(None, threshold(6.5, 7.0)) == "unknown"


# -- the real PDF ----------------------------------------------------------


def _newest_report() -> Path | None:
    if not REPORTS.is_dir():
        return None
    found = sorted(REPORTS.glob("reporte_diario_*.pdf"))
    return found[-1] if found else None


@pytest.mark.skipif(_newest_report() is None, reason="no downloaded INA report")
def test_parses_a_real_ina_daily_report():
    """Checked against the real layout, not a fixture of my own making."""
    rows = th.parse_report(_newest_report())
    assert len(rows) > 15
    by_name = {r.key: r for r in rows}
    corrientes = by_name["corrientes"]
    assert corrientes.unit == th.UNIT_LEVEL
    assert corrientes.alert == pytest.approx(6.5)
    assert corrientes.evacuation == pytest.approx(7.0)
    assert any(r.unit == th.UNIT_FLOW for r in rows)
