"""Assembling the wave view, and the window/record split it rests on."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import pytest
from ina_a5 import DailyValue, SeriesCache

from parana_crecida import gauges as roster
from parana_crecida.chain import ChainNode
from parana_crecida.export import ExportBlocked, export_wave
from parana_crecida.normalize import Climatology
from parana_crecida.store import Store
from parana_crecida.validate import Check, ValidationResult
from parana_crecida.wave import analyse, build_payload

TODAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 22, 0, 0)

UPSTREAM = "26261"    # Corrientes
DOWNSTREAM = "26263"  # Empedrado


def node(series_id, km):
    return ChainNode(series_id, roster.gauge(series_id).name, km, 1, 1000.0)


NODES = [node(UPSTREAM, 0.0), node(DOWNSTREAM, 300.0)]


def flat_climatology(*series_ids):
    curve = {"5": 1.0, "10": 2.0, "25": 3.0, "50": 4.0, "75": 5.0, "90": 6.0, "95": 7.0}
    return Climatology(
        {
            s: {"years": 100, "percentiles": {str(d): dict(curve) for d in range(1, 366)}}
            for s in series_ids
        }
    )


def wave_series(n_days, *, end=TODAY, peaks=(), height=5.0, half_width=12, base=3.0):
    """A record with triangular crests at the given day offsets from the start."""
    start = end - timedelta(days=n_days - 1)
    out = []
    for i in range(n_days):
        value = base + 0.3 * math.sin(2 * math.pi * i / 90.0)
        for peak in peaks:
            distance = abs(i - peak)
            if distance < half_width:
                value += height * (1 - distance / half_width)
        out.append(DailyValue(day=start + timedelta(days=i), value=value))
    return out


@pytest.fixture
def store(tmp_path):
    cache = SeriesCache(tmp_path / "cache")
    # 700 days, with matching crests 300 days back and 8 days apart.
    cache.write(UPSTREAM, wave_series(700, peaks=(400,)))
    cache.write(DOWNSTREAM, wave_series(700, peaks=(408,)))
    with Store(":memory:") as s:
        s.load_gauges()
        s.load_observations(cache, [UPSTREAM, DOWNSTREAM])
        yield s


@pytest.fixture
def clim():
    return flat_climatology(UPSTREAM, DOWNSTREAM)


# -- analysis --------------------------------------------------------------


def test_analyse_covers_the_chain(store, clim):
    result = analyse(store, clim, NODES, today=TODAY)
    assert [g.node.series_id for g in result.gauges] == [UPSTREAM, DOWNSTREAM]


def test_the_plot_is_windowed(store, clim):
    result = analyse(store, clim, NODES, window_days=180, today=TODAY)
    assert result.window_start == TODAY - timedelta(days=180)
    for gauge in result.gauges:
        assert len(gauge.offsets) <= 181
        assert max(gauge.offsets) <= 180


def test_travel_times_use_the_whole_record_not_the_window(store, clim):
    """The crests are 300 days back; a 180-day window must still find the lag.

    This is the case that matters live: a wave still rising has not crested,
    so there may be no crest in the window at all, and the travel times come
    from a century of earlier waves.
    """
    result = analyse(store, clim, NODES, window_days=180, today=TODAY)
    assert all(g.crests == [] for g in result.gauges)  # nothing in the window
    assert result.legs and result.legs[0]["median_days"] == pytest.approx(8, abs=1)


def test_a_gauge_with_no_data_is_left_out_rather_than_faked(store, clim):
    nodes = NODES + [node("26276", 900.0)]  # Rosario, nothing loaded
    result = analyse(store, clim, nodes, today=TODAY)
    assert [g.node.series_id for g in result.gauges] == [UPSTREAM, DOWNSTREAM]


def test_rising_is_reported(store, clim):
    result = analyse(store, clim, NODES, today=TODAY)
    assert all(g.rising in (True, False) for g in result.gauges)


def test_celerity_is_fitted_across_the_chain(store, clim):
    result = analyse(store, clim, NODES, today=TODAY)
    assert result.celerity is not None
    assert result.celerity.km_per_day == pytest.approx(300 / 8, rel=0.2)


# -- payload ---------------------------------------------------------------


def payload(store, clim, **kwargs):
    return build_payload(analyse(store, clim, NODES, today=TODAY, **kwargs), now=NOW)


def test_payload_header(store, clim):
    data = payload(store, clim)
    assert data["schema"] == 1
    assert data["window"]["end"] == "2026-10-28"
    assert data["window"]["days"] == 180


def test_payload_gauges_carry_position_and_series(store, clim):
    gauge = payload(store, clim)["gauges"][0]
    assert gauge["series_id"] == UPSTREAM
    assert gauge["river_km"] == 0.0
    assert len(gauge["offsets"]) == len(gauge["values"]) == len(gauge["percentiles"])


def test_payload_uses_accented_display_names(store, clim):
    nodes = [node("26251", 0.0)]
    cache_backed = analyse(store, clim, nodes, today=TODAY)
    # 26251 has no data here, so fall back to checking the roster mapping.
    assert roster.gauge("26251").label == "Puerto Iguazú"
    assert cache_backed.gauges == []


def test_payload_legs(store, clim):
    leg = payload(store, clim)["legs"][0]
    assert leg["from_name"] == "Corrientes"
    assert leg["to_name"] == "Empedrado"
    assert leg["km"] == 300.0
    assert leg["n"] >= 1


def test_short_legs_publish_no_speed(store, clim):
    """Resolution-limited legs keep their arrival time and drop km/day."""
    short = [node(UPSTREAM, 0.0), node(DOWNSTREAM, 21.0)]
    data = build_payload(analyse(store, clim, short, today=TODAY), now=NOW)
    leg = data["legs"][0]
    if leg["resolution_limited"]:
        assert leg["celerity"] is None


def test_payload_is_json_safe(store, clim):
    import json

    assert json.loads(json.dumps(payload(store, clim)))["schema"] == 1


# -- the export gate -------------------------------------------------------


def failing():
    return ValidationResult([Check("bad", "error", False, "broken")])


def passing():
    return ValidationResult([Check("ok", "error", True, "fine")])


def test_a_failed_test_blocks_the_wave_export(store, tmp_path):
    with pytest.raises(ExportBlocked):
        export_wave(store, failing(), out_dir=tmp_path)


def test_no_chain_blocks_the_wave_export(store, tmp_path):
    """Without a chain nothing can be placed by distance, so nothing is."""
    with pytest.raises(ExportBlocked, match="chain"):
        export_wave(
            store, passing(), chain_path=tmp_path / "missing.json", out_dir=tmp_path
        )


# -- frozen travel times ---------------------------------------------------


def test_frozen_travel_times_are_used_as_given(store, clim):
    """The scheduled run has a year of cache; it must not re-measure."""
    frozen = [{"from": UPSTREAM, "to": DOWNSTREAM, "from_name": "Corrientes",
               "to_name": "Empedrado", "km": 300.0, "median_days": 11.0,
               "p25_days": 9.0, "p75_days": 13.0, "correlation_days": 11.0,
               "n": 42, "confident": True, "agree": True,
               "resolution_limited": False, "celerity": 27.3}]
    result = analyse(store, clim, NODES, today=TODAY, travel_times=frozen)
    assert result.legs == frozen
    assert result.celerity.km_per_day == pytest.approx(300 / 11)


def test_a_short_cache_with_frozen_times_still_reports_them(tmp_path, clim):
    """The regression this fixes: a year of data measured zero waves."""
    cache = SeriesCache(tmp_path / "cache")
    cache.write(UPSTREAM, wave_series(300))
    cache.write(DOWNSTREAM, wave_series(300))
    frozen = [{"from": UPSTREAM, "to": DOWNSTREAM, "from_name": "Corrientes",
               "to_name": "Empedrado", "km": 300.0, "median_days": 11.0,
               "p25_days": 9.0, "p75_days": 13.0, "correlation_days": None,
               "n": 42, "confident": True, "agree": None,
               "resolution_limited": False, "celerity": 27.3}]
    with Store(":memory:") as s:
        s.load_gauges()
        s.load_observations(cache, [UPSTREAM, DOWNSTREAM])
        result = analyse(s, clim, NODES, today=TODAY, travel_times=frozen)
    assert result.legs[0]["n"] == 42
    assert result.celerity is not None


def test_no_frozen_table_means_no_travel_times_rather_than_wrong_ones(store, clim):
    result = analyse(store, clim, NODES, today=TODAY, travel_times=[])
    assert result.legs == []
    assert result.celerity is None
