"""Travel time: recovering a lag that is known, and refusing ones that are not."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from parana_crecida import gauges as roster
from parana_crecida.chain import ChainNode, Leg
from parana_crecida.events import Crest
from parana_crecida.lag import (
    MAX_CELERITY,
    MIN_CELERITY,
    cross_correlate,
    fit_celerity,
    leg_lag,
    match_crests,
    plausible_window,
)

START = date(2000, 1, 1)


def node(series_id, km):
    return ChainNode(series_id, roster.gauge(series_id).name, km, 1, 1000.0)


def leg(km, upstream="26261", downstream="26263"):
    return Leg(node(upstream, 0.0), node(downstream, km))


def crest(day_offset, *, series_id="26261", anomaly=2.0, value=6.0, percentile=95.0):
    return Crest(
        series_id=series_id,
        day=START + timedelta(days=day_offset),
        value=value,
        percentile=percentile,
        anomaly=anomaly,
        prominence=2.0,
        rise_start=None,
        rise_days=None,
        rise_rate=None,
    )


# -- the plausible window --------------------------------------------------


def test_window_brackets_the_celerity_band():
    lo, hi = plausible_window(600.0)
    assert lo == int(600 / MAX_CELERITY)
    assert hi == math.ceil(600 / MIN_CELERITY)
    assert lo < hi


def test_window_is_at_least_a_day_wide_for_a_short_leg():
    lo, hi = plausible_window(5.0)
    assert lo >= 1 and hi > lo


# -- crest matching --------------------------------------------------------


def test_a_crest_is_matched_to_its_arrival():
    km = 300.0  # window is 1 to 15 days
    ups = [crest(0)]
    downs = [crest(8, series_id="26263")]
    matches = match_crests(ups, downs, km)
    assert len(matches) == 1
    assert matches[0].lag_days == 8


def test_an_arrival_outside_the_window_is_not_matched():
    """Otherwise a crest pairs with the next flood two months later."""
    matches = match_crests([crest(0)], [crest(90, series_id="26263")], 300.0)
    assert matches == []


def test_an_arrival_too_soon_is_not_matched():
    matches = match_crests([crest(0)], [crest(0, series_id="26263")], 2000.0)
    assert matches == []


def test_a_much_smaller_arrival_is_not_matched():
    """A big wave upstream does not arrive as a ripple."""
    ups = [crest(0, anomaly=4.0)]
    downs = [crest(8, series_id="26263", anomaly=0.2)]
    assert match_crests(ups, downs, 300.0) == []


def test_each_arrival_is_claimed_only_once():
    """So one flood cannot be counted as several arrivals."""
    ups = [crest(0), crest(3)]
    downs = [crest(8, series_id="26263")]
    matches = match_crests(ups, downs, 300.0)
    assert len(matches) == 1
    # The nearer upstream crest keeps it.
    assert matches[0].upstream.day == START + timedelta(days=3)


def test_the_nearest_plausible_arrival_wins():
    ups = [crest(0)]
    downs = [crest(6, series_id="26263"), crest(12, series_id="26263")]
    assert match_crests(ups, downs, 300.0)[0].lag_days == 6


def test_only_flood_crests_are_matched_by_default():
    ups = [crest(0, percentile=50.0)]
    downs = [crest(8, series_id="26263", percentile=50.0)]
    assert match_crests(ups, downs, 300.0) == []
    assert len(match_crests(ups, downs, 300.0, high_only=False)) == 1


def test_matching_nothing():
    assert match_crests([], [crest(1)], 300.0) == []
    assert match_crests([crest(1)], [], 300.0) == []


# -- cross-correlation -----------------------------------------------------


def shifted_pair(lag_days, n=1200, period=120.0):
    """Two anomaly series where the second is the first, delayed."""
    up, down = [], []
    for i in range(n):
        day = START + timedelta(days=i)
        up.append((day, math.sin(2 * math.pi * i / period)))
        down.append(
            (day, math.sin(2 * math.pi * (i - lag_days) / period))
        )
    return up, down


@pytest.mark.parametrize("lag_days", [4, 9, 15])
def test_cross_correlation_recovers_an_injected_lag(lag_days):
    up, down = shifted_pair(lag_days)
    # 600 km: the window spans 2 to 30 days, so all three fit inside it.
    assert cross_correlate(up, down, 600.0) == pytest.approx(lag_days, abs=1)


def test_cross_correlation_will_not_look_outside_the_window():
    """The annual cycle correlates beautifully at 365 days and means nothing."""
    up, down = shifted_pair(300)
    found = cross_correlate(up, down, 600.0)
    lo, hi = plausible_window(600.0)
    assert found is None or lo <= found <= hi


def test_cross_correlation_needs_an_overlap_worth_having():
    up, down = shifted_pair(5, n=100)
    assert cross_correlate(up, down, 600.0) is None


def test_cross_correlation_of_nothing():
    assert cross_correlate([], [], 600.0) is None


# -- leg_lag ---------------------------------------------------------------


def build_matched(lag_days, count, km):
    """``count`` floods, each arriving ``lag_days`` later downstream."""
    ups = [crest(i * 200) for i in range(count)]
    downs = [crest(i * 200 + lag_days, series_id="26263") for i in range(count)]
    return {"26261": ups, "26263": downs}, km


def test_leg_lag_summarises_the_distribution():
    crests, km = build_matched(8, 9, 300.0)
    result = leg_lag(leg(km), crests)
    assert result.n == 9
    assert result.median_days == pytest.approx(8)
    assert result.p25_days == pytest.approx(8)
    assert result.celerity == pytest.approx(300 / 8, rel=0.01)


def test_leg_lag_is_not_confident_on_too_few_waves():
    crests, km = build_matched(8, 2, 300.0)
    assert not leg_lag(leg(km), crests).confident


def test_leg_lag_is_confident_with_enough_waves():
    crests, km = build_matched(8, 9, 300.0)
    assert leg_lag(leg(km), crests).confident


def test_leg_lag_without_matches():
    result = leg_lag(leg(300.0), {})
    assert result.n == 0
    assert result.median_days is None
    assert result.celerity is None
    assert not result.confident


def test_estimators_agreeing_is_reported():
    crests, km = build_matched(8, 9, 300.0)
    up, down = shifted_pair(8)
    result = leg_lag(leg(km), crests, {"26261": up, "26263": down})
    assert result.correlation_days is not None
    assert result.estimators_agree is True


def test_agreement_is_unknown_without_the_second_estimator():
    crests, km = build_matched(8, 9, 300.0)
    assert leg_lag(leg(km), crests).estimators_agree is None


# -- resolution ------------------------------------------------------------


def test_a_short_leg_is_flagged_resolution_limited():
    """21 km measured as 2 days says more about daily sampling than the river."""
    crests, km = build_matched(2, 9, 21.0)
    result = leg_lag(leg(km), crests)
    assert result.resolution_limited
    assert result.median_days == pytest.approx(2)


def test_a_long_leg_is_not_resolution_limited():
    crests, km = build_matched(9, 9, 400.0)
    assert not leg_lag(leg(km), crests).resolution_limited


def test_a_leg_with_no_estimate_counts_as_resolution_limited():
    assert leg_lag(leg(300.0), {}).resolution_limited


# -- whole-chain celerity --------------------------------------------------


def test_celerity_is_fitted_over_the_sum_not_leg_by_leg():
    """Roundings cancel over 780 km; they compound on a 21 km leg."""
    legs = []
    for km, lag_days in ((100.0, 2), (200.0, 5), (480.0, 14)):
        crests, _ = build_matched(lag_days, 9, km)
        legs.append(leg_lag(leg(km), crests))
    fit = fit_celerity(legs)
    assert fit is not None
    assert fit.total_km == pytest.approx(780.0)
    assert fit.total_days == pytest.approx(21.0)
    assert fit.km_per_day == pytest.approx(780.0 / 21.0, rel=0.01)
    assert fit.legs_used == 3
    assert fit.legs_skipped == 0


def test_a_leg_with_no_estimate_is_skipped_not_treated_as_zero():
    crests, km = build_matched(5, 9, 200.0)
    legs = [leg_lag(leg(km), crests), leg_lag(leg(300.0), {})]
    fit = fit_celerity(legs)
    assert fit.legs_used == 1
    assert fit.legs_skipped == 1
    assert fit.total_km == pytest.approx(200.0)


def test_no_fit_without_any_usable_leg():
    assert fit_celerity([leg_lag(leg(300.0), {})]) is None
    assert fit_celerity([]) is None


def test_days_per_100km_is_the_inverse():
    crests, km = build_matched(10, 9, 400.0)
    fit = fit_celerity([leg_lag(leg(km), crests)])
    assert fit.days_per_100km == pytest.approx(100.0 / fit.km_per_day)


# -- freezing --------------------------------------------------------------


def test_table_round_trip(tmp_path):
    from parana_crecida.lag import load_table, write_table

    crests, km = build_matched(8, 9, 300.0)
    path = tmp_path / "travel.json"
    write_table(path, [leg_lag(leg(km), crests)], record_span="1884-2026")
    rows = load_table(path)
    assert len(rows) == 1
    assert rows[0]["median_days"] == pytest.approx(8)
    assert rows[0]["from_name"] == "Corrientes"


def test_load_table_of_a_missing_file_is_empty_not_an_error(tmp_path):
    from parana_crecida.lag import load_table

    assert load_table(tmp_path / "nope.json") == []


def test_celerity_from_frozen_rows_matches_the_live_fit():
    from parana_crecida.lag import fit_celerity_rows, to_row

    legs = []
    for km, lag_days in ((100.0, 2), (200.0, 5), (480.0, 14)):
        crests, _ = build_matched(lag_days, 9, km)
        legs.append(leg_lag(leg(km), crests))
    live = fit_celerity(legs)
    frozen = fit_celerity_rows([to_row(L) for L in legs])
    assert frozen.km_per_day == pytest.approx(live.km_per_day)
    assert frozen.legs_used == live.legs_used


def test_frozen_rows_skip_a_leg_with_no_estimate():
    from parana_crecida.lag import fit_celerity_rows, to_row

    crests, km = build_matched(5, 9, 200.0)
    rows = [to_row(leg_lag(leg(km), crests)), to_row(leg_lag(leg(300.0), {}))]
    fit = fit_celerity_rows(rows)
    assert fit.legs_used == 1 and fit.legs_skipped == 1


# -- the committed table ---------------------------------------------------

from parana_crecida.lag import load_table as _load_table  # noqa: E402

COMMITTED = _load_table()


@pytest.mark.skipif(not COMMITTED, reason="no committed travel times")
def test_the_committed_table_covers_every_routing_leg():
    from parana_crecida.chain import load_chain, routing_legs

    expected = {(L.upstream.series_id, L.downstream.series_id) for L in routing_legs(load_chain())}
    got = {(r["from"], r["to"]) for r in COMMITTED}
    assert got == expected


@pytest.mark.skipif(not COMMITTED, reason="no committed travel times")
def test_the_committed_table_rests_on_real_waves():
    """A table built from a short cache would have almost no matches."""
    assert all(r["n"] >= 5 for r in COMMITTED)


@pytest.mark.skipif(not COMMITTED, reason="no committed travel times")
def test_the_committed_crest_speed_is_physically_plausible():
    from parana_crecida.lag import fit_celerity_rows

    fit = fit_celerity_rows(COMMITTED)
    assert 20 < fit.km_per_day < 120
    assert fit.total_km == pytest.approx(781, abs=20)
