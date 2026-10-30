"""The command line, exercised offline.

``fetch`` and ``catalogue`` need the network and are not covered here; the
client's own behaviour under failure is covered in the ina-a5 suite.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from click.testing import CliRunner
from ina_a5 import DailyValue, SeriesCache

from parana_crecida.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def seasonal(n_days, *, start=date(2015, 1, 1), base=3.0, shift=0.0):
    return [
        DailyValue(
            day=start + timedelta(days=i),
            value=base + math.sin(2 * math.pi * i / 365.0) + 0.05 * math.sin(i * 1.7) + shift,
        )
        for i in range(n_days)
    ]


# -- gauges ----------------------------------------------------------------


def test_gauges_lists_the_roster(runner):
    result = runner.invoke(main, ["gauges"])
    assert result.exit_code == 0
    assert "Corrientes" in result.output
    assert "Rosario" in result.output


def test_gauges_can_filter_to_tier_one(runner):
    result = runner.invoke(main, ["gauges", "--tier", "1"])
    assert result.exit_code == 0
    assert "Corrientes" in result.output
    assert "Guazucito" not in result.output  # tier 2


def test_gauges_names_the_routing_nodes(runner):
    result = runner.invoke(main, ["gauges"])
    assert "routing nodes" in result.output


# -- guards ----------------------------------------------------------------


def test_guards_passes_a_healthy_cache(runner, tmp_path):
    cache = SeriesCache(tmp_path / "cache")
    values = seasonal(365 * 7)
    cache.write(26261, values)
    result = runner.invoke(
        main,
        [
            "guards",
            "--cache-dir", str(tmp_path / "cache"),
            "--series", "26261",
            "--as-of", values[-1].day.isoformat(),
        ],
    )
    assert result.exit_code == 0
    assert "1 clean, 0 flagged" in result.output


def test_guards_reports_an_uncached_series(runner, tmp_path):
    (tmp_path / "cache").mkdir()
    result = runner.invoke(
        main, ["guards", "--cache-dir", str(tmp_path / "cache"), "--series", "26261"]
    )
    assert result.exit_code == 0
    assert "not cached" in result.output


def test_guards_flags_a_stale_feed(runner, tmp_path):
    cache = SeriesCache(tmp_path / "cache")
    values = seasonal(365 * 7)
    cache.write(26261, values)
    late = (values[-1].day + timedelta(days=60)).isoformat()
    result = runner.invoke(
        main,
        [
            "guards",
            "--cache-dir", str(tmp_path / "cache"),
            "--series", "26261",
            "--as-of", late,
        ],
    )
    assert result.exit_code == 0
    assert "stale" in result.output


def test_guards_catches_a_datum_step(runner, tmp_path):
    """The Corrientes case, end to end through the CLI."""
    cache = SeriesCache(tmp_path / "cache")
    history = seasonal(365 * 6)
    stepped = seasonal(90, start=history[-1].day + timedelta(days=1), shift=4.0)
    cache.write(26261, history + stepped)
    result = runner.invoke(
        main,
        [
            "guards",
            "--cache-dir", str(tmp_path / "cache"),
            "--series", "26261",
            "--as-of", stepped[-1].day.isoformat(),
        ],
    )
    assert result.exit_code == 0
    assert "datum_step" in result.output


def test_guards_writes_a_report(runner, tmp_path):
    cache = SeriesCache(tmp_path / "cache")
    cache.write(26261, seasonal(100))
    report = tmp_path / "reports" / "guards.txt"
    result = runner.invoke(
        main,
        [
            "guards",
            "--cache-dir", str(tmp_path / "cache"),
            "--series", "26261",
            "--as-of", "2015-04-10",
            "--report", str(report),
        ],
    )
    assert result.exit_code == 0
    assert report.exists()
    assert "guards" in report.read_text(encoding="utf-8")


def test_guards_searches_cache_roots_in_order(runner, tmp_path):
    """The multi-root behaviour build_daily.py lacked, through the CLI."""
    SeriesCache(tmp_path / "recent").write(26261, seasonal(365 * 7))
    (tmp_path / "full").mkdir()
    values = seasonal(365 * 7)
    result = runner.invoke(
        main,
        [
            "guards",
            "--cache-dir", str(tmp_path / "full"),
            "--cache-dir", str(tmp_path / "recent"),
            "--series", "26261",
            "--as-of", values[-1].day.isoformat(),
        ],
    )
    assert result.exit_code == 0
    assert "1 clean" in result.output


# -- plumbing --------------------------------------------------------------


def test_only_implemented_commands_are_registered():
    """No stubs: every registered command must actually do something.

    The analysis commands (events, lags, track) arrive with their modules.
    """
    assert set(main.commands) == {
        "gauges",
        "animation",
        "film",
        "floodwater",
        "backtest",
        "basemap",
        "catalogue",
        "chain",
        "climatology",
        "lags",
        "oni",
        "rainfall",
        "thresholds",
        "fetch",
        "load",
        "mapframes",
        "validate",
        "exposure",
        "export",
        "frequency",
        "daily",
        "flooded",
        "freshness",
        "guards",
    }


def test_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


# -- freshness -------------------------------------------------------------


def write_site(
    tmp_path, *, age_days=1, rain_stale=0, with_rain=True, with_layers=True,
    with_stage=True, stage_age_days=1,
):
    import json
    from datetime import timedelta

    # The river's clock, not the runner's. `crecida freshness` measures age
    # against America/Argentina/Buenos_Aires; a fixture built on the local
    # date disagrees with it by a day whenever the two are on different
    # calendar days - which, on a UTC runner, is every day between 00:00
    # and 03:00 UTC. CI went red at 00:01.
    from ina_a5.observations import DEFAULT_TZ
    from datetime import datetime

    today = datetime.now(DEFAULT_TZ).date()

    data = tmp_path / "site"
    data.mkdir(exist_ok=True)
    if with_stage:
        # The daily river colour is the map's default layer: absent or behind,
        # the map opens on nothing while every other check passes.
        newest = today - timedelta(days=stage_age_days)
        (data / "river_stage.json").write_text(json.dumps({
            "days": [(newest - timedelta(days=1)).isoformat(), newest.isoformat()],
            "anchors": [{"series_id": "26261", "river_km": 659.8}],
            "segments": [{"km": 660.0}],
        }), encoding="utf-8")
    if with_layers:
        # The map fetches these by relative path from the published site, so
        # one left behind in the repository's data/ directory is simply never
        # found and the map renders without gauges or departamento lines.
        for name in ("gauges.geojson", "departamentos.geojson"):
            (data / name).write_text('{"type":"FeatureCollection","features":[]}',
                                     encoding="utf-8")
    (data / "river_today.json").write_text(json.dumps({
        "last_data_day": "2026-10-28", "data_age_days": age_days,
    }), encoding="utf-8")
    if with_rain:
        (data / "drivers.json").write_text(json.dumps({
            "rainfall": {"fetched_on": "2026-10-29", "stale_days": rain_stale},
        }), encoding="utf-8")
    return data


def test_freshness_passes_on_current_data(runner, tmp_path):
    site = write_site(tmp_path)
    result = runner.invoke(main, ["freshness", "--site-data", str(site)])
    assert result.exit_code == 0
    assert "current" in result.output


def test_freshness_fails_on_a_stalled_river_feed(runner, tmp_path):
    """The site stays up; the run goes red, which is what reaches a person."""
    site = write_site(tmp_path, age_days=9)
    result = runner.invoke(main, ["freshness", "--site-data", str(site)])
    assert result.exit_code != 0
    assert "9 days old" in result.output


def test_freshness_fails_on_rainfall_left_to_rot(runner, tmp_path):
    site = write_site(tmp_path, rain_stale=40)
    result = runner.invoke(main, ["freshness", "--site-data", str(site)])
    assert result.exit_code != 0
    assert "service account" in result.output


def test_freshness_tolerates_a_site_with_no_rainfall(runner, tmp_path):
    site = write_site(tmp_path, with_rain=False)
    result = runner.invoke(main, ["freshness", "--site-data", str(site)])
    assert result.exit_code == 0


def test_freshness_fails_when_the_daily_river_is_not_published(runner, tmp_path):
    """It is the map's default layer, so its absence is a blank map."""
    site = write_site(tmp_path, with_stage=False)
    result = runner.invoke(main, ["freshness", "--site-data", str(site)])
    assert result.exit_code != 0
    assert "default layer" in result.output


def test_freshness_fails_when_the_daily_river_falls_behind(runner, tmp_path):
    site = write_site(tmp_path, stage_age_days=9)
    result = runner.invoke(main, ["freshness", "--site-data", str(site)])
    assert result.exit_code != 0
    assert "daily river colour is 9 days old" in result.output


def test_freshness_fails_when_nothing_is_published(runner, tmp_path):
    result = runner.invoke(main, ["freshness", "--site-data", str(tmp_path / "nope")])
    assert result.exit_code != 0
    assert "nothing published" in result.output


def test_freshness_fails_when_a_map_layer_is_not_published(runner, tmp_path):
    """A missing layer leaves the map looking healthy but blank."""
    site = write_site(tmp_path, with_layers=False)
    result = runner.invoke(main, ["freshness", "--site-data", str(site)])
    assert result.exit_code != 0
    assert "map layers missing" in result.output
    assert "gauges.geojson" in result.output
