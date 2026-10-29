"""Typed records and the a5 variable map.

Variable ids are taken from the catalogue as observed in practice; a5 exposes
no documented enumeration. ``proc_id`` is deliberately absent: the catalogue
carries only a free-text procedure name, so nothing here may depend on a
procedure identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Water level, in metres of gauge-local stage.
VAR_LEVEL_INSTANT = 2
VAR_LEVEL_DAILY_MEAN = 39
VAR_LEVEL_4_HOURLY = 101

# Discharge, in m3/s.
VAR_FLOW_INSTANT = 4
VAR_FLOW_DAILY_MEAN = 40

LEVEL_VARS = frozenset({VAR_LEVEL_INSTANT, VAR_LEVEL_DAILY_MEAN, VAR_LEVEL_4_HOURLY})
FLOW_VARS = frozenset({VAR_FLOW_INSTANT, VAR_FLOW_DAILY_MEAN})
DAILY_MEAN_VARS = frozenset({VAR_LEVEL_DAILY_MEAN, VAR_FLOW_DAILY_MEAN})
INSTANT_VARS = frozenset({VAR_LEVEL_INSTANT, VAR_FLOW_INSTANT})

VAR_NAMES = {
    VAR_LEVEL_INSTANT: "Altura hidrometrica",
    VAR_LEVEL_DAILY_MEAN: "Altura h. media diaria",
    VAR_LEVEL_4_HOURLY: "Altura h. 4-horaria",
    VAR_FLOW_INSTANT: "Caudal",
    VAR_FLOW_DAILY_MEAN: "Caudal medio diario",
}

UNITS = {"level": "m", "flow": "m3/s"}


def var_family(var_id: int | str | None) -> str | None:
    """``"level"``, ``"flow"`` or ``None`` for anything we do not model.

    Series of different families must never be averaged or spliced together.
    """
    try:
        vid = int(var_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if vid in LEVEL_VARS:
        return "level"
    if vid in FLOW_VARS:
        return "flow"
    return None


@dataclass(frozen=True, slots=True)
class Series:
    """One a5 series: a single variable measured at a single station."""

    series_id: str
    station_id: str = ""
    station: str = ""
    river: str = ""
    var_id: int | None = None
    network: str = ""
    lon: float | None = None
    lat: float | None = None
    cat_start: date | None = None
    cat_end: date | None = None

    @property
    def family(self) -> str | None:
        return var_family(self.var_id)

    @property
    def unit(self) -> str:
        return UNITS.get(self.family or "", "")

    @property
    def is_daily_mean(self) -> bool:
        return self.var_id in DAILY_MEAN_VARS

    @property
    def has_coords(self) -> bool:
        return self.lon is not None and self.lat is not None


@dataclass(frozen=True, slots=True)
class DailyValue:
    """One calendar day of one series.

    ``n_raw`` is how many raw rows were averaged into ``value`` - 1 for a
    daily-mean series, up to 6 for a 4-hourly one, more for instantaneous.
    It is the handle for spotting a partial day.
    """

    day: date
    value: float
    n_raw: int = 1
