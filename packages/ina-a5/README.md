# ina-a5

A Python client for the INA a5 hydrological API (`alerta.ina.gob.ar/a5`), the
public feed for Argentine river gauges.

Extracted from the [rios-argentina](https://github.com/ammarmahfoud90/rios-argentina)
scripts, where the same HTTP, catalogue and cache code had been copy-pasted into
six files with slightly different bugs in each. Nothing here knows about any
particular river or project.

```bash
pip install "ina-a5 @ git+https://github.com/ammarmahfoud90/parana-crecida#subdirectory=packages/ina-a5"
```

```python
from datetime import date
from ina_a5 import A5Client, SeriesCache, fetch_catalogue, fetch_daily, run_guards

with A5Client() as client:                      # A5_TOKEN is read if set
    catalogue = fetch_catalogue(client)         # ~9.5k series, paged properly
    values = fetch_daily(client, 26261, date(2024, 1, 1), date(2024, 3, 1))

cache = SeriesCache(["cache", "cache_recent"])  # searched in order
cache.update(26261, values)
flags = run_guards(cache.read(26261), today=date.today())
```

## What it does differently

**Failure raises.** The original `get_json` returned `None` for a 429, a 500, a
timeout and a genuinely empty result alike, so a throttled run was
indistinguishable from a gauge with no data. Here every failure raises an
`A5Error` subclass and an empty list means the server really said so.

**Retries are status-aware.** 429 and 5xx back off exponentially with jitter and
honour `Retry-After` (ignoring absurd values); other 4xx fail at once; 401 and 403
raise `A5AuthError` rather than calling `SystemExit`.

**One session per client.** `requests.Session` is not thread-safe. Use
`client.clone()` to give each worker thread its own. Keep concurrency low — the
server belongs to a public agency.

**Timezones are explicit.** Timestamps are parsed and converted to a named zone
(Buenos Aires by default) rather than string-truncated, and today's partial day is
dropped by default.

## Endpoint quirks it handles

- **The catalogue silently caps at 5000 features** unless you page with
  `limit`/`offset`. The real catalogue holds about 9.5k series.
- **Catalogue pages overlap** — `offset` is sometimes ignored, so "a short page
  means the end" is not a sufficient stop condition. Paging stops on *no new ids*.
- **Long history requests look capped**, though no limit is documented. A round row
  count (1000, 5000, 10000, 20000) or a first row far later than requested is
  treated as truncation and re-fetched in five-year chunks.
- **One gauge appears as several series** — instantaneous level (2), daily mean
  (39), 4-hourly (101), and flow (4, 40). Series of different families are never
  merged.
- **The catalogue's time filter may be ignored**, so `live_series` filters locally.
- **`proc_id` is not available** — only a free-text procedure name.

## Modules

| Module | Contents |
|---|---|
| `client` | `A5Client`, `RetryPolicy` |
| `catalogue` | `fetch_catalogue`, `iter_features`, `parse_feature`, `live_series` |
| `observations` | `fetch_daily`, `fetch_history`, `rows_to_daily` |
| `cache` | `SeriesCache` — read, merge, top-up windows, trim |
| `quality` | `run_guards` and the individual stale / flatline / spike / range / datum-step checks |
| `models` | `Series`, `DailyValue`, the variable-id map |
| `errors` | `A5Error` and subclasses |

The cache file format is unchanged from rios-argentina
(`cache/{series_id}.json` holding `[["YYYY-MM-DD", value], ...]`), so an existing
cache directory is readable with no migration.

## The guards

Each check judges a gauge against its own record, never an absolute threshold,
because gauge zeros differ between stations. `datum_step` is the interesting one:
it exists because Corrientes has two different gauge zeros in one spliced record,
which made a 126-year series read 2.69 m where a 26-year series read 7.01 m. A
flood sits above the historical p95 for days or weeks and then recedes; a re-zeroed
gauge sits outside its envelope every single day and never returns.

## Licence

MIT. Data from the Instituto Nacional del Agua (INA), Argentina.
