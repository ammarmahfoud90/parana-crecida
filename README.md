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
crecida gauges            # the gauges in scope and how far each is trusted
crecida thresholds        # parse INA's alert/evacuation levels into data/
crecida fetch             # top up the cached series from the a5 API
crecida load              # load the JSON cache into DuckDB
crecida validate          # run the data tests
crecida export            # validate, then write site/data
crecida daily             # all of the above, in order: what CI runs
crecida guards            # check for stale, stuck or re-zeroed gauges
crecida catalogue         # page the whole a5 catalogue to data/
```

## Two layers, and the gate between them

The JSON cache under `cache/` is the **raw layer** - one file per series, the
format rios-argentina already writes, cheap to top up and safe to delete. DuckDB
is the **clean layer**: typed, keyed on `(series_id, day)` so duplicate days are
structurally impossible, and the only thing the exporter reads. A bad load can
always be rebuilt from the raw files without going back to INA's server.

Between the store and the site is the gate. `export` takes a validation result and
refuses to write unless it passed, so "export without validating" is not a thing
you can accidentally write. If a test fails, nothing on disk changes, CI skips its
commit and deploy steps, and the already-published page stays up - with its own
last-update date visible, so a reader can see the data has not moved.

The data tests, in `src/parana_crecida/validate.py`:

| Test | Blocks? |
|---|---|
| There are rows at all | yes - an empty store must never pass vacuously |
| No null fields, no NaN or infinity | yes |
| No duplicate `(gauge, day)` | yes |
| No readings dated after today | yes - that means a timezone bug, not a fast river |
| No series systematically outside its unit's range | yes |
| Thresholds in the same unit as their gauge | yes |
| Every tier-1 gauge has data | warns; blocks only if *none* do |
| Isolated out-of-range readings | warns |
| Data is fresh | warns - if INA is down, keep showing the last good reading |
| Latest reading within the gauge's own record | warns |

The range check splits two faults that look alike. A whole series out of range
means the series is not what we think it is - discharge filed as stage - and blocks.
One bad row in twenty thousand is a glitch in INA's archive and only warns. Both
cases are real and both turned up on the first run against live data.

## Alert and evacuation levels

The a5 API does not publish thresholds; they are only in INA's daily report PDF.
`crecida thresholds` parses that into `data/ina_thresholds.csv`, which is then the
one source for them.

Three rules it follows, because a wrong threshold would mislabel a real flood:

- **Nothing is invented.** A gauge INA does not list gets an empty row saying so,
  and a missing threshold reads as *unknown* everywhere downstream, never as 0 m.
- **Nothing is dropped.** Report rows that match no gauge in scope are written out
  too, marked `unmatched_report_row`, so the CSV can be checked against the PDF
  line by line.
- **Matching is on name and unit.** INA lists a *stage* for Puerto Pilcomayo, but
  the series held under that name is *discharge*; attaching a 5.35 m alert to an
  1,800 m3/s reading would mark every day of the record as catastrophic, so the
  match is refused and reported.

As of the 2026-10-28 report, **14 of the 45 gauges in scope have official levels**.
The other 31 are shown as having none.

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

## View 1: river today

`site/index.html` is a plain page with no build step: it fetches the committed
`site/data/river_today.json` and renders every gauge from Puerto Iguazú to San
Fernando in river order, with the latest level, the change since the previous
reading, and the official alert and evacuation lines where INA publishes them.

Spanish and English, switchable, remembered per reader. Nothing in the pipeline is
translated — the JSON carries proper nouns and stable keys, and the page turns
those into text — so adding a third language touches one file. Gauge names carry
their real spelling on the page (*Paraná*, *Iguazú*, *Zárate*) while the CLI prints
ASCII, because a Windows console is cp1252 and an accent there is a crash.

The change is measured against the *previous reading*, not "yesterday": gauges have
gaps, and calling a two-day gap a one-day rise would overstate how fast the river
moved. Where there is a gap, the page says how many days it spans.

## Status

Built:

- `ina-a5`: client, catalogue paging, observation download, cache, quality guards.
- `parana_crecida`: gauge roster, INA threshold parsing, the DuckDB store, the data
  tests, the export gate, view 1, and the daily GitHub Actions run.

Not built yet — see `.claude/plans` for the full plan:

- Views 2 to 5: the wave, upstream drivers, flooded area, method and limits.
- `data/parana_chain.json`, the reviewed downstream chain with river-km, derived
  from the HydroRIVERS topology and hand-corrected for the bad snaps.
- `normalize`: stage anomaly against the day-of-year percentile curves. Raw metres
  are not comparable across gauges, since every gauge has its own zero.
- `events`: rise, crest and recession detection per gauge.
- `lag`: travel time by cross-correlation and by crest matching, with a celerity
  fit stratified by wave amplitude.
- `track`: the live wave and its projected arrivals.
- The wave trajectory plot — river-km against time, where a flood wave is a
  diagonal streak whose slope is the celerity.

Events are defined relative to each gauge's own seasonal normal (the day-of-year
p90/p95 curves from rios-argentina's `climatology_final.json`) as well as against
the official levels. Both are worth having: the official levels are what people act
on, the percentiles are what make gauges comparable.

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
