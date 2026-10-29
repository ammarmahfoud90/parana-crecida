"""Catalogue paging and feature parsing.

The paging tests encode a real server quirk: ``offset`` is sometimes ignored,
so pages overlap. "A short page means the end" is therefore not a sufficient
stop condition - "no ids we have not already seen" is.
"""

from __future__ import annotations

from datetime import date

from a5fakes import FakeResponse

from ina_a5 import coords_ok, fetch_catalogue, flatten, live_series, parse_feature, pick
from ina_a5.catalogue import iter_features

# Corrientes, roughly.
LON, LAT = -58.83, -27.47


def feature(series_id, *, var_id=39, lon=LON, lat=LAT, **props):
    base = {
        "id": series_id,
        "var_id": var_id,
        "nombre": f"station-{series_id}",
        "estacion_id": f"est-{series_id}",
    }
    base.update(props)
    return {"type": "Feature", "properties": base, "geometry": {"coordinates": [lon, lat]}}


def page(*ids, **kwargs):
    return FakeResponse(200, {"features": [feature(i, **kwargs) for i in ids]})


# -- parsing ---------------------------------------------------------------


def test_parse_feature_reads_the_obvious_fields():
    series = parse_feature(
        feature(26261, rio="Parana", red="alturas_oficial")
    )
    assert series.series_id == "26261"
    assert series.station == "station-26261"
    assert series.river == "Parana"
    assert series.var_id == 39
    assert series.network == "alturas_oficial"
    assert (series.lon, series.lat) == (LON, LAT)


def test_parse_feature_reads_nested_date_range():
    """``date_range`` is nested, which is what ``flatten``/``pick`` are for."""
    raw = feature(26261)
    raw["properties"]["date_range"] = {
        "timestart": "1905-01-01T09:00:00",
        "timeend": "2026-10-20T09:00:00",
    }
    series = parse_feature(raw)
    assert series.cat_start == date(1905, 1, 1)
    assert series.cat_end == date(2026, 10, 20)


def test_parse_feature_survives_missing_geometry():
    series = parse_feature({"properties": {"id": 1, "var_id": 39}})
    assert series.lon is None and series.lat is None
    assert not series.has_coords


def test_series_family_and_unit():
    assert parse_feature(feature(1, var_id=39)).family == "level"
    assert parse_feature(feature(1, var_id=39)).unit == "m"
    assert parse_feature(feature(1, var_id=40)).family == "flow"
    assert parse_feature(feature(1, var_id=48)).family is None


def test_flatten_and_pick():
    flat = flatten({"a": 1, "b": {"c": 2, "d": {"e": 3}}})
    assert flat == {"a": 1, "b.c": 2, "b.d.e": 3}
    assert pick(flat, "c") == 2
    assert pick(flat, "missing", "e") == 3
    assert pick(flat, "nope", default="fallback") == "fallback"


def test_coords_ok_rejects_null_island_and_far_away():
    assert coords_ok(parse_feature(feature(1)))
    assert not coords_ok(parse_feature(feature(1, lon=0.0, lat=0.0)))
    assert not coords_ok(parse_feature(feature(1, lon=2.35, lat=48.85)))  # Paris


# -- paging ----------------------------------------------------------------


def test_paging_follows_offsets_until_a_short_page(make_client):
    client = make_client([page(1, 2, 3), page(4, 5)], attempts=1)
    ids = [parse_feature(f).series_id for f in iter_features(client, page_size=3)]
    assert ids == ["1", "2", "3", "4", "5"]
    offsets = [params["offset"] for _, params in client.session.calls]
    assert offsets == [0, 3]


def test_overlapping_pages_are_deduplicated(make_client):
    """Page two repeats id 3; it must be yielded once and paging must go on."""
    client = make_client([page(1, 2, 3), page(3, 4, 5), page(6, 7)], attempts=1)
    ids = [parse_feature(f).series_id for f in iter_features(client, page_size=3)]
    assert ids == ["1", "2", "3", "4", "5", "6", "7"]


def test_paging_stops_when_a_page_brings_nothing_new(make_client):
    """The offset is being ignored - a full page of repeats means stop."""
    client = make_client([page(1, 2, 3), page(1, 2, 3)], attempts=1)
    ids = [parse_feature(f).series_id for f in iter_features(client, page_size=3)]
    assert ids == ["1", "2", "3"]
    assert len(client.session.calls) == 2  # did not loop forever


def test_paging_stops_on_an_empty_page(make_client):
    client = make_client([page(1, 2, 3), FakeResponse(200, {"features": []})], attempts=1)
    assert len(list(iter_features(client, page_size=3))) == 3


def test_hard_stop_bounds_the_walk(make_client):
    client = make_client([page(1, 2), page(3, 4)], attempts=1)
    ids = list(iter_features(client, page_size=2, hard_stop=4))
    assert len(ids) == 4


# -- fetch_catalogue -------------------------------------------------------


def test_fetch_catalogue_keeps_only_hydro_series_in_bbox(make_client):
    client = make_client(
        [
            FakeResponse(
                200,
                {
                    "features": [
                        feature(1, var_id=39),            # level, keep
                        feature(2, var_id=40),            # flow, keep
                        feature(3, var_id=48),            # rainfall, drop
                        feature(4, var_id=39, lon=2.35, lat=48.85),  # off-bbox
                        feature(5, var_id=39, lon=0.0, lat=0.0),     # null island
                    ]
                },
            )
        ],
        attempts=1,
    )
    kept = fetch_catalogue(client, page_size=10)
    assert [s.series_id for s in kept] == ["1", "2"]


def test_fetch_catalogue_can_keep_everything(make_client):
    client = make_client(
        [FakeResponse(200, {"features": [feature(1, var_id=48)]})], attempts=1
    )
    kept = fetch_catalogue(client, hydro_only=False, in_bbox=False, page_size=10)
    assert [s.series_id for s in kept] == ["1"]


def test_live_series_filters_on_catalogue_end():
    today = date(2026, 10, 28)
    # A series with no date_range at all cannot be called live.
    assert live_series([parse_feature(feature(1))], today=today) == []

    raw = feature(2)
    raw["properties"]["date_range"] = {"timeend": "2026-10-25T09:00:00"}
    stale = feature(3)
    stale["properties"]["date_range"] = {"timeend": "2026-01-01T09:00:00"}
    catalogue = [parse_feature(raw), parse_feature(stale)]
    assert [s.series_id for s in live_series(catalogue, today=today)] == ["2"]
