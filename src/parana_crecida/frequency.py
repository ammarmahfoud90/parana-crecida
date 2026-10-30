"""How rare is this, in years.

The site has been saying "above the 95th percentile for the date", and for
most of its life that was fine. It stops working exactly when it matters:
a percentile saturates, so in a real flood a dozen gauges all read 100 and
the measure can no longer tell a bad year from the worst on record. That
is the opposite of what a reader needs at the peak.

Flood hydrology has a standard answer and it is not a percentile. Fit a
distribution to the annual maxima and quote the **return period** - the
average interval between years whose maximum reaches this level. "A
one-in-forty-year height at Goya" means something to a person, a
municipality and an insurer. "p100" means the curve ran out.

**Log-Pearson III**, which is the usual choice for flood frequency in the
Americas: a Pearson III fitted to the base-10 logarithms of the annual
maxima. Fitted by L-moments rather than by the method of moments, because
the ordinary skew coefficient is badly behaved on 60-odd points and
L-moments are far less sensitive to the single largest value - which, in a
record that contains 1983, matters.

**Three things this does not pretend.**

It is a frequency analysis of *stage*, not discharge. Stage depends on the
channel as well as the flow, and a river that scours or silts shifts its
own rating over decades. Worse, some of these gauges have datum breaks in
their history - Corrientes is known to - and this fits straight across
them, because the break dates are not recorded anywhere this project can
read. A break inflates the spread and flattens the curve, so a return
period here is good to the nearest step on the ladder and not better.

It assumes the annual maxima are independent and identically distributed.
On a regulated river below two of the largest dams in the world they are
not quite, and a reader is told so.

And a return period is not a schedule. A one-in-fifty-year level has a 2%
chance in any year, including the year after it last happened.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import gauges as roster

log = logging.getLogger(__name__)

DEFAULT_TABLE = Path("data/return_periods.json")

#: Fewest annual maxima before a fit is allowed. Thirty is the usual floor
#: for flood frequency work, and below it the upper tail is guesswork.
MIN_YEARS = 30

#: The Parana's flood season runs through the southern summer and autumn,
#: so a calendar year would cut a single flood in half and count it twice.
#: A water year starting in July keeps each flood whole.
WATER_YEAR_START_MONTH = 7

#: A year needs this many daily readings before its maximum is trusted.
MIN_DAYS_IN_YEAR = 200

#: Return periods the table carries, so a page can show the ladder a
#: reading sits on rather than only its own number.
LADDER = (2, 5, 10, 25, 50, 100)


@dataclass(frozen=True, slots=True)
class Fit:
    """A fitted stage-frequency curve for one gauge."""

    series_id: str
    name: str
    years: int
    first_year: int
    last_year: int
    #: Log-Pearson III parameters, on log10 of stage.
    mean: float
    sd: float
    skew: float
    #: Stage at each return period in :data:`LADDER`.
    levels: dict[int, float]
    record_max: float
    record_max_year: int

    def return_period(self, stage: float) -> float | None:
        """Average years between maxima at or above ``stage``.

        Capped at 500: beyond that the fit is extrapolating far outside
        the record and the number would be arithmetic rather than
        hydrology.
        """
        if stage is None or stage <= 0:
            return None
        probability = _exceedance(math.log10(stage), self.mean, self.sd, self.skew)
        if probability <= 0:
            return 500.0
        return min(500.0, 1.0 / probability)


def water_year(day: date) -> int:
    """The year a flood season belongs to, labelled by the year it ends."""
    return day.year + 1 if day.month >= WATER_YEAR_START_MONTH else day.year


def annual_maxima(values) -> dict[int, tuple[date, float]]:
    """``{water year: (day, stage)}`` for years with enough readings."""
    counts: dict[int, int] = {}
    best: dict[int, tuple[date, float]] = {}
    for value in values:
        year = water_year(value.day)
        counts[year] = counts.get(year, 0) + 1
        if year not in best or value.value > best[year][1]:
            best[year] = (value.day, value.value)
    return {y: v for y, v in best.items() if counts[y] >= MIN_DAYS_IN_YEAR}


def _l_moments(sorted_values: list[float]) -> tuple[float, float, float]:
    """First three L-moments of an ascending sample.

    L-moments are linear combinations of order statistics, so a single
    enormous year moves them far less than it moves the ordinary skew -
    which on a 60-year Parana record is the difference between a usable
    fit and one bent by 1983 alone.
    """
    n = len(sorted_values)
    b0 = sum(sorted_values) / n
    b1 = sum(i * x for i, x in enumerate(sorted_values)) / (n * (n - 1))
    b2 = sum(
        i * (i - 1) * x for i, x in enumerate(sorted_values)
    ) / (n * (n - 1) * (n - 2))
    l1 = b0
    l2 = 2 * b1 - b0
    l3 = 6 * b2 - 6 * b1 + b0
    return l1, l2, l3


def _skew_from_l(tau3: float) -> float:
    """Pearson III skew from the L-skewness, by the Hosking approximation."""
    if abs(tau3) < 1e-6:
        return 0.0
    if abs(tau3) < 1.0 / 3.0:
        z = 3 * math.pi * tau3 * tau3
        alpha = (1 + 0.2906 * z) / (z + 0.1882 * z * z + 0.0442 * z ** 3)
    else:
        z = 1 - abs(tau3)
        alpha = (0.36067 * z - 0.59567 * z * z + 0.25361 * z ** 3) / (
            1 - 2.78861 * z + 2.56096 * z * z - 0.77045 * z ** 3
        )
    skew = 2.0 / math.sqrt(alpha)
    return math.copysign(skew, tau3)


def _wilson_hilferty(probability: float, skew: float) -> float:
    """Frequency factor K for a Pearson III at this exceedance probability."""
    z = _normal_quantile(1 - probability)
    if abs(skew) < 1e-6:
        return z
    k = skew / 6.0
    return (2.0 / skew) * (((z - k) * k + 1.0) ** 3 - 1.0)


def _normal_quantile(p: float) -> float:
    """Standard normal quantile, Acklam's rational approximation."""
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def _exceedance(log_stage: float, mean: float, sd: float, skew: float) -> float:
    """Probability a year's maximum reaches this log-stage, by bisection on K."""
    if sd <= 0:
        return 0.0
    target = (log_stage - mean) / sd
    lo, hi = 1e-4, 0.999
    for _ in range(60):
        mid = (lo + hi) / 2
        if _wilson_hilferty(mid, skew) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fit(series_id: str, name: str, values) -> Fit | None:
    """Fit a Log-Pearson III to one gauge's annual maxima."""
    maxima = annual_maxima(values)
    usable = {y: v for y, v in maxima.items() if v[1] > 0}
    if len(usable) < MIN_YEARS:
        return None

    logs = sorted(math.log10(v[1]) for v in usable.values())
    l1, l2, l3 = _l_moments(logs)
    if l2 <= 0:
        return None
    skew = _skew_from_l(l3 / l2)
    # L-scale to Pearson III standard deviation, through the same
    # approximation used for the skew.
    alpha = 4.0 / (skew * skew) if abs(skew) > 1e-6 else 1e6
    # Through log-gamma: a near-symmetric record drives alpha enormous and
    # gamma(alpha) straight past the limit of a float, where the ratio it
    # appears in is perfectly well behaved. As alpha grows this tends to
    # l2 * sqrt(pi), which is the normal case, as it should.
    ratio = math.exp(math.lgamma(alpha) - math.lgamma(alpha + 0.5))
    sd = abs(l2 * math.sqrt(math.pi * alpha) * ratio)
    if not (0 < sd < 10):
        return None

    peak_year, (peak_day, peak_value) = max(usable.items(), key=lambda kv: kv[1][1])
    return Fit(
        series_id=series_id,
        name=name,
        years=len(usable),
        first_year=min(usable),
        last_year=max(usable),
        mean=round(l1, 6),
        sd=round(sd, 6),
        skew=round(skew, 6),
        levels={
            period: round(10 ** (l1 + sd * _wilson_hilferty(1.0 / period, skew)), 2)
            for period in LADDER
        },
        record_max=round(peak_value, 2),
        record_max_year=peak_year,
    )


def build(store, nodes=None) -> list[Fit]:
    """Fit every gauge with a long enough record."""
    from . import chain as ch

    nodes = nodes if nodes is not None else ch.load_chain()
    out: list[Fit] = []
    for node in sorted(nodes, key=lambda n: n.river_km):
        gauge = roster.BY_ID.get(node.series_id)
        if gauge is None or "flow" in gauge.flags:
            continue
        fitted = fit(node.series_id, gauge.label, store.series(node.series_id))
        if fitted is None:
            continue
        out.append(fitted)
        log.info(
            "%s: %d years (%d-%d), 1-in-10 at %.2f m, record %.2f in %d",
            fitted.name, fitted.years, fitted.first_year, fitted.last_year,
            fitted.levels[10], fitted.record_max, fitted.record_max_year,
        )
    return out


def to_payload(fits: list[Fit]) -> dict[str, object]:
    return {
        "schema": 1,
        "note": (
            "Log-Pearson III fitted by L-moments to the annual maxima of "
            "each gauge's own record, on a water year starting 1 July so a "
            "flood season is not split in two. Stage, not discharge: the "
            "channel shifts over decades and so does the rating. A return "
            "period is not a schedule - a one-in-fifty-year level has a 2% "
            "chance in any year, including the one after it last happened."
        ),
        "method": "log-pearson-iii/l-moments",
        "min_years": MIN_YEARS,
        "ladder": list(LADDER),
        "built_on": date.today().isoformat(),
        "gauges": [
            {
                "series_id": f.series_id,
                "name": f.name,
                "years": f.years,
                "first_year": f.first_year,
                "last_year": f.last_year,
                "mean_log": f.mean,
                "sd_log": f.sd,
                "skew_log": f.skew,
                "levels": {str(k): v for k, v in f.levels.items()},
                "record_max": f.record_max,
                "record_max_year": f.record_max_year,
            }
            for f in fits
        ],
    }


def load(path: Path | str = DEFAULT_TABLE) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def return_period_of(table: dict, series_id: str, stage: float | None) -> float | None:
    """Return period for a stage, from a loaded table."""
    if stage is None or not table:
        return None
    row = next(
        (g for g in table.get("gauges", []) if g["series_id"] == series_id), None
    )
    if row is None or stage <= 0:
        return None
    probability = _exceedance(
        math.log10(stage), row["mean_log"], row["sd_log"], row["skew_log"]
    )
    if probability <= 0:
        return 500.0
    return round(min(500.0, 1.0 / probability), 1)
