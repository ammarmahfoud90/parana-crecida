"""Upstream drivers: ENSO parsing, and what a payload may and may not claim."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from ina_a5 import DailyValue, SeriesCache

from parana_crecida.drivers import (
    DRIVER_SERIES,
    ONI_URL,
    OniReading,
    build_payload,
    classify_enso,
    load_oni,
    parse_oni,
    write_oni,
)
from parana_crecida.normalize import Climatology
from parana_crecida.store import Store

TODAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 22, 0, 0)

SAMPLE = """\
SEAS YR TOTAL ANOM
DJF 2026  26.12  -0.39
JFM 2026  26.58  -0.21
FMA 2026  27.21   0.11
MAM 2026  27.90   0.46
AMJ 2026  28.74   0.95
MJJ 2026  29.02   1.39
JJA 2026  29.09   1.80
JAS 2026  29.12   2.16
"""


# -- parsing ---------------------------------------------------------------


def test_parse_oni_reads_every_season():
    readings = parse_oni(SAMPLE)
    assert len(readings) == 8
    assert readings[-1].season == "JAS"
    assert readings[-1].anomaly == pytest.approx(2.16)
    assert readings[-1].sst == pytest.approx(29.12)


def test_parse_oni_skips_the_header_and_rubbish():
    readings = parse_oni("SEAS YR TOTAL ANOM\nnonsense\nJAS 2026 29.12 2.16\nXYZ 1 2 3\n")
    assert len(readings) == 1


def test_parse_oni_skips_an_unparseable_number_rather_than_guessing():
    readings = parse_oni("JAS 2026 29.12 n/a\nJJA 2026 29.09 1.80\n")
    assert [r.season for r in readings] == ["JJA"]


def test_parse_oni_sorts_chronologically():
    readings = parse_oni("JAS 2026 29.12 2.16\nDJF 2020 26.0 0.5\n")
    assert [r.year for r in readings] == [2020, 2026]


def test_season_maps_to_its_middle_month():
    assert OniReading("DJF", 2026, 0.0).day == date(2026, 1, 1)
    assert OniReading("JAS", 2026, 0.0).day == date(2026, 8, 1)
    assert OniReading("NDJ", 2026, 0.0).day == date(2026, 12, 1)


# -- classification --------------------------------------------------------


@pytest.mark.parametrize(
    "anomaly,expected",
    [
        (2.16, "el_nino_very_strong"),
        (2.0, "el_nino_very_strong"),
        (1.7, "el_nino_strong"),
        (1.2, "el_nino_moderate"),
        (0.6, "el_nino_weak"),
        (0.2, "neutral"),
        (-0.2, "neutral"),
        (-0.7, "la_nina_weak"),
        (-1.2, "la_nina_moderate"),
        (-1.7, "la_nina_strong"),
        (-2.5, "la_nina_very_strong"),
    ],
)
def test_classify_enso(anomaly, expected):
    assert classify_enso(anomaly) == expected


def test_classify_enso_without_a_reading():
    assert classify_enso(None) == "unknown"


def test_the_boundaries_are_noaa_s():
    """+/-0.5 is the threshold; 0.5 is warm and 0.49 is not."""
    assert classify_enso(0.5) == "el_nino_weak"
    assert classify_enso(0.49) == "neutral"
    assert classify_enso(-0.5) == "la_nina_weak"
    assert classify_enso(-0.49) == "neutral"


# -- storing ---------------------------------------------------------------


def test_oni_round_trip(tmp_path):
    path = tmp_path / "oni.json"
    write_oni(path, parse_oni(SAMPLE))
    loaded = load_oni(path)
    assert len(loaded) == 8
    assert loaded[-1].anomaly == pytest.approx(2.16)
    assert json.loads(path.read_text(encoding="utf-8"))["source"] == ONI_URL


def test_load_oni_of_a_missing_file_is_empty_not_an_error(tmp_path):
    assert load_oni(tmp_path / "nope.json") == []


# -- the payload -----------------------------------------------------------


def flat_climatology(*series_ids):
    curve = {"5": 100.0, "10": 200.0, "25": 300.0, "50": 400.0,
             "75": 500.0, "90": 600.0, "95": 700.0}
    return Climatology(
        {s: {"years": 20, "percentiles": {str(d): dict(curve) for d in range(1, 366)}}
         for s in series_ids}
    )


@pytest.fixture
def store(tmp_path):
    cache = SeriesCache(tmp_path / "cache")
    for series_id in DRIVER_SERIES:
        cache.write(
            series_id,
            [
                DailyValue(day=TODAY - timedelta(days=i), value=800.0 - i)
                for i in range(400)
            ],
        )
    with Store(":memory:") as s:
        s.load_gauges()
        s.load_observations(cache, list(DRIVER_SERIES))
        yield s


@pytest.fixture
def clim():
    return flat_climatology(*DRIVER_SERIES)


def test_payload_covers_the_driver_gauges(store, clim):
    data = build_payload(store, clim, today=TODAY, now=NOW)
    assert [g["series_id"] for g in data["gauges"]] == list(DRIVER_SERIES)


def test_payload_windows_the_series(store, clim):
    data = build_payload(store, clim, window_days=90, today=TODAY, now=NOW)
    assert len(data["gauges"][0]["days"]) == 91


def test_payload_reports_the_seven_day_change(store, clim):
    data = build_payload(store, clim, today=TODAY, now=NOW)
    # values fall by 1 per day going back, so the latest is 7 above day -7.
    assert data["gauges"][0]["change_7d"] == pytest.approx(7.0)


def test_payload_places_the_latest_against_the_record(store, clim):
    data = build_payload(store, clim, today=TODAY, now=NOW)
    assert data["gauges"][0]["latest"]["percentile"] == 100.0


def test_a_gauge_with_no_climatology_reports_no_percentile(store):
    """Unknown, never a number invented from nothing."""
    data = build_payload(store, Climatology({}), today=TODAY, now=NOW)
    assert data["gauges"][0]["latest"]["percentile"] is None


def test_payload_carries_enso(store, clim):
    data = build_payload(store, clim, oni=parse_oni(SAMPLE), today=TODAY, now=NOW)
    assert data["enso"]["season"] == "JAS"
    assert data["enso"]["phase"] == "el_nino_very_strong"
    assert len(data["enso"]["recent"]) == 8


def test_payload_without_enso_says_so(store, clim):
    data = build_payload(store, clim, oni=[], today=TODAY, now=NOW)
    assert data["enso"] is None


def test_report_only_stations_are_carried_with_their_date(store, clim):
    """Guaira and Itaipu: today's number from the PDF, and no history."""
    snapshot = {
        "source_date": "2026-10-28",
        "rows": [
            {"station": "Represa Itaipú", "river": "Paraná", "unit": "m3/s",
             "value": 16300.0, "trend": "falling", "alert": 20000.0,
             "evacuation": 25000.0},
            {"station": "Corrientes", "river": "Paraná", "unit": "m",
             "value": 7.35, "trend": "rising", "alert": 6.5, "evacuation": 7.0},
        ],
    }
    data = build_payload(store, clim, snapshot=snapshot, today=TODAY, now=NOW)
    assert [r["station"] for r in data["report_only"]] == ["Represa Itaipú"]
    assert data["report_date"] == "2026-10-28"


def test_report_only_is_empty_without_a_snapshot(store, clim):
    data = build_payload(store, clim, today=TODAY, now=NOW)
    assert data["report_only"] == []


def test_payload_does_not_leak_internal_notes(store, clim):
    """The roster's notes are English developer annotations."""
    data = build_payload(store, clim, today=TODAY, now=NOW)
    assert "note" not in data["gauges"][0]


def test_payload_is_json_safe(store, clim):
    data = build_payload(store, clim, oni=parse_oni(SAMPLE), today=TODAY, now=NOW)
    assert json.loads(json.dumps(data))["schema"] == 1
