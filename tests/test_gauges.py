"""The gauge roster: scope, tiers and trust flags."""

from __future__ import annotations

import pytest

from parana_crecida import gauges as roster


def test_every_series_id_is_unique():
    ids = [g.series_id for g in roster.GAUGES]
    assert len(ids) == len(set(ids))


def test_lookup_by_id():
    assert roster.gauge(26261).name == "Corrientes"
    assert roster.gauge("26261").name == "Corrientes"
    with pytest.raises(KeyError):
        roster.gauge(1)


def test_main_stem_runs_corrientes_to_villa_constitucion():
    """Downstream order, as the HydroRIVERS walk produced it."""
    assert roster.MAIN_STEM[0].name == "Corrientes"
    assert roster.MAIN_STEM[-1].name == "Villa Constitucion"


def test_main_stem_order_matches_the_known_sequence():
    expected = [
        "Corrientes",
        "Empedrado",
        "Bella Vista",
        "Goya",
        "Esquina",
        "La Paz",
        "Santa Elena",
        "Hernandarias",
        "Parana",
        "Diamante",
        "San Lorenzo",
        "Rosario",
        "Villa Constitucion",
    ]
    assert [g.name for g in roster.MAIN_STEM] == expected


def test_every_main_stem_gauge_is_tier_one():
    assert all(g.tier == 1 for g in roster.MAIN_STEM)


def test_routing_nodes_exclude_bad_snaps_and_flow_gauges():
    for g in roster.routing_nodes():
        assert not (g.flags & {"bad_snap", "unrouted", "flow"})
        assert g.tier == 1


def test_the_whole_main_stem_is_usable_for_routing():
    """If this fails, a tier-1 gauge has picked up a disqualifying flag."""
    assert len(roster.routing_nodes()) == len(roster.MAIN_STEM)


def test_delta_gauges_are_tier_two():
    assert all(g.tier == 2 for g in roster.DELTA)


def test_known_bad_snaps_are_flagged():
    """These six were snapped onto small tributary reaches by snap_v2."""
    for series_id in (26262, 26272, 26278, 26279, 26280, 26282):
        assert "bad_snap" in roster.gauge(series_id).flags


def test_estuary_gauges_are_flagged_unrouted():
    """No reach within radius, or inside snap_v2's Rio de la Plata box."""
    for series_id in (26281, 26283, 26284, 26291, 26294, 26296):
        assert "unrouted" in roster.gauge(series_id).flags


def test_scope_runs_from_puerto_iguazu_to_san_fernando():
    """The full main stem the project plan asks for."""
    assert roster.gauge(26251).name == "Puerto Iguazu"
    assert roster.gauge(26294).name == "San Fernando"


def test_every_tide_affected_gauge_is_below_villa_constitucion():
    """Nothing tidal may sit in the tier-1 routing chain."""
    assert not any("tide_affected" in g.flags for g in roster.MAIN_STEM)


def test_upstream_gauges_are_flagged_regulated():
    """Yacyreta operates the reach above the confluence."""
    for g in roster.UPSTREAM:
        if g.name != "Confluencia":
            assert "regulated" in g.flags


def test_flow_gauges_are_flagged():
    assert "flow" in roster.gauge(26685).flags
    assert "flow" in roster.gauge(26690).flags
    assert not roster.gauge(26685).is_routing_node


def test_delta_branch_gauges_name_their_branch():
    assert roster.gauge(26282).branch == roster.PALMAS
    assert roster.gauge(26287).branch == roster.IBICUY
    assert roster.gauge(26290).branch == roster.GUAZU


@pytest.mark.parametrize("tier", [1, 2, 3])
def test_series_ids_filters_by_tier(tier):
    ids = roster.series_ids(tiers=(tier,))
    assert ids
    assert all(roster.gauge(i).tier == tier for i in ids)


def test_series_ids_defaults_to_everything():
    assert len(roster.series_ids()) == len(roster.GAUGES)


def test_flags_are_from_the_documented_set():
    known = {"regulated", "tide_affected", "bad_snap", "unrouted", "flow"}
    for g in roster.GAUGES:
        assert g.flags <= known, f"{g.name} has an undocumented flag"


def test_branches_are_from_the_documented_set():
    known = {
        roster.MAIN,
        roster.PALMAS,
        roster.GUAZU,
        roster.IBICUY,
        roster.VICTORIA,
        roster.ESTUARY,
        roster.TRIBUTARY,
    }
    assert all(g.branch in known for g in roster.GAUGES)
