"""The downstream chain: which gauge is below which, and how far apart.

Travel time is meaningless without distance, and distance along a river is
not distance on a map. This module walks the HydroRIVERS topology that
rios-argentina prepared (``network.json``) from the most upstream gauge in
scope, accumulating reach geometry, and records where each gauge falls.

**The result is derived once and committed**, to ``data/parana_chain.json``.
Everything downstream reads the committed file; re-deriving is an explicit
command whose output you diff. A chain that silently reordered itself
between runs would quietly change every travel time on the site.

Two things to hold on to about the numbers:

* They are approximate. The geometry is simplified to 0.002 degrees and the
  degree-to-km factors are flat. Checked against known distances they come
  out well - Corrientes to Rosario at 731 km against a real 730, Corrientes
  to Parana at 569 against 570 - which is accurate enough for celerity and
  not accurate enough to quote to the kilometre.
* A gauge that is absent is absent on purpose. ``snap_v2`` could not route
  the estuary group, and it put six gauges on small tributary reaches. None
  of those is in the tier-1 routing chain, so the chain that carries the
  travel-time work is clean; the rest are recorded as off-chain rather than
  given a plausible-looking number.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from . import gauges as roster

log = logging.getLogger(__name__)

DEFAULT_CHAIN = Path("data/parana_chain.json")

#: Flat degree-to-kilometre factors, as rios-argentina's propagate.py uses.
#: HydroRIVERS' own LENGTH_KM was not carried into network.json.
KM_PER_DEG_LON = 96.0
KM_PER_DEG_LAT = 110.57

#: The walk starts at the most upstream gauge in scope.
DEFAULT_START = "26251"  # Puerto Iguazu

#: Guard against a cycle in the topology, which prep_network reported none of.
MAX_REACHES = 5000

SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ChainNode:
    """One gauge's place on the river."""

    series_id: str
    name: str
    river_km: float
    reach_id: int
    #: Upstream catchment area in km2 at that reach, straight from HydroRIVERS.
    #: It should only ever grow downstream, which is a cheap ordering check.
    up_skm: float

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Leg:
    """The stretch between two neighbouring gauges on the chain."""

    upstream: ChainNode
    downstream: ChainNode

    @property
    def km(self) -> float:
        return self.downstream.river_km - self.upstream.river_km

    @property
    def label(self) -> str:
        return f"{self.upstream.name} -> {self.downstream.name}"


def reach_km(points: list[list[float]]) -> float:
    """Length of one reach's polyline, in kilometres."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx = (x2 - x1) * KM_PER_DEG_LON
        dy = (y2 - y1) * KM_PER_DEG_LAT
        total += (dx * dx + dy * dy) ** 0.5
    return total


def derive(
    network_path: Path | str,
    snapped_path: Path | str,
    *,
    start_series_id: str = DEFAULT_START,
) -> tuple[list[ChainNode], list[str]]:
    """Walk downstream from ``start_series_id``.

    Returns the gauges in scope that lie on the walk, in river order, and
    the series ids of those in scope that do not.
    """
    network = json.loads(Path(network_path).read_text(encoding="utf-8"))
    snapped = json.loads(Path(snapped_path).read_text(encoding="utf-8"))

    reaches = {r["id"]: r for r in network["reaches"]}
    in_scope = {g.series_id for g in roster.GAUGES}
    # Only gauges whose snap we trust may take a position on the river. A
    # bad snap can still land on the walk and look perfectly reasonable:
    # Confluencia is snapped to Puerto Iguazu's reach, 660 km above the
    # junction it is named after, and would otherwise enter the chain at
    # km 0. Flagged gauges fall through to off_chain with no river-km.
    untrusted = {"bad_snap", "unrouted"}
    trusted = {g.series_id for g in roster.GAUGES if not (g.flags & untrusted)}

    gauges_on_reach: dict[int, list[dict[str, Any]]] = {}
    start_reach: int | None = None
    for station in snapped:
        series_id = str(station.get("series_id"))
        if not station.get("routed") or station.get("reach") not in reaches:
            continue
        if series_id == start_series_id:
            start_reach = station["reach"]
        if series_id in trusted:
            gauges_on_reach.setdefault(station["reach"], []).append(station)

    if start_reach is None:
        raise ValueError(
            f"start gauge {start_series_id} is not routed to a reach in the network"
        )

    nodes: list[ChainNode] = []
    found: set[str] = set()
    cursor: int | None = start_reach
    km = 0.0
    visited: set[int] = set()

    while cursor and cursor in reaches and cursor not in visited:
        visited.add(cursor)
        if len(visited) > MAX_REACHES:
            raise RuntimeError("walk exceeded the reach limit; topology may cycle")
        for station in gauges_on_reach.get(cursor, []):
            series_id = str(station["series_id"])
            if series_id in found:
                continue
            found.add(series_id)
            nodes.append(
                ChainNode(
                    series_id=series_id,
                    name=roster.gauge(series_id).name,
                    river_km=round(km, 1),
                    reach_id=int(cursor),
                    up_skm=float(station.get("up_skm") or 0.0),
                )
            )
        km += reach_km(reaches[cursor]["pts"])
        cursor = reaches[cursor].get("next")

    off_chain = sorted(in_scope - found)
    log.info("chain: %d gauges over %.0f km, %d off-chain", len(nodes), km, len(off_chain))
    return nodes, off_chain


def write_chain(
    path: Path,
    nodes: list[ChainNode],
    off_chain: list[str],
    *,
    derived_on: date | None = None,
    source: str = "HydroRIVERS v10 via rios-argentina network.json",
) -> None:
    """Freeze the chain. Pretty-printed so a re-derivation diffs readably."""
    payload = {
        "schema": SCHEMA_VERSION,
        "derived_on": (derived_on or date.today()).isoformat(),
        "source": source,
        "note": (
            "River-km are approximate: simplified geometry and flat "
            "degree-to-km factors. Good to a few km, not to the kilometre."
        ),
        "nodes": [n.to_json() for n in nodes],
        "off_chain": off_chain,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def load_chain(path: Path | str = DEFAULT_CHAIN) -> list[ChainNode]:
    """The frozen chain, in river order. Empty if it has not been derived."""
    path = Path(path)
    if not path.exists():
        log.warning("no chain at %s; travel time is unavailable", path)
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    nodes = [
        ChainNode(
            series_id=str(n["series_id"]),
            name=n["name"],
            river_km=float(n["river_km"]),
            reach_id=int(n["reach_id"]),
            up_skm=float(n["up_skm"]),
        )
        for n in payload.get("nodes", [])
    ]
    nodes.sort(key=lambda n: n.river_km)
    return nodes


def validate_chain(nodes: list[ChainNode]) -> list[str]:
    """Problems with a chain, as readable lines. Empty means it is sound."""
    problems: list[str] = []
    if not nodes:
        return ["the chain is empty"]

    for a, b in zip(nodes, nodes[1:]):
        if b.river_km <= a.river_km:
            problems.append(
                f"{b.name} at {b.river_km} km is not downstream of "
                f"{a.name} at {a.river_km} km"
            )
        # Catchment can only grow downstream; a drop means the walk left the
        # stem or two gauges are on the wrong reaches.
        if b.up_skm < a.up_skm:
            problems.append(
                f"catchment shrinks from {a.name} ({a.up_skm:.0f} km2) to "
                f"{b.name} ({b.up_skm:.0f} km2)"
            )

    seen = [n.series_id for n in nodes]
    if len(seen) != len(set(seen)):
        problems.append("a gauge appears more than once")

    missing = [
        g.name for g in roster.routing_nodes() if g.series_id not in set(seen)
    ]
    if missing:
        problems.append("routing nodes missing from the chain: " + ", ".join(missing))

    return problems


def legs(nodes: list[ChainNode], *, only: set[str] | None = None) -> list[Leg]:
    """Consecutive pairs along the chain.

    ``only`` restricts to a set of series ids first, so travel time can be
    fitted over the tier-1 gauges alone without the chain pretending the
    off-chain ones are not there.
    """
    chosen = [n for n in nodes if only is None or n.series_id in only]
    return [Leg(a, b) for a, b in zip(chosen, chosen[1:])]


def routing_legs(nodes: list[ChainNode]) -> list[Leg]:
    """The legs travel time may be fitted over: tier-1, correctly snapped."""
    return legs(nodes, only={g.series_id for g in roster.routing_nodes()})
