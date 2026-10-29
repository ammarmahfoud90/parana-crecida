"""The export gate: nothing is written unless the data tests passed."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from ina_a5 import DailyValue, SeriesCache

from parana_crecida.export import (
    ExportBlocked,
    build_river_today,
    export_river_today,
)
from parana_crecida.store import Store
from parana_crecida.thresholds import STATUS_OFFICIAL, Threshold
from parana_crecida.validate import Check, ValidationResult, validate

TODAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 22, 0, 0)


@pytest.fixture
def cache(tmp_path):
    return SeriesCache(tmp_path / "cache")


@pytest.fixture
def store(cache):
    with Store(":memory:") as s:
        s.load_gauges()
        end = date(2026, 10, 27)
        s_values = [
            # Oscillates instead of falling off a cliff: a 500-day straight
            # descent would run past -17 m and trip the sane-range check.
            DailyValue(day=end - timedelta(days=i), value=7.35 - (i % 60) * 0.05)
            for i in range(500)
        ]
        cache.write(26261, s_values)
        s.load_observations(cache, ["26261"])
        s.load_thresholds(
            {"26261": Threshold("26261", "m", 6.5, 7.0, STATUS_OFFICIAL, "Corrientes")}
        )
        yield s


def passing() -> ValidationResult:
    return ValidationResult([Check("ok_check", "error", True, "fine")])


def failing() -> ValidationResult:
    return ValidationResult([Check("bad_check", "error", False, "broken")])


THRESHOLDS = {"26261": Threshold("26261", "m", 6.5, 7.0, STATUS_OFFICIAL, "Corrientes")}


# -- the gate --------------------------------------------------------------


def test_a_failed_test_blocks_the_export(store, tmp_path):
    with pytest.raises(ExportBlocked):
        export_river_today(store, THRESHOLDS, failing(), out_dir=tmp_path)


def test_a_blocked_export_leaves_the_previous_file_untouched(store, tmp_path):
    """The last good data stays online, which is the whole point."""
    export_river_today(store, THRESHOLDS, passing(), out_dir=tmp_path, today=TODAY)
    before = (tmp_path / "river_today.json").read_text(encoding="utf-8")

    with pytest.raises(ExportBlocked):
        export_river_today(store, THRESHOLDS, failing(), out_dir=tmp_path)

    assert (tmp_path / "river_today.json").read_text(encoding="utf-8") == before


def test_a_passing_export_writes(store, tmp_path):
    result = export_river_today(
        store, THRESHOLDS, passing(), out_dir=tmp_path, today=TODAY
    )
    assert result.path.exists()
    assert result.with_official_thresholds == 1
    assert result.last_data_day == date(2026, 10, 27)


def test_no_temp_file_is_left_behind(store, tmp_path):
    export_river_today(store, THRESHOLDS, passing(), out_dir=tmp_path, today=TODAY)
    assert list(tmp_path.glob("*.tmp")) == []


def test_the_real_validator_drives_the_gate(store, tmp_path):
    """Not just hand-made results: the actual checks must let this through."""
    result = validate(store, today=TODAY)
    export_river_today(store, THRESHOLDS, result, out_dir=tmp_path, today=TODAY)
    assert (tmp_path / "river_today.json").exists()


# -- payload ---------------------------------------------------------------


def payload(store):
    return build_river_today(store, THRESHOLDS, passing(), now=NOW, today=TODAY)


def test_payload_header(store):
    data = payload(store)
    assert data["schema"] == 1
    assert data["last_data_day"] == "2026-10-27"
    assert data["data_age_days"] == 1
    assert data["official_source_url"].startswith("https://alerta.ina.gob.ar")


def test_payload_gauge(store):
    gauge = next(g for g in payload(store)["gauges"] if g["series_id"] == "26261")
    assert gauge["name"] == "Corrientes"
    assert gauge["value"] == pytest.approx(7.35)
    assert gauge["change"] == pytest.approx(0.05)
    assert gauge["alert"] == 6.5
    assert gauge["status"] == "evacuation"


def test_a_gauge_without_thresholds_is_unknown_not_below(store):
    gauge = next(g for g in payload(store)["gauges"] if g["series_id"] == "26273")
    assert gauge["alert"] is None
    assert gauge["status"] == "unknown"


def test_gauges_are_in_river_order(store):
    names = [g["name"] for g in payload(store)["gauges"]]
    assert names[0] == "Puerto Iguazú"
    assert names.index("Corrientes") < names.index("Rosario")


def test_payload_carries_accented_display_names(store):
    names = {g["name"] for g in payload(store)["gauges"]}
    assert "Paraná" in names
    assert "Parana" not in names


def test_payload_reports_its_own_warnings(store):
    result = ValidationResult([Check("data_is_fresh", "warn", False, "old")])
    data = build_river_today(store, THRESHOLDS, result, now=NOW, today=TODAY)
    assert data["validation"] == {"ok": True, "warnings": ["data_is_fresh"]}


def test_payload_is_json_serialisable_and_ascii_safe(store, tmp_path):
    export_river_today(store, THRESHOLDS, passing(), out_dir=tmp_path, today=TODAY)
    text = (tmp_path / "river_today.json").read_text(encoding="utf-8")
    assert json.loads(text)["schema"] == 1
    assert text.endswith("\n")
