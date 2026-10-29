# parana-crecida

Flood-wave tracker for the Argentine Paraná.

A flood wave takes days to travel from the Paraguay/Paraná confluence to the Delta,
so a crest observed at Corrientes is advance warning for Goya, Paraná, Rosario and
the Delta towns. This repo turns the observed daily stage into that warning: it
detects rises and crests at each gauge, estimates travel time between gauges from
the historical record, and projects where a wave now in progress will arrive.

Data comes from INA's a5 API (`alerta.ina.gob.ar/a5`).

## Two packages

| Package | What it is |
|---|---|
| `packages/ina-a5` | A client for the INA a5 API. Generic — knows nothing about the Paraná. |
| `src/parana_crecida` | The tracker. |

`ina-a5` exists because the ingestion code in
[rios-argentina](https://github.com/ammarmahfoud/rios-argentina) had been
copy-pasted into six scripts, each with slightly different bugs. It is a separate
distribution so that rios-argentina can depend on it rather than keep its copies:

```bash
pip install "ina-a5 @ git+https://github.com/ammarmahfoud/parana-crecida#subdirectory=packages/ina-a5"
```

The three things it fixes, which are the whole reason to extract it:

1. **Failure raises.** The original `get_json` returned `None` for a 429, a 500, a
   timeout and an empty result alike, so "the server throttled us" was
   indistinguishable from "this gauge has no data".
2. **Retries are status-aware.** 429 and 5xx back off exponentially and honour
   `Retry-After`; other 4xx fail at once, because retrying cannot help.
3. **One session per client.** `requests.Session` is not thread-safe, and the
   original climatology build shared one across a thread pool.

It also converts timestamps to Buenos Aires time instead of truncating the string,
and drops today's partial day by default — a day still filling up averages to a
level the river never had, which at a wave front reads as a spurious rise.

The on-disk cache format is unchanged from rios-argentina (`cache/{series_id}.json`
holding `[["YYYY-MM-DD", value], ...]`), so its existing `cache/` and
`cache_recent/` are readable here with no migration. There are tests that read the
real files to prove it rather than assert it.

## Install

```bash
python -m venv .venv --system-site-packages
.venv/Scripts/python -m pip install -e packages/ina-a5 -e .
```

On Linux or macOS use `.venv/bin/python`. Install `ina-a5` first or in the same
command — the tracker depends on it and it is not on PyPI.

Python 3.12 or newer. An `A5_TOKEN` environment variable is supported but has never
been required for the public endpoints.

## Use

```bash
crecida gauges                      # the gauges in scope and how far each is trusted
crecida catalogue                   # page the whole a5 catalogue to data/
crecida fetch --tier 1              # top up the cached series
crecida guards                      # check for stale, stuck or re-zeroed gauges
```

Reuse rios-argentina's existing history instead of re-downloading a century:

```bash
crecida guards --cache-dir "../Rios de Argentina/cache" --cache-dir cache_recent
```

`--cache-dir` is repeatable and searched in order. Writes go to the first root.

Every data command takes `--report PATH` and writes a plain-text run summary, the
convention rios-argentina uses. Read it before committing.

## Scope: Puerto Iguazú to San Fernando

Gauges are tiered by how much they can carry, because the lower river is genuinely
harder and pretending otherwise would put confident arrival times on gauges that
cannot support them. `crecida gauges` prints the whole roster.

- **Tier 1 — Corrientes to Villa Constitución.** Thirteen main-stem gauges,
  correctly snapped, records of 120–143 years at Corrientes, Goya, La Paz, Paraná,
  Diamante and Rosario. Celerity is fitted here and arrival times are published as
  forecasts.
- **Tier 2 — below Villa Constitución, to San Fernando.** Tracked and charted, with
  wide uncertainty and no celerity fit until validated. Zárate, Campana and Escobar
  are on the Paraná de las Palmas branch, not the main stem; Campana, Escobar,
  Baradero, Tigre, San Fernando and Olivos are unrouted (no reach in radius, or
  inside the Río de la Plata box); the braided delta is a single centerline in
  HydroRIVERS; and stage there is tide- and backwater-affected, so a "crest" is
  partly not a river signal. The estuary gauges are the downstream boundary, not
  routing nodes.
- **Tier 3 — context.** Puerto Iguazú down to Santa Ana, between Itaipú and
  Yacyretá; Posadas to Paso de la Patria, below Yacyretá — all dam-operated. Plus
  the Paraguay, Bermejo and Iguazú inflows. Lead indicators, never routing nodes.

Six gauges are flagged `bad_snap`: `snap_v2.py` put Barranqueras, Santa Fe, San
Nicolás, Ramallo, San Pedro and Zárate on small tributary reaches. They need a
manual reach override before they can contribute river-km.

## Status

Built:

- `ina-a5`: client, catalogue paging, observation download, cache, quality guards.
- `parana_crecida`: the gauge roster, and the `gauges` / `catalogue` / `fetch` /
  `guards` commands.

Not built yet — see `.claude/plans` for the full plan:

- `data/parana_chain.json`, the reviewed downstream chain with river-km, derived
  from the HydroRIVERS topology and hand-corrected for the bad snaps.
- `normalize`: stage anomaly against the day-of-year percentile curves. Raw metres
  are not comparable across gauges, since every gauge has its own zero.
- `events`: rise, crest and recession detection per gauge.
- `lag`: travel time by cross-correlation and by crest matching, with a celerity
  fit stratified by wave amplitude.
- `track`: the live wave and its projected arrivals.
- `site/index.html`: a wave trajectory plot — river-km against time, where a flood
  wave is a diagonal streak whose slope is the celerity.
- The daily GitHub Actions job that publishes it.

INA does publish per-gauge alert and evacuation levels, but in its daily report
PDF rather than through the a5 API — so they have to be parsed from
`alerta.ina.gob.ar/ina/06-INFORMES/diario/` once and stored. Until that is done, an
event is defined relative to each gauge's own seasonal normal (the day-of-year
p90/p95 curves from rios-argentina's `climatology_final.json`). Both definitions
are worth having: the official levels are what people act on, the percentiles are
what make gauges comparable.

**This is not an alert system.** Travel times describe what has already happened,
not a forecast. Every page links to INA's official alert.

## Tests

```bash
.venv/Scripts/python -m pytest
```

Two tests read the real rios-argentina cache from `~/rios-argentina/cache_recent`
and skip if it is not there.

## Data provenance

Observations and metadata are from the Instituto Nacional del Agua (INA),
Argentina, via the public a5 API. River topology is derived from HydroRIVERS v10.
Historical series, percentile curves and the snapped station network come from
rios-argentina.
