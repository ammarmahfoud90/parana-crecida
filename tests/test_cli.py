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
        "catalogue",
        "chain",
        "climatology",
        "lags",
        "oni",
        "thresholds",
        "fetch",
        "load",
        "validate",
        "export",
        "daily",
        "guards",
    }


def test_version(runner):
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output
