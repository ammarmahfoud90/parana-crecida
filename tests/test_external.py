"""The checks that need Earth Engine, kept out of the automated suite.

These are the measurements the audit could not make. They are written
rather than described so that the next person with a credential runs a
command instead of reconstructing a query, and so the automated suite
stays offline and deterministic.

    pytest -m needs_ee --run-external

Each one answers a question `AUDIT/05_defect_register.md` lists as
unconfirmed. None of them is a unit test: they are instruments, and they
report numbers as well as passing or failing.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.needs_ee


@pytest.fixture(scope="module")
def ee_ready():
    from parana_crecida import rainfall as rf

    rf.initialize(None)
    return True


def test_the_union_is_smaller_than_the_sum(ee_ready):
    """U3: measure the double counting rather than estimating it.

    The corridors are 20 km buffers around centrelines that meet at a
    gauge, so neighbours overlap. The audit measured the overlap of the
    *polygons* offline at 9,889 km2, 17.7% of their summed area, and
    estimated the overlapping *water* at roughly 700 km2 by assuming it is
    spread evenly along each reach - which it is not, least of all where
    the Yacyreta reservoir sits. This settles it.
    """
    from parana_crecida import flooded as fl
    from parana_crecida import floodwater as fw
    from parana_crecida.riverfilm import published_keys

    keys = published_keys()
    stretches = [s for s in fl.load_corridors() if s.key in keys]
    end = date.today() - timedelta(days=fl.LAG_DAYS)
    start = end - timedelta(days=fw.WINDOW_DAYS - 1)

    union = fw.union_geometry(stretches)
    over_union = fw.area_km2(union, fw.water_mask(union, start, end))
    summed = 0.0
    for stretch in stretches:
        corridor = fl.corridor_geometry(stretch)
        km2 = fw.area_km2(corridor, fw.water_mask(corridor, start, end))
        assert km2 is not None, f"{stretch.key} was not observed"
        summed += km2

    print(f"\nunion {over_union:,.0f} km2 vs sum {summed:,.0f} km2 "
          f"({100 * (summed / over_union - 1):.1f}% inflation)")
    assert over_union is not None
    assert over_union < summed, "the corridors are supposed to overlap"


def test_every_published_cycle_was_actually_observed(ee_ready):
    """U2 and the coverage floor, over the whole published series.

    floodwater now refuses a cycle below MIN_COVERAGE, but the committed
    series predates that test. This measures the observed share of every
    corridor for every cycle already published, which is the denominator
    the series has never carried.
    """
    from parana_crecida import flooded as fl
    from parana_crecida import floodwater as fw

    payload = fw.load()
    thin = []
    for row in payload.get("stretches", []):
        stretch = next(s for s in fl.load_corridors() if s.key == row["key"])
        corridor = fl.corridor_geometry(stretch)
        for day in row.get("days", []):
            end = date.fromisoformat(day)
            start = end - timedelta(days=fw.WINDOW_DAYS - 1)
            seen = fw.observed_km2(corridor, start, end)
            share = (seen or 0) / (row["corridor_km2"] or 1)
            if share < fw.MIN_COVERAGE:
                thin.append(f"{row['key']}@{day}: {share:.0%}")
    print(f"\n{len(thin)} published cycle(s) below the coverage floor")
    for line in thin:
        print(f"  {line}")
    assert not thin, "published cycles were measured over partial coverage"


def test_what_happened_on_the_cycle_that_failed(ee_ready):
    """U1: missing acquisitions, or a mask that failed?

    The cycle ending 2026-06-30 lost 750 km2 of permanent channel, a
    9.8-sigma outlier confined to two adjacent corridors. The guards added
    in stage 6 reject it either way, which is correct and is not the same
    as knowing why. This reports the scenes and the coverage.
    """
    from parana_crecida import flooded as fl
    from parana_crecida import floodwater as fw

    end, suspects = date(2026, 6, 30), ("posadas_ita_ibate", "ita_ibate_corrientes")
    start = end - timedelta(days=fw.WINDOW_DAYS - 1)
    for key in suspects:
        stretch = next(s for s in fl.load_corridors() if s.key == key)
        corridor = fl.corridor_geometry(stretch)
        got = fw.acquisitions(corridor, start, end)
        seen = fw.observed_km2(corridor, start, end)
        print(f"\n{key}: {got['scenes']} scene(s) on {got['acq_days']}, "
              f"orbits {got['orbits']}, platforms {got['platforms']}, "
              f"{seen:,.0f} km2 observed")
    # Reports rather than asserts: the answer is the finding.


def test_the_mask_scored_inside_and_outside_the_channel(ee_ready):
    """A1.1 and A1.2: decompose the published IoU.

    The published 0.69 is scored on total water, of which the permanent
    channel is 56-93% of the positive class - so most of it measures
    whether the mask can find a river whose location JRC already supplies.
    And flood_km2, the figure the product is named for, has never been
    scored at all. Same date, same reference, no new imagery: this splits
    the one number into the two it is hiding.
    """
    import sys

    sys.path.insert(0, "scripts")
    from validate_mask import agreement, optical_water

    from parana_crecida import flooded as fl
    from parana_crecida import floodwater as fw

    day = date(2026, 10, 22)
    start, end = day - timedelta(days=6), day + timedelta(days=6)
    permanent = fw.permanent_water()

    for stretch in fl.load_corridors():
        corridor = fl.corridor_geometry(stretch)
        truth, seen, scenes = optical_water(corridor, day)
        if truth is None:
            continue
        mask = fw.water_mask(corridor, start, end)
        inside = agreement(corridor, truth.And(permanent), mask.And(permanent), seen)
        beyond = agreement(corridor, truth.And(permanent.Not()),
                           mask.And(permanent.Not()), seen)
        print(f"\n{stretch.key}: channel IoU {inside['iou']}, "
              f"beyond-channel IoU {beyond['iou']} ({scenes} scenes)")
    # Reports rather than asserts: there is no prior figure to assert against.
