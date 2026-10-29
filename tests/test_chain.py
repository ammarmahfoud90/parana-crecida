"""The river chain: distance along the water, and what may join it."""

from __future__ import annotations

import json
from datetime import date

import pytest

from parana_crecida import chain as ch
from parana_crecida import gauges as roster

# One degree of longitude at the flat factor the walk uses.
DEG_KM = ch.KM_PER_DEG_LON


def line(x0: float) -> list[list[float]]:
    """A reach one degree of longitude long, starting at ``x0``."""
    return [[x0, 0.0], [x0 + 1.0, 0.0]]


@pytest.fixture
def synthetic(tmp_path):
    """Four reaches in a row, with a gauge on the first and the third."""
    network = {
        "min_order": 3,
        "reaches": [
            {"id": 1, "next": 2, "ord": 5, "up_skm": 100.0, "dis": 1, "pts": line(0)},
            {"id": 2, "next": 3, "ord": 5, "up_skm": 150.0, "dis": 1, "pts": line(1)},
            {"id": 3, "next": 4, "ord": 5, "up_skm": 200.0, "dis": 1, "pts": line(2)},
            {"id": 4, "next": 0, "ord": 5, "up_skm": 250.0, "dis": 1, "pts": line(3)},
        ],
    }
    snapped = [
        {"series_id": "26251", "reach": 1, "routed": True, "up_skm": 100.0},
        {"series_id": "26261", "reach": 3, "routed": True, "up_skm": 200.0},
    ]
    net_path = tmp_path / "network.json"
    snap_path = tmp_path / "snapped.json"
    net_path.write_text(json.dumps(network), encoding="utf-8")
    snap_path.write_text(json.dumps(snapped), encoding="utf-8")
    return net_path, snap_path


# -- geometry --------------------------------------------------------------


def test_reach_km_measures_along_the_polyline():
    assert ch.reach_km(line(0)) == pytest.approx(DEG_KM)
    assert ch.reach_km([[0, 0], [1, 0], [2, 0]]) == pytest.approx(2 * DEG_KM)


def test_reach_km_of_a_degenerate_reach_is_zero():
    assert ch.reach_km([[0, 0]]) == 0.0
    assert ch.reach_km([]) == 0.0


# -- derivation ------------------------------------------------------------


def test_derive_accumulates_distance_downstream(synthetic):
    nodes, _ = ch.derive(*synthetic, start_series_id="26251")
    assert [n.series_id for n in nodes] == ["26251", "26261"]
    assert nodes[0].river_km == 0.0
    # Two reaches between them, one degree each.
    assert nodes[1].river_km == pytest.approx(2 * DEG_KM, abs=0.1)


def test_derive_reports_gauges_that_are_not_on_the_walk(synthetic):
    _, off_chain = ch.derive(*synthetic, start_series_id="26251")
    assert "26276" in off_chain  # Rosario, not in the synthetic network
    assert "26251" not in off_chain


def test_derive_refuses_an_unroutable_start(synthetic):
    with pytest.raises(ValueError, match="not routed"):
        ch.derive(*synthetic, start_series_id="26276")


def test_an_untrusted_snap_is_kept_off_the_chain(tmp_path, synthetic):
    """Confluencia's real failure: snapped 660 km above its own junction.

    A bad snap can land squarely on the walk and look entirely plausible,
    so the filter is on the flag, not on the geometry.
    """
    net_path, _ = synthetic
    snapped = [
        {"series_id": "26251", "reach": 1, "routed": True, "up_skm": 100.0},
        # 26690 Confluencia is flagged bad_snap in the roster.
        {"series_id": "26690", "reach": 1, "routed": True, "up_skm": 100.0},
    ]
    snap_path = tmp_path / "snapped2.json"
    snap_path.write_text(json.dumps(snapped), encoding="utf-8")

    nodes, off_chain = ch.derive(net_path, snap_path, start_series_id="26251")
    assert [n.series_id for n in nodes] == ["26251"]
    assert "26690" in off_chain


def test_an_unrouted_gauge_is_kept_off_the_chain(tmp_path, synthetic):
    net_path, _ = synthetic
    snapped = [
        {"series_id": "26251", "reach": 1, "routed": True, "up_skm": 100.0},
        {"series_id": "26261", "reach": 3, "routed": False, "up_skm": 200.0},
    ]
    snap_path = tmp_path / "snapped3.json"
    snap_path.write_text(json.dumps(snapped), encoding="utf-8")
    nodes, off_chain = ch.derive(net_path, snap_path, start_series_id="26251")
    assert [n.series_id for n in nodes] == ["26251"]
    assert "26261" in off_chain


# -- validation ------------------------------------------------------------


def node(series_id, km, up_skm=100.0, reach=1):
    return ch.ChainNode(series_id, roster.gauge(series_id).name, km, reach, up_skm)


def test_validate_rejects_an_empty_chain():
    assert ch.validate_chain([]) == ["the chain is empty"]


def test_validate_rejects_a_gauge_that_is_not_downstream():
    problems = ch.validate_chain([node("26261", 100.0), node("26263", 50.0)])
    assert any("not downstream" in p for p in problems)


def test_validate_rejects_a_shrinking_catchment():
    """Catchment only grows downstream; a drop means the walk left the stem."""
    problems = ch.validate_chain(
        [node("26261", 100.0, up_skm=500.0), node("26263", 200.0, up_skm=100.0)]
    )
    assert any("catchment shrinks" in p for p in problems)


def test_validate_rejects_a_repeated_gauge():
    problems = ch.validate_chain([node("26261", 10.0), node("26261", 20.0)])
    assert any("more than once" in p for p in problems)


def test_validate_names_missing_routing_nodes():
    problems = ch.validate_chain([node("26261", 10.0)])
    assert any("routing nodes missing" in p for p in problems)


# -- legs ------------------------------------------------------------------


def test_legs_are_consecutive_pairs():
    nodes = [node("26261", 0.0), node("26263", 60.0), node("26264", 130.0)]
    pairs = ch.legs(nodes)
    assert [round(leg.km) for leg in pairs] == [60, 70]
    assert pairs[0].label == "Corrientes -> Empedrado"


def test_legs_can_be_restricted_before_pairing():
    """Restricting first, so a skipped gauge does not split one leg in two."""
    nodes = [node("26261", 0.0), node("26263", 60.0), node("26264", 130.0)]
    pairs = ch.legs(nodes, only={"26261", "26264"})
    assert len(pairs) == 1
    assert round(pairs[0].km) == 130


# -- round trip ------------------------------------------------------------


def test_write_then_load(tmp_path):
    nodes = [node("26261", 0.0), node("26263", 60.0)]
    path = tmp_path / "chain.json"
    ch.write_chain(path, nodes, ["26276"], derived_on=date(2026, 10, 28))
    loaded = ch.load_chain(path)
    assert [n.series_id for n in loaded] == ["26261", "26263"]
    assert loaded[1].river_km == 60.0
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["derived_on"] == "2026-10-28"
    assert payload["off_chain"] == ["26276"]


def test_load_of_a_missing_chain_is_empty_not_an_error(tmp_path):
    assert ch.load_chain(tmp_path / "nope.json") == []


def test_load_sorts_by_river_km(tmp_path):
    path = tmp_path / "chain.json"
    ch.write_chain(path, [node("26263", 60.0), node("26261", 0.0)], [])
    assert [n.series_id for n in ch.load_chain(path)] == ["26261", "26263"]


# -- the committed chain ---------------------------------------------------

COMMITTED = ch.load_chain()


@pytest.mark.skipif(not COMMITTED, reason="no committed chain")
def test_the_committed_chain_is_sound():
    assert ch.validate_chain(COMMITTED) == []


@pytest.mark.skipif(not COMMITTED, reason="no committed chain")
def test_the_committed_chain_runs_from_puerto_iguazu():
    assert COMMITTED[0].series_id == "26251"
    assert COMMITTED[0].river_km == 0.0


@pytest.mark.skipif(not COMMITTED, reason="no committed chain")
@pytest.mark.parametrize(
    "upstream,downstream,real_km",
    [
        ("26261", "26276", 730),  # Corrientes -> Rosario
        ("26261", "26271", 570),  # Corrientes -> Parana
    ],
)
def test_committed_distances_match_the_real_river(upstream, downstream, real_km):
    """Checked against published river distances, not against itself."""
    by_id = {n.series_id: n for n in COMMITTED}
    measured = by_id[downstream].river_km - by_id[upstream].river_km
    assert measured == pytest.approx(real_km, abs=25)


@pytest.mark.skipif(not COMMITTED, reason="no committed chain")
def test_every_routing_node_is_on_the_committed_chain():
    on_chain = {n.series_id for n in COMMITTED}
    assert {g.series_id for g in roster.routing_nodes()} <= on_chain


@pytest.mark.skipif(not COMMITTED, reason="no committed chain")
def test_the_routing_legs_cover_corrientes_to_villa_constitucion():
    pairs = ch.routing_legs(COMMITTED)
    assert pairs[0].upstream.name == "Corrientes"
    assert pairs[-1].downstream.name == "Villa Constitucion"
    assert sum(leg.km for leg in pairs) == pytest.approx(781, abs=20)
