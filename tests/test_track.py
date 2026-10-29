"""Arrival projections.

This is the only thing on the site that points forward, so the tests are
mostly about what it refuses to say: no projection across a leg it cannot
measure, none for a gauge the crest has already passed, and no anchor at
all unless there is really a wave in progress.

Two of these exist because the behaviour was wrong first and the backtest
caught it: the anchor is the wave *front* rather than the furthest gauge
that has turned over, and the record is truncated at the day being asked
about.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from parana_crecida import track as tk


@dataclass(frozen=True, slots=True)
class Value:
    day: date
    value: float


class FakeStore:
    def __init__(self, series):
        self.series_map = series

    def series(self, series_id, *, since=None):
        return [
            v for v in self.series_map.get(series_id, [])
            if since is None or v.day >= since
        ]


class FakeClim:
    def percentile_of(self, series_id, day, value):
        return min(100.0, value * 10)


@dataclass(frozen=True, slots=True)
class Node:
    series_id: str
    river_km: float


def ramp(start: date, values: list[float]) -> list[Value]:
    return [Value(start + timedelta(days=i), v) for i, v in enumerate(values)]


def rising(start: date, days: int = 30) -> list[Value]:
    return ramp(start, [1.0 + 0.1 * i for i in range(days)])


def crested(start: date, days: int = 30, fall: float = 1.0) -> list[Value]:
    up = [1.0 + 0.1 * i for i in range(days - 6)]
    top = up[-1]
    return ramp(start, up + [top - fall * (i + 1) / 6 for i in range(6)])


#: Real series ids, so the roster's own flag filtering is exercised too.
#: Corrientes, Empedrado, Bella Vista, Goya - four tier-1 gauges in order.
A, B, C, D = "26261", "26263", "26264", "26265"

LEGS = [
    {"from": A, "to": B, "km": 60, "median_days": 2.0,
     "p25_days": 1.0, "p75_days": 3.0, "confident": True},
    {"from": B, "to": C, "km": 90, "median_days": 3.0,
     "p25_days": 2.0, "p75_days": 5.0, "confident": True},
    {"from": C, "to": D, "km": 80, "median_days": 2.0,
     "p25_days": 1.0, "p75_days": 4.0, "confident": True},
]

NODES = [Node(A, 0.0), Node(B, 60.0), Node(C, 150.0), Node(D, 230.0)]


# -- reading one gauge -----------------------------------------------------


def test_a_rising_gauge_has_not_turned():
    start = date(2026, 9, 1)
    state = tk.gauge_state(A, "A", 0.0, rising(start), FakeClim())
    assert state.rising
    assert not state.turned
    assert state.status == "rising"


def test_a_fallen_gauge_is_confirmed():
    state = tk.gauge_state(A, "A", 0.0, crested(date(2026, 9, 1)), FakeClim())
    assert state.turned
    assert state.confirmed
    assert state.status == "fallen"


def test_a_gauge_that_merely_stopped_rising_is_not_confirmed():
    """The case this was written for.

    Corrientes sat 0.07 m below a peak it had climbed 2.50 m to reach. That
    is not a crest, it is a river that has stopped going up, and the
    difference is published rather than smoothed over.
    """
    up = [1.0 + 0.1 * i for i in range(25)]
    flat = [up[-1] - 0.01 * (i + 1) for i in range(5)]
    state = tk.gauge_state(A, "A", 0.0, ramp(date(2026, 9, 1), up + flat), FakeClim())
    assert state.turned
    assert not state.confirmed
    assert state.status == "cresting"


def test_too_short_a_record_gives_no_state():
    assert tk.gauge_state(A, "A", 0.0, rising(date(2026, 10, 1), 4), FakeClim()) is None


# -- finding the wave front ------------------------------------------------


def make_states(shapes: dict[str, str], start=date(2026, 9, 1)):
    series = {
        sid: (crested(start) if shape == "crested" else rising(start))
        for sid, shape in shapes.items()
    }
    out = []
    for node in NODES:
        if node.series_id not in series:
            continue
        out.append(tk.gauge_state(
            node.series_id, node.series_id, node.river_km,
            series[node.series_id], FakeClim(),
        ))
    return out


def test_the_anchor_is_the_front_not_the_furthest_gauge():
    """The bug the backtest found.

    Over a window that ends after a flood every gauge has turned over, and
    "the most downstream gauge that has turned" is then the last gauge on
    the chain - which has nothing below it to project to. The anchor must
    be the boundary between crested above and rising below.
    """
    states = make_states({A: "crested", B: "crested", C: "rising", D: "rising"})
    anchor = tk.find_anchor(states)
    assert anchor is not None and anchor.series_id == B


def test_a_river_that_has_entirely_crested_has_no_front():
    states = make_states({A: "crested", B: "crested", C: "crested", D: "crested"})
    assert tk.find_anchor(states) is None


def test_a_river_still_rising_everywhere_has_no_front():
    states = make_states({A: "rising", B: "rising", C: "rising"})
    assert tk.find_anchor(states) is None


def test_the_front_taken_is_the_most_downstream_one():
    """Two boundaries; the lower one is the shorter journey and wins."""
    states = make_states({A: "crested", B: "rising", C: "crested", D: "rising"})
    anchor = tk.find_anchor(states)
    assert anchor is not None and anchor.series_id == C


# -- projecting ------------------------------------------------------------


def track_from(shapes, rows=LEGS, today=date(2026, 9, 30)):
    start = today - timedelta(days=29)
    store = FakeStore({
        sid: (crested(start) if shape == "crested" else rising(start))
        for sid, shape in shapes.items()
    })
    return tk.build(store, FakeClim(), nodes=NODES, rows=rows, today=today)


def test_projections_accumulate_down_the_chain():
    track = track_from({A: "crested", B: "rising", C: "rising", D: "rising"})
    assert track.anchor.series_id == A
    by_id = {p.series_id: p for p in track.projections}
    peak = track.anchor.peak_day
    assert by_id[B].expected == peak + timedelta(days=2)
    assert by_id[C].expected == peak + timedelta(days=5)
    assert by_id[D].expected == peak + timedelta(days=7)
    assert [by_id[k].legs for k in (B, C, D)] == [1, 2, 3]


def test_the_window_widens_with_every_leg():
    track = track_from({A: "crested", B: "rising", C: "rising", D: "rising"})
    spans = [(p.latest - p.earliest).days for p in track.projections]
    assert spans == sorted(spans), spans
    assert spans[0] < spans[-1]


def test_a_leg_with_no_travel_time_ends_the_chain():
    """Better to publish nothing past it than a total that skipped it."""
    rows = [dict(LEGS[0]), {**LEGS[1], "confident": False}, dict(LEGS[2])]
    track = track_from({A: "crested", B: "rising", C: "rising", D: "rising"}, rows=rows)
    assert [p.series_id for p in track.projections] == [B]


def test_a_gauge_the_crest_has_passed_gets_no_projection():
    track = track_from({A: "crested", B: "crested", C: "rising", D: "rising"})
    assert track.anchor.series_id == B
    assert [p.series_id for p in track.projections] == [C, D]


def test_no_crest_yet_is_said_rather_than_guessed():
    track = track_from({A: "rising", B: "rising", C: "rising", D: "rising"})
    assert track.anchor is None
    assert track.reason == "no_crest_yet"
    assert track.projections == []


def test_an_anchor_with_no_measured_legs_says_so():
    """A crest above Yacyreta is one we cannot follow, not one that has passed."""
    rows = [{**LEGS[0], "from": "26999"}]
    track = track_from({A: "crested", B: "rising", C: "rising"}, rows=rows)
    assert track.anchor is not None
    assert track.reason == "no_measured_legs_below_anchor"


def test_the_record_is_truncated_at_the_day_asked_about():
    """The other bug the backtest found.

    The store returns everything from `since` onward. Without a cut at
    `today`, a question about a past date saw the whole record after it and
    anchored on a flood that had not happened yet.
    """
    start = date(2026, 1, 1)
    # Quiet all year, then an enormous flood in December.
    series = ramp(start, [1.0] * 300 + [1.0 + i for i in range(60)])
    store = FakeStore({A: series, B: series, C: series})
    track = tk.build(
        store, FakeClim(), nodes=NODES, rows=LEGS, today=date(2026, 3, 1)
    )
    for state in track.states:
        assert state.last_day <= date(2026, 3, 1)
        assert state.peak_value <= 1.0


# -- timing ----------------------------------------------------------------


def test_timing_is_judged_against_the_data_not_the_calendar():
    """INA's feed runs a day or two behind.

    Judged against today, every gauge is overdue by exactly that lag.
    """
    track = track_from({A: "crested", B: "rising", C: "rising", D: "rising"})
    as_of = max(s.last_day for s in track.states)
    projection = track.projections[0]
    assert projection.timing(as_of) in {"ahead", "due", "overdue"}
    # Four days further on, nothing has been measured, so nothing changes.
    payload = tk.payload(track, today=as_of + timedelta(days=4))
    assert payload["as_of"] == as_of.isoformat()
    assert payload["projections"][0]["timing"] == projection.timing(as_of)


def test_days_ahead_counts_from_today():
    track = track_from({A: "crested", B: "rising", C: "rising", D: "rising"})
    today = date(2026, 9, 30)
    payload = tk.payload(track, today=today)
    for row, projection in zip(payload["projections"], track.projections):
        assert row["days_ahead"] == (projection.expected - today).days


# -- the payload -----------------------------------------------------------


def test_the_payload_says_whether_the_crest_is_confirmed():
    track = track_from({A: "crested", B: "rising", C: "rising", D: "rising"})
    payload = tk.payload(track)
    assert payload["anchor"]["confirmed"] is True
    assert payload["anchor"]["series_id"] == A
    assert "how high" in payload["note"]


def test_the_payload_of_an_empty_track_carries_the_reason():
    track = track_from({A: "rising", B: "rising", C: "rising"})
    payload = tk.payload(track)
    assert payload["anchor"] is None
    assert payload["reason"] == "no_crest_yet"
    assert payload["projections"] == []


# -- the frozen skill measurement ------------------------------------------

SKILL = pytest.importorskip("parana_crecida.backtest").load()


@pytest.mark.skipif(not SKILL, reason="the backtest has not been run")
def test_the_published_skill_is_real_and_not_flattering():
    """The page states its own accuracy, so the numbers must be there."""
    for key in (
        "scored", "inside_window_pct", "median_error_days",
        "baseline_median_error_days", "crest_flatness",
    ):
        assert key in SKILL, key
    assert SKILL["scored"] > 500
    assert 0 <= SKILL["inside_window_pct"] <= 100


@pytest.mark.skipif(not SKILL, reason="the backtest has not been run")
def test_the_projection_beats_having_no_travel_times_at_all():
    """If it cannot beat "the crest arrives everywhere at once", it is noise."""
    assert SKILL["median_error_days"] < SKILL["baseline_median_error_days"], (
        f"median error {SKILL['median_error_days']} d against a baseline of "
        f"{SKILL['baseline_median_error_days']} d - the travel times are "
        "adding nothing"
    )
