"""Client for the INA a5 hydrological API (``alerta.ina.gob.ar/a5``).

Extracted from the rios-argentina scripts, where the same HTTP, catalogue and
cache code had been copy-pasted into six files with slightly different bugs in
each. Nothing here knows anything about the Parana, or about any one project.

    from ina_a5 import A5Client, SeriesCache, fetch_catalogue, fetch_daily

    with A5Client() as client:
        catalogue = fetch_catalogue(client)
        values = fetch_daily(client, 26261, date(2024, 1, 1), date(2024, 3, 1))
"""

from __future__ import annotations

from .cache import BACKFILL_DAYS, SeriesCache
from .catalogue import (
    AR_BBOX,
    coords_ok,
    fetch_catalogue,
    flatten,
    iter_features,
    live_series,
    parse_feature,
    pick,
)
from .client import DEFAULT_BASE_URL, A5Client, RetryPolicy
from .errors import (
    A5AuthError,
    A5Error,
    A5HttpError,
    A5ResponseError,
    A5TransportError,
    A5Truncated,
)
from .models import (
    DAILY_MEAN_VARS,
    FLOW_VARS,
    INSTANT_VARS,
    LEVEL_VARS,
    VAR_NAMES,
    DailyValue,
    Series,
    var_family,
)
from .observations import (
    DEFAULT_TZ,
    extract_observations,
    fetch_daily,
    fetch_history,
    fetch_raw,
    rows_to_daily,
)
from .quality import (
    GuardFlag,
    GuardThresholds,
    StepShares,
    datum_step,
    doy,
    percentile,
    run_guards,
)

__version__ = "0.1.0"

__all__ = [
    "AR_BBOX",
    "BACKFILL_DAYS",
    "DAILY_MEAN_VARS",
    "DEFAULT_BASE_URL",
    "DEFAULT_TZ",
    "FLOW_VARS",
    "INSTANT_VARS",
    "LEVEL_VARS",
    "VAR_NAMES",
    "A5AuthError",
    "A5Client",
    "A5Error",
    "A5HttpError",
    "A5ResponseError",
    "A5TransportError",
    "A5Truncated",
    "DailyValue",
    "GuardFlag",
    "GuardThresholds",
    "RetryPolicy",
    "Series",
    "SeriesCache",
    "StepShares",
    "coords_ok",
    "datum_step",
    "doy",
    "extract_observations",
    "fetch_catalogue",
    "fetch_daily",
    "fetch_history",
    "fetch_raw",
    "flatten",
    "iter_features",
    "live_series",
    "parse_feature",
    "percentile",
    "pick",
    "rows_to_daily",
    "run_guards",
    "var_family",
]
