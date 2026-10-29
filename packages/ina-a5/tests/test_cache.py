"""Cache round-tripping, multi-root resolution and trimming."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ina_a5 import DailyValue, SeriesCache

# The rios-argentina publish copy, if this machine has it. Reading its real
# files is the only way to be sure the format is genuinely unchanged.
RIOS_CACHE = Path.home() / "rios-argentina" / "cache_recent"


def values(*pairs):
    return [DailyValue(day=date(*d), value=v) for d, v in pairs]


@pytest.fixture
def cache(tmp_path):
    return SeriesCache(tmp_path / "cache")


# -- round trip ------------------------------------------------------------


def test_write_then_read(cache):
    original = values(((2024, 1, 1), 2.5), ((2024, 1, 2), 2.7))
    cache.write(26261, original)
    assert cache.read(26261) == original


def test_on_disk_format_is_unchanged(cache, tmp_path):
    """Compact ``[[iso, value], ...]`` - what rios-argentina already writes."""
    cache.write(26261, values(((2024, 1, 1), 2.5)))
    text = (tmp_path / "cache" / "26261.json").read_text(encoding="utf-8")
    assert text == '[["2024-01-01",2.5]]'
    assert json.loads(text) == [["2024-01-01", 2.5]]


def test_values_are_sorted_on_write(cache):
    cache.write(26261, values(((2024, 1, 3), 3.0), ((2024, 1, 1), 1.0)))
    assert [v.day.day for v in cache.read(26261)] == [1, 3]


def test_missing_series_reads_empty(cache):
    assert cache.read(99999) == []
    assert cache.last_day(99999) is None


def test_corrupt_file_reads_empty_rather_than_exploding(cache, tmp_path):
    (tmp_path / "cache").mkdir(parents=True)
    (tmp_path / "cache" / "26261.json").write_text("{not json", encoding="utf-8")
    assert cache.read(26261) == []


def test_corrupt_rows_are_skipped(cache, tmp_path):
    (tmp_path / "cache").mkdir(parents=True)
    (tmp_path / "cache" / "26261.json").write_text(
        '[["2024-01-01",1.0],["nope",2.0],["2024-01-03","x"],["2024-01-04",4.0]]',
        encoding="utf-8",
    )
    assert [v.day.day for v in cache.read(26261)] == [1, 4]


def test_write_is_atomic(cache, tmp_path):
    """No ``.tmp`` left behind, so an interrupted run cannot poison the cache."""
    cache.write(26261, values(((2024, 1, 1), 1.0)))
    assert list((tmp_path / "cache").glob("*.tmp")) == []


# -- multiple roots --------------------------------------------------------


def test_read_falls_through_to_a_later_root(tmp_path):
    """The bug this fixes: build_daily only ever looked in ``cache/``."""
    full, recent = tmp_path / "cache", tmp_path / "cache_recent"
    SeriesCache(recent).write(26261, values(((2024, 1, 1), 1.0)))
    cache = SeriesCache([full, recent])
    assert len(cache.read(26261)) == 1


def test_the_first_root_wins(tmp_path):
    full, recent = tmp_path / "cache", tmp_path / "cache_recent"
    SeriesCache(full).write(26261, values(((2024, 1, 1), 1.0), ((2024, 1, 2), 2.0)))
    SeriesCache(recent).write(26261, values(((2024, 1, 2), 9.9)))
    assert len(SeriesCache([full, recent]).read(26261)) == 2


def test_writes_go_to_the_write_root(tmp_path):
    full, recent = tmp_path / "cache", tmp_path / "cache_recent"
    cache = SeriesCache([full, recent], write_root=full)
    cache.write(26261, values(((2024, 1, 1), 1.0)))
    assert (full / "26261.json").exists()
    assert not (recent / "26261.json").exists()


def test_series_ids_spans_roots_and_skips_metadata(tmp_path):
    full, recent = tmp_path / "cache", tmp_path / "cache_recent"
    SeriesCache(full).write(26261, values(((2024, 1, 1), 1.0)))
    SeriesCache(recent).write(26271, values(((2024, 1, 1), 1.0)))
    full.mkdir(exist_ok=True)
    (full / "_catalogue.json").write_text("{}", encoding="utf-8")
    assert SeriesCache([full, recent]).series_ids() == ["26261", "26271"]


def test_empty_roots_is_rejected():
    with pytest.raises(ValueError):
        SeriesCache([])


# -- updating --------------------------------------------------------------


def test_merge_lets_fresh_values_win(cache):
    """Why the backfill overlap is worth asking for: revisions land."""
    cache.write(26261, values(((2024, 1, 1), 1.0), ((2024, 1, 2), 2.0)))
    merged = cache.merge(26261, values(((2024, 1, 2), 2.5), ((2024, 1, 3), 3.0)))
    assert [(v.day.day, v.value) for v in merged] == [(1, 1.0), (2, 2.5), (3, 3.0)]


def test_merge_does_not_write(cache):
    cache.write(26261, values(((2024, 1, 1), 1.0)))
    cache.merge(26261, values(((2024, 1, 2), 2.0)))
    assert len(cache.read(26261)) == 1


def test_update_writes(cache):
    cache.write(26261, values(((2024, 1, 1), 1.0)))
    cache.update(26261, values(((2024, 1, 2), 2.0)))
    assert len(cache.read(26261)) == 2


def test_top_up_window_overlaps_the_last_cached_day(cache):
    cache.write(26261, values(((2024, 1, 10), 1.0)))
    start, end = cache.top_up_window(26261, date(2024, 1, 20))
    assert start == date(2023, 12, 31)  # 10 days before the last reading
    assert end == date(2024, 1, 20)


def test_top_up_window_cold_start(cache):
    start, end = cache.top_up_window(26261, date(2024, 1, 20), cold_start_days=30)
    assert start == date(2023, 12, 21)
    assert end == date(2024, 1, 20)


# -- trimming --------------------------------------------------------------


def test_trim_keeps_only_recent_days(tmp_path):
    source = SeriesCache(tmp_path / "cache")
    source.write(
        26261, values(((2015, 1, 1), 1.0), ((2023, 6, 1), 2.0), ((2024, 1, 1), 3.0))
    )
    dest = tmp_path / "cache_recent"
    written = source.trim(years=2.0, today=date(2024, 6, 1), dest=dest)
    assert written == {"26261": 2}
    assert [v.day.year for v in SeriesCache(dest).read(26261)] == [2023, 2024]


def test_trim_can_restrict_to_named_series(tmp_path):
    source = SeriesCache(tmp_path / "cache")
    source.write(26261, values(((2024, 1, 1), 1.0)))
    source.write(26271, values(((2024, 1, 1), 1.0)))
    dest = tmp_path / "cache_recent"
    written = source.trim(years=6.0, today=date(2024, 6, 1), keep=[26261], dest=dest)
    assert list(written) == ["26261"]


# -- compatibility with the real rios-argentina cache ----------------------


@pytest.mark.skipif(not RIOS_CACHE.is_dir(), reason="rios-argentina cache not present")
def test_reads_a_real_rios_argentina_cache_file():
    """The format claim, checked against the actual files rather than asserted."""
    cache = SeriesCache(RIOS_CACHE)
    ids = cache.series_ids()
    assert ids, "expected cached series in rios-argentina/cache_recent"
    for series_id in ids[:5]:
        read = cache.read(series_id)
        assert read, f"series {series_id} read back empty"
        assert all(isinstance(v.value, float) for v in read)
        assert read == sorted(read, key=lambda v: v.day)


@pytest.mark.skipif(not RIOS_CACHE.is_dir(), reason="rios-argentina cache not present")
def test_corrientes_history_is_readable():
    """Corrientes (26261) is the gauge every datum-seam story is about."""
    read = SeriesCache(RIOS_CACHE).read(26261)
    if not read:
        pytest.skip("26261 not in the trimmed cache")
    assert len(read) > 365
    assert all(-5.0 < v.value < 20.0 for v in read)  # metres of stage
