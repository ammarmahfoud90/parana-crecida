"""The gauges in scope: Confluencia to the Delta.

This is the scope definition, not the river chain. It says which series the
tracker reads and how much to trust each one. Downstream order and river-km
live in ``data/parana_chain.json``, which is derived from the HydroRIVERS
topology and then reviewed by hand - see the README.

Tiers come from how usable each gauge is for travel-time work:

``1``
    Main stem, Corrientes to Villa Constitucion. Correctly snapped, long
    records, free-flowing. Celerity is fitted on these and arrival times are
    published as forecasts.
``2``
    Delta and lower reach. Tracked and charted, but tide- and Rio de la
    Plata-backwater-affected, and the braided channels are a single
    centerline in HydroRIVERS. No celerity fit until validated.
``3``
    Context only. Upstream of the confluence (regulated by Yacyreta), or off
    the main stem. Useful as lead indicators, never as a routing node.

Flags record what is known to be wrong, so no analysis silently relies on a
gauge that cannot carry it:

``regulated``
    Downstream of Yacyreta; stage reflects dam operation as well as runoff.
``tide_affected``
    Delta or estuary; a crest here is partly not a river signal.
``bad_snap``
    ``snap_v2.py`` put this gauge on a small tributary reach, not the main
    stem. Needs a manual reach override before it can be used for river-km.
``unrouted``
    ``routed=False`` in ``stations_snapped.json`` - no reach within radius,
    or inside the Rio de la Plata box.
``flow``
    Discharge (m3/s), not stage (m).
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAIN = "main"
PALMAS = "palmas"
GUAZU = "guazu"
IBICUY = "ibicuy"
VICTORIA = "victoria"
ESTUARY = "estuary"
TRIBUTARY = "tributary"


@dataclass(frozen=True, slots=True)
class Gauge:
    """One gauge in scope.

    ``name`` is ASCII and is what the CLI prints: a Windows console is
    cp1252 and an accent there is a crash, not a cosmetic problem.
    ``display`` carries the real Spanish spelling and is what the published
    site shows. Only gauges whose names differ set it.
    """

    series_id: str
    name: str
    branch: str = MAIN
    tier: int = 3
    flags: frozenset[str] = field(default_factory=frozenset)
    note: str = ""
    display: str = ""

    @property
    def label(self) -> str:
        """The name a reader should see."""
        return self.display or self.name

    @property
    def is_routing_node(self) -> bool:
        """True when this gauge may be used as a travel-time node."""
        return self.tier == 1 and not (self.flags & {"bad_snap", "unrouted", "flow"})


def _g(series_id, name, branch=MAIN, tier=3, flags=(), note="", display="") -> Gauge:
    return Gauge(str(series_id), name, branch, tier, frozenset(flags), note, display)


#: Tier 1: the main-stem routing chain, in downstream order.
#: Order here is from the HydroRIVERS downstream walk and is asserted by the
#: tests; the authoritative river-km is in ``data/parana_chain.json``.
MAIN_STEM = [
    _g(26261, "Corrientes", tier=1, note="126-year record; known datum seam"),
    _g(26263, "Empedrado", tier=1),
    _g(26264, "Bella Vista", tier=1),
    _g(26265, "Goya", tier=1, note="124-year record"),
    _g(26267, "Esquina", tier=1),
    _g(26268, "La Paz", tier=1, note="121-year record"),
    _g(26269, "Santa Elena", tier=1),
    _g(26270, "Hernandarias", tier=1),
    _g(26271, "Parana", tier=1, note="125-year record", display="Paraná"),
    _g(26273, "Diamante", tier=1, note="125-year record"),
    _g(26275, "San Lorenzo", tier=1),
    _g(26276, "Rosario", tier=1, note="143-year record, the longest in scope"),
    _g(26277, "Villa Constitucion", tier=1, note="last reliably routed gauge", display="Villa Constitución"),
]

#: Tier 2: below Villa Constitucion. Tracked, not trusted for celerity.
DELTA = [
    # INA publishes alert and evacuation levels for Victoria and calls its
    # river "Parana/Delta", so it belongs in scope even though it sits on a
    # right-bank delta channel rather than the stem.
    _g(26274, "Victoria", VICTORIA, 2, (), "Riacho Victoria, opposite Rosario"),
    _g(26278, "San Nicolas", tier=2, flags=("bad_snap",), display="San Nicolás"),
    _g(26279, "Ramallo", tier=2, flags=("bad_snap",)),
    _g(26280, "San Pedro", tier=2, flags=("bad_snap",)),
    _g(26281, "Baradero", TRIBUTARY, 2, ("unrouted",), "on the Rio Baradero"),
    _g(26282, "Zarate", PALMAS, 2, ("bad_snap", "tide_affected"), display="Zárate"),
    _g(26283, "Campana", PALMAS, 2, ("unrouted", "tide_affected")),
    _g(26284, "Escobar", PALMAS, 2, ("unrouted", "tide_affected")),
    _g(26286, "Canal Nuevo", IBICUY, 2, ("tide_affected",)),
    _g(26287, "Ibicuy", IBICUY, 2, ("tide_affected",)),
    _g(26290, "Guazucito", GUAZU, 2, ("tide_affected",)),
    # The estuary mouth. Levels here are driven by the Rio de la Plata and
    # the wind as much as by the river, which is why they are the downstream
    # boundary rather than a routing node.
    _g(26291, "Tigre", ESTUARY, 2, ("unrouted", "tide_affected"), "Rio Lujan"),
    _g(26294, "San Fernando", ESTUARY, 2, ("unrouted", "tide_affected"), "Rio Lujan"),
    _g(26296, "Olivos", ESTUARY, 2, ("unrouted", "tide_affected"), "Rio de la Plata"),
]

#: Tier 3: upstream of the confluence. Lead indicators, and regulated.
#: Puerto Iguazu down to Santa Ana sits between Itaipu and Yacyreta; Posadas
#: to Paso de la Patria sits below Yacyreta. All of it is dam-operated.
UPSTREAM = [
    _g(26251, "Puerto Iguazu", tier=3, flags=("regulated",), note="upstream boundary", display="Puerto Iguazú"),
    _g(26252, "Libertad", tier=3, flags=("regulated",)),
    _g(26253, "El Dorado", tier=3, flags=("regulated",)),
    _g(26254, "Libertador", tier=3, flags=("regulated",)),
    _g(26255, "Santa Ana", tier=3, flags=("regulated",)),
    _g(26256, "Posadas", tier=3, flags=("regulated",)),
    _g(26257, "Ituzaingo", tier=3, flags=("regulated", "unrouted"), display="Ituzaingó"),
    _g(26258, "Ita Ibate", tier=3, flags=("regulated",), display="Itá Ibaté"),
    _g(26259, "Itati", tier=3, flags=("regulated",), display="Itatí"),
    _g(26260, "Paso de la Patria", tier=3, flags=("regulated",)),
    _g(26685, "Yacyreta efluente", tier=3, flags=("regulated", "flow"), display="Yacyretá efluente"),
    # Snapped onto Puerto Iguazu's reach, about 660 km upstream of the
    # junction it is named after, so it carries no usable river position.
    _g(
        26690, "Confluencia", tier=3, flags=("flow", "bad_snap"),
        note="Parana/Paraguay junction; snapped ~660 km upstream",
    ),
]

#: Tier 3: off the main stem, but they explain what the main stem does.
TRIBUTARIES = [
    _g(26262, "Barranqueras", TRIBUTARY, 3, ("bad_snap",), "Chaco side at Corrientes"),
    _g(26266, "Reconquista", TRIBUTARY, 3),
    _g(26272, "Santa Fe", TRIBUTARY, 3, ("bad_snap",), "snapped off the main stem"),
    # Discharge, not stage: the record runs 500-12,600, which is the
    # Paraguay's flow in m3/s and not metres on any datum. INA's daily
    # report lists a *level* for Puerto Pilcomayo, so its official
    # thresholds belong to a stage series whose id we do not have yet.
    _g(26652, "Puerto Pilcomayo", TRIBUTARY, 3, ("flow",), "Paraguay, discharge"),
    _g(26442, "Puerto Formosa", TRIBUTARY, 3, ("bad_snap",), "Paraguay"),
    _g(26300, "Bermejo", TRIBUTARY, 3),
]

GAUGES: list[Gauge] = [*UPSTREAM, *MAIN_STEM, *DELTA, *TRIBUTARIES]

BY_ID: dict[str, Gauge] = {g.series_id: g for g in GAUGES}


def gauge(series_id: str | int) -> Gauge:
    """Look up one gauge. Raises ``KeyError`` if it is not in scope."""
    return BY_ID[str(series_id)]


def series_ids(*, tiers: tuple[int, ...] = (1, 2, 3)) -> list[str]:
    """Series ids in scope, optionally limited to some tiers."""
    return [g.series_id for g in GAUGES if g.tier in tiers]


def routing_nodes() -> list[Gauge]:
    """The gauges travel time may be fitted between, in downstream order."""
    return [g for g in MAIN_STEM if g.is_routing_node]


def main_stem_sequence() -> list[Gauge]:
    """Every stage gauge from Puerto Iguazu to San Fernando, in river order.

    This is the display order for the "river today" view: one pass down the
    river. Flow gauges are left out because m3/s does not belong on a stage
    profile, and the tributaries are not on the stem at all.
    """
    return [
        g
        for g in (*UPSTREAM, *MAIN_STEM, *DELTA)
        if "flow" not in g.flags
    ]
