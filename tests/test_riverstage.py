"""The river coloured by stage, day by day.

This is the map's default layer and the only one that moves every day, so
what is tested here is the geometry it is painted on - that distance along
the line agrees with the chain, that the line never runs backwards, and that
the break at Yacyreta is really there - plus the two rules about which
gauges may colour the river and over what days.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from parana_crecida import chain as ch
from parana_crecida import flooded as fl
from parana_crecida import riverstage as rs

CORRIDORS = fl.load_corridors()
needs_corridors = pytest.mark.skipif(not CORRIDORS, reason="no committed corridors")


# -- walking the line ------------------------------------------------------


def test_walk_km_accumulates():
    run = rs.walk_km([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
    assert run[0] == 0.0
    assert run[1] == pytest.approx(rs.KM_PER_DEG_LON)
    assert run[2] == pytest.approx(rs.KM_PER_DEG_LON + rs.KM_PER_DEG_LAT)


def test_walk_km_of_a_single_point_is_zero():
    assert rs.walk_km([[0.0, 0.0]]) == [0.0]


@needs_corridors
def test_the_coloured_line_stops_where_the_gauges_do():
    """No colour below the last gauge that can anchor it.

    The Delta corridor is 191 km of river that the radar layer covers and
    the daily layer cannot: its downstream gauge at Ibicuy is tide-affected
    and may not anchor a percentile. Drawing it anyway would stretch the
    last upstream value over the whole delta, which would look like
    measurement and be nothing of the kind.
    """
    line = rs.centreline_with_km()
    assert line[-1].river_km == pytest.approx(1440.5, abs=1), (
        "the line should stop at Villa Constitucion"
    )
    assert any(c.key == "villa_constitucion_ibicuy" for c in CORRIDORS), (
        "this test is meaningless without a Delta corridor to leave out"
    )


@needs_corridors
def test_the_line_runs_the_whole_river_without_doubling_back():
    line = rs.centreline_with_km()
    assert len(line) > 500
    assert all(b.river_km >= a.river_km for a, b in zip(line, line[1:]))
    assert line[0].river_km == pytest.approx(0.0, abs=1)
    # Villa Constitucion on the frozen chain.
    assert line[-1].river_km == pytest.approx(1440, abs=5)


@needs_corridors
def test_every_gauge_lands_exactly_on_its_own_anchor():
    """The reason each stretch is scaled rather than accumulated.

    Walking the vertices gives up to 10 km more per stretch than the chain's
    gauge spacing. Accumulated, that carries a stretch past where the next
    one begins and leaves the gauges a few km off their own line.
    """
    import math

    line = rs.centreline_with_km()
    nodes = {n.series_id: n for n in ch.load_chain()}
    end = line[-1].river_km
    for stretch in CORRIDORS:
        if stretch.to_id not in nodes:
            continue
        # Stretches below the end of the drawn line are not on it. The Delta
        # corridor exists for the radar overlay; no gauge below Villa
        # Constitucion can anchor a percentile, so no line is drawn there.
        if nodes[stretch.to_id].river_km > end + 0.5:
            continue
        end_lon, end_lat = stretch.centreline[-1]
        nearest = min(
            line,
            key=lambda v: math.hypot(
                (v.lon - end_lon) * rs.KM_PER_DEG_LON,
                (v.lat - end_lat) * rs.KM_PER_DEG_LAT,
            ),
        )
        assert nearest.river_km == pytest.approx(
            nodes[stretch.to_id].river_km, abs=0.5
        ), stretch.key


# -- the dam ---------------------------------------------------------------


@needs_corridors
def test_yacyreta_sits_between_posadas_and_ita_ibate():
    """The dam is placed from the line, so this checks it landed sensibly."""
    line = rs.centreline_with_km()
    nodes = {n.series_id: n for n in ch.load_chain()}
    (dam,) = rs.dam_positions(line)
    assert nodes["26256"].river_km < dam < nodes["26258"].river_km


def test_segments_are_cut_at_the_break():
    """One piece carries the break, and it is the one that crosses it."""
    line = [rs.Vertex(lon=-56.0 - i * 0.01, lat=-27.0, river_km=i) for i in range(40)]
    pieces = rs.segments(line, [12.0], step_km=5.0)
    flagged = [p for p in pieces if p["dam_break"]]
    assert len(flagged) == 1
    assert flagged[0]["km"] == pytest.approx(12.0, abs=5)


def test_segments_without_a_break_are_evenly_cut():
    line = [rs.Vertex(lon=-56.0 - i * 0.01, lat=-27.0, river_km=i) for i in range(41)]
    pieces = rs.segments(line, [], step_km=5.0)
    assert len(pieces) == 8
    assert not any(p["dam_break"] for p in pieces)
    assert all(len(p["points"]) > 1 for p in pieces)


def test_segments_keep_their_coordinates_as_lat_lon():
    """Leaflet wants [lat, lon]; the centreline is stored [lon, lat]."""
    line = [rs.Vertex(lon=-56.0, lat=-27.0, river_km=i) for i in range(12)]
    piece = rs.segments(line, [], step_km=5.0)[0]
    lat, lon = piece["points"][0]
    assert (lat, lon) == (-27.0, -56.0)


# -- which gauges may colour the river -------------------------------------


@dataclass(frozen=True, slots=True)
class FakeValue:
    day: date
    value: float


class FakeStore:
    """A store that answers for whichever gauges a test names."""

    def __init__(self, series: dict[str, list[FakeValue]]):
        self.series_map = series

    def series(self, series_id, *, since=None):
        return [
            v for v in self.series_map.get(series_id, []) if since is None or v.day >= since
        ]

    def last_data_day(self):
        return max(
            (v.day for values in self.series_map.values() for v in values), default=None
        )


class FakeClim:
    """Percentile is the level, so a test can read the arithmetic back."""

    def percentile_of(self, series_id, day, value):
        return value


@needs_corridors
def test_a_tide_affected_gauge_never_anchors_the_river():
    """A Delta gauge rises with the Plata, which is not a flood wave.

    Ibicuy and Canal Nuevo sit below the drawn line and are tide-affected;
    colouring the Parana with their percentile would show a tide as a wave.
    """
    days = [date(2026, 10, 20) + timedelta(days=i) for i in range(5)]
    store = FakeStore({
        sid: [FakeValue(d, 50.0) for d in days]
        for sid in ("26261", "26271", "26287", "26286")
    })
    payload = rs.build(store, FakeClim(), today=date(2026, 10, 25))
    named = {a["series_id"] for a in payload["anchors"]}
    assert "26261" in named  # Corrientes
    assert not named & {"26287", "26286"}, "a tide-affected gauge got through"


@needs_corridors
def test_the_day_axis_is_the_union_over_every_gauge():
    """Gauges do not all report to the same day.

    The top of the river often lags the rest by one. Taking the first
    gauge's days as the axis would drop the newest day from the whole river
    whenever that gauge is the one behind.
    """
    early = [date(2026, 10, 20) + timedelta(days=i) for i in range(4)]
    late = [date(2026, 10, 20) + timedelta(days=i) for i in range(5)]
    store = FakeStore({
        "26251": [FakeValue(d, 50.0) for d in early],   # Puerto Iguazu, a day behind
        "26271": [FakeValue(d, 50.0) for d in late],    # Parana
    })
    payload = rs.build(store, FakeClim(), today=date(2026, 10, 25))
    assert payload["days"][-1] == "2026-10-24"
    by_id = {a["series_id"]: a for a in payload["anchors"]}
    assert len(by_id["26251"]["pct"]) == len(payload["days"])
    assert by_id["26251"]["pct"][-1] is None, "the lagging gauge should read as a gap"
    assert by_id["26271"]["pct"][-1] == 50.0


@needs_corridors
def test_anchors_are_ordered_downstream():
    days = [date(2026, 10, 20) + timedelta(days=i) for i in range(3)]
    store = FakeStore({
        sid: [FakeValue(d, 50.0) for d in days]
        for sid in ("26271", "26261", "26265")
    })
    payload = rs.build(store, FakeClim(), today=date(2026, 10, 22))
    kms = [a["river_km"] for a in payload["anchors"]]
    assert kms == sorted(kms)


@needs_corridors
def test_build_says_where_the_line_is_cut():
    store = FakeStore({"26261": [FakeValue(date(2026, 10, 20), 50.0)]})
    payload = rs.build(store, FakeClim(), today=date(2026, 10, 20))
    (dam,) = payload["dams"]
    assert dam["name"] == "Yacyreta"
    assert any(p["dam_break"] for p in payload["segments"])


# -- the committed file ----------------------------------------------------

PUBLISHED = Path("site/data/river_stage.json")
published = pytest.mark.skipif(
    not PUBLISHED.exists(), reason="river stage has not been exported"
)


@published
def test_the_published_file_is_aligned():
    data = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    assert data["days"] == sorted(data["days"])
    for anchor in data["anchors"]:
        assert len(anchor["pct"]) == len(data["days"]), anchor["name"]
        assert len(anchor["value"]) == len(data["days"]), anchor["name"]
        assert anchor["lonlat"], f"{anchor['name']} has no position to draw"


@published
def test_every_published_percentile_is_a_percentile():
    data = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    for anchor in data["anchors"]:
        for value in anchor["pct"]:
            assert value is None or 0 <= value <= 100, anchor["name"]


@published
def test_no_published_anchor_sits_past_the_drawn_line():
    """An anchor beyond the line cannot colour anything on it."""
    data = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    end = max(s["km"] for s in data["segments"])
    for anchor in data["anchors"]:
        assert anchor["river_km"] <= end + 10, anchor["name"]
