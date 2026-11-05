# Stage 4 — Observation frequency, freshness, and hydrological audit

Read-only. No code was changed. Dated 2026-11-05. Follows
[`AUDIT/01_inspection.md`](01_inspection.md),
[`AUDIT/02_science_review.md`](02_science_review.md) and
[`AUDIT/03_validation_and_area.md`](03_validation_and_area.md).

**Scope and sources.** Published values are read from `origin/main`, which is
what the Pages workflow deploys; its most recent run exported on
2026-11-04T23:26:54-03:00. Gauge-record facts come from querying
`data/crecida.duckdb` directly — **that local store ends 2026-10-28 while the
published data runs to 2026-11-03**, so store comparisons are made on dates both
cover. No Earth Engine credential was available, so no acquisition was counted;
where that is the binding limit it is marked.

Severity scale as in Stages 2 and 3: **Critical**, **High**, **Medium**,
**Low**, **Supported**.

**What this stage found.** The hydrological side is the strongest part of this
project: 22 of 22 published stage values match the store exactly, there are no
duplicate or future-dated readings, tier-1 coverage is complete, and the
roster's flags, tiers and refusals are disciplined and documented. Three
specific defects sit on top of it — one spurious reading published and
percentile-ranked at 100, a withdrawn forecast still shipping in a published
JSON, and a unit of measurement asserted from a hand-typed flag rather than read
from INA. The freshness side is weaker: the satellite series' dates are
synthetic, no acquisition is recorded anywhere, the staleness gate watches a
field that advances whether or not anything was measured, and on the gauges page
the satellite figure appears undated and in the present tense next to readings
from yesterday.

---

# Part A — Observation frequency and freshness

## A1 — **High.** The published 12-day cadence is synthetic, phase-locked to the day the series was first built

Every published cycle end is exactly 12 days after the previous one:
2025-11-02 → 2026-10-28, 31 cycles, 360 days, no exceptions. That regularity is
not an observation. It is produced by `floodwater._grid()`
(`src/parana_crecida/floodwater.py:235-254`), which on a cold start returns

```python
[last - timedelta(days=window_days * i) for i in range(KEEP_CYCLES - 1, -1, -1)]
```

with `last = today - LAG_DAYS` and `LAG_DAYS = 2` (`flooded.py:93`), then on
every later run anchors to `known[-1]` and adds exactly `window_days`.

`git log data/floodwater.json` shows the file was first created on
**2026-10-30**. Reproducing the cold start from that date:

| | |
|---|---|
| `last` = 2026-10-30 − 2 days | **2026-10-28** |
| earliest of 31 cycles = `last` − 360 days | **2025-11-02** |
| published earliest cycle | **2025-11-02** |

An exact match. **The entire date axis of the headline product is an artefact of
the calendar day on which the author happened to run the build.** It has no
relationship to Sentinel-1's orbital repeat phase, to any acquisition, or to the
hydrograph. A reader looking at `index.html`'s chart — "Un año de río, pasada por
pasada" / "A year of river, pass by pass" — sees 31 evenly spaced points and
reasonably concludes each is a pass. Each is a 12-day bin boundary chosen by the
build date.

The project is explicit that anchoring the grid is deliberate and gives a good
reason (`floodwater.py:237-244`: computing it backwards from today "moves every
date by a day each time it runs", which orphaned a full set of map frames). The
anchoring is right. What is missing is any statement that the resulting dates
are bin edges rather than observations.

## A2 — **High.** An 11-day window against a 12-day repeat makes coverage phase-dependent, and the constellation's phase changed mid-series

Stage 1 C11 established that `floodwater._vh()` uses
`.filterDate(start, end)` (`floodwater.py:128`), which is end-exclusive, with
`start = end − 11 days` (`floodwater.py:280`). The window is therefore **11
days wide**, not 12, where `flooded.py:202` compensates explicitly for the same
semantics.

The consequence is sharper than a one-day rounding. With a 12-day orbital
repeat and 11-day windows stepping 12 days, each relative orbit falls at the
*same* position in every window. So a relative orbit whose acquisition lands on
the single excluded day is **absent from every cycle in the series**, not
occasionally. For a corridor covered by two relative orbits — which
`flooded.py:274-277` records is the case here — losing one permanently halves
the observation density for the whole series, invisibly.

And the phase was not stable. Stage 2 S14 established that Sentinel-1C became
fully operational in early May 2025, Sentinel-1D data opened on 17 April 2026,
the nominal six-day repeat was re-established on **24 June 2026**, Sentinel-1A
was terminated on **29 June 2026**, and ESA notes the new pair's ground-track
pattern is **shifted by one day** relative to the old A/B configuration
([Copernicus Data Space, 24 June 2026](https://dataspace.copernicus.eu/news/2026-6-24-s1cd-final-orbital-configuration-achieved);
[Copernicus Data Space, 28 May 2026](https://dataspace.copernicus.eu/news/2026-5-28-sentinel-1-orbital-reconfiguration-dates)).

So a window geometry whose coverage depends on phase met a constellation whose
phase moved, in the window **19–30 June 2026** — which is the cycle Stage 2 S1
showed to be a 9.8-sigma failure in channel detection, localised to two adjacent
corridors. I am not claiming this is the proven cause; I am recording that the
mechanism exists, is verifiable from code and ESA documentation, and predicts
exactly the kind of failure observed at exactly the date observed.

**Cannot be verified here.** Which relative orbits serve each corridor and
whether any is systematically excluded. One Earth Engine query settles it:
`aggregate_array('relativeOrbitNumber_start')` over each corridor per cycle.

## A3 — **High.** Nothing in the pipeline records an acquisition date, an image count, or an observed area

The actual cadence per reach — the thing Part A asks for — **cannot be
determined from the published product at all**, and I could not determine it
here without a credential.

| recorded by the superseded pipeline | recorded by the live pipeline |
|---|---|
| `images` per window (`flooded.py:406`) | — |
| `window_starts` per cycle (`flooded.py:403`) | — |
| observed area, via a `seen` reduction (`flooded.py:300-308`, `550-555`) | — |
| coverage threshold `MIN_COVERAGE = 0.95` (`flooded.py:250`) | — |

`floodwater.rebuild()` stores `days`, `water_km2` and `flood_km2`
(`floodwater.py:316-318`) and nothing else. So per reach per cycle there is one
number with no denominator, no count of contributing acquisitions, and no
acquisition dates. Stage 3 B3 is the same finding from the area side; it is
restated here because it is also the reason Part A's central question is
unanswerable from the published data.

The fix is cheap and already written elsewhere in the repository: reduce
`water_mask(...).mask()` in the same call, and store `seen_km2`,
`images` and the list of acquisition dates beside every area.

## A4 — **High.** On `gauges.html` the satellite figure is undated and in the present tense, beside gauge readings from yesterday

`site/gauges.html:505-511` renders the verdict's water evidence through

```js
evidenceWater: (km2, ratio, km) =>
  `Radar measures ${km2} km² of water over ${km} km of river, ${ratio} times the driest pass`,
```

(`gauges.html:209-210`; Spanish at `:130-131`). Under a present-tense headline —
"The Paraná is in a major flood, along most of its length"
(`stateHighWide`, `gauges.html:190`) — the published payload is:

```json
"water": {"km2": 9727.5, "day": "2026-10-28", "ratio": 1.9,
          "flood_km2": 4301.9, "driest_day": "2026-06-30",
          "driest_km2": 5203.6, "river_km": 1477.3, "stretches": 8}
```

`day` and `driest_day` are both **in the payload and rendered nowhere**. The
same page shows `data_age_days` for the gauges as "(1 day behind)" and applies a
`stale` class above three days (`gauges.html:535-542`). So on one page, in one
block, the gauge state is dated and age-styled while the satellite measurement —
eight days old today, and reaching fourteen days old before the next cycle lands
— is presented as a present-tense fact with no date at all.

This is the specific failure Part A warns about: an intermittent satellite
measurement described as continuous detection. The suggested wording in the
brief applies directly. `index.html` by contrast does this correctly — it shows
`passDay` and says "In the satellite's last complete pass, closed on {day}"
(`index.html:160-161`, `:549`) — so the project already has the right wording
and has not applied it on the page where it matters most.

The `1.9` ratio and the `driest_*` fields also carry the 2026-06-30 anchor that
Stage 2 S1 showed to be corrupt, so this is a second published surface affected
by it, not just the index headline.

## A5 — **High.** The staleness gate watches a field that advances whether or not anything was measured

`floodwater.rebuild()` sets `fetched_on = date.today()` on every successful run
regardless of whether a cycle was added, and the docstring says so deliberately
(`floodwater.py:291-295`: "checked today, the newest pass is still the one from
last week is a current answer").

`crecida freshness` then tests **`fetched_on`** against `max_rain_age_days`,
default 10 (`cli.py:966-977`), and never looks at `stretches[].days[-1]` or at
`latest.day`.

Live evidence from `origin/main`:

| field | value | age today (2026-11-05) |
|---|---|---|
| `fetched_on` | 2026-11-04 | 1 day |
| `latest.day` | 2026-10-28 | **8 days** |
| next cycle due | 2026-11-09, published ~2026-11-11 | up to **14 days** |

The gate reports everything current. Eight days is normal for a 12-day cycle —
the point is that the gate **cannot distinguish 8 days from 80**. Per-reach
Earth Engine failures are caught, logged and not re-raised
(`floodwater.py:306-309`), so a reach can stop advancing indefinitely while
`fetched_on` keeps moving.

Compounding it: the tolerance is 10 days while the worst-case normal age is 14
days, so even a gate pointed at the right field would need a different
threshold. And because the Earth Engine step is `continue-on-error: true`
(`.github/workflows/daily.yml:83`), a total credential or API failure is only
caught when `fetched_on` itself goes stale — 10 days later.

**What the gate must test instead:** the age of `latest.day`, against a
tolerance derived from the repeat cycle plus the processing lag, and separately
the age of each reach's newest cycle so that one stalled reach is visible.

## A6 — **Medium.** The product does not separate the five timestamps Part A asks it to

Part A asks the product to distinguish daily INA observations, satellite
acquisition dates, composite windows, processing timestamps, and the age of the
newest valid satellite observation. What exists:

| required distinction | present? | where |
|---|---|---|
| daily INA gauge observations | **yes** | `river_today.json` `last_data_day` 2026-11-03, `data_age_days` 1, shown with a "(n days behind)" label |
| satellite **acquisition** dates | **no** | not recorded anywhere (A3) |
| composite observation window | **partly** | `window_days: 12` is published, but the actual window is 11 days (A2) and only the end is published, not the start |
| processing / publication timestamp | **yes** | `generated_at` on five payloads, `fetched_on` on `floodwater.json` |
| age of the newest valid satellite observation | **no** | derivable by a reader from `latest.day`, shown nowhere, gated nowhere (A4, A5) |
| missing / incomplete / rejected observations | **partly** | `complete` and `stretches` count per cycle, and a `withheld` list with reasons — both good. But `complete: true` means every reach returned a row, not that any row is sound (Stage 3 B4), and nothing records a rejection |

The gap is consistent: the gauge product's provenance is well instrumented and
the satellite product's is not, which is the wrong way round for a project whose
own memory records that "the radar measurement is the point; the gauge pages are
supporting evidence".

## A7 — **Medium.** "It updates itself, three times a day" is true of the gauges and false of the satellite product

`site/method.html:329` (EN; ES at `:220`):

> "A tracker for the Paraná flood built from public data: the river heights INA
> publishes daily, Sentinel-1 radar imagery, and satellite rainfall over the
> basin. **It updates itself, three times a day.**"

Three crons is accurate for the workflow (`daily.yml:14-16`) and for the gauge
data. The satellite product advances at most once per 12-day cycle, and on most
days the Earth Engine step measures nothing at all — which `daily.yml:91-95`
states plainly in a comment. A reader meeting that sentence on the method page
of a satellite flood product will read it as the satellite figure updating three
times a day.

## A8 — **Medium.** Five published payloads have no staleness gate

`crecida freshness` checks `river_today.json`, the rainfall block inside
`drivers.json`, the existence of `gauges.geojson` and `departamentos.geojson`,
the newest day in `river_stage.json`, and `fetched_on` in `floodwater.json`
(`cli.py:900-978`). It does **not** check:

| payload | its own freshness field | currently |
|---|---|---|
| `map_frames.json` | `generated_for` | 2026-10-30 |
| `river_film.json` | `generated_for` | 2026-10-30 |
| `track.json` | `generated_for` / `as_of` | 2026-11-04 / 2026-11-03 |
| `wave.json` | `generated_at` | 2026-11-04 |
| `flooded.json` | `fetched_on` / `stale_days` | 2026-11-02 / 0 |

All are currently as fresh as their inputs allow — the map and film correctly
guard on the last cycle drawn, and both are current for the 2026-10-28 cycle. So
this is an absence of a gate, not a present stale file. But the map's water
overlay is the product's second most prominent surface, and it can go
arbitrarily stale with a green run.

## A9 — **Supported.** What the freshness design gets right

Recorded so none of it is lost:

- **`crecida freshness` runs last, after the deploy** (`daily.yml:149-157`), on
  the stated reasoning that the export gate deliberately lets stale data through
  so an INA outage keeps the site up rather than taking it down, and that a
  stall should turn the run red rather than freeze the page quietly. This is a
  well-argued design and the comment explaining it is better than most
  production monitoring.
- **Three spaced crons at odd minutes** (`daily.yml:8-16`), with the recorded
  reason that GitHub drops scheduled runs under load and that a `0 12 * * *` was
  dropped on its first day. The job is idempotent and commits nothing when
  nothing changed.
- **The export gate is a hard gate**: `export_floodwater` and the rest raise
  `ExportBlocked` on a failed data test, so a bad gauge day cannot reach the page
  (`export.py:753-758`). The signature makes "export without validating"
  unwriteable. (Its limitation is Stage 1 C14 — the tests are gauge-only.)
- **The push retries with rebase** (`daily.yml:129-137`) for the documented race
  where another run lands mid-export.
- **Gauge freshness is surfaced to the reader** with an age in days and a
  `stale` class above three days (`gauges.html:535-542`).
- **The commit step stages only generated paths** (`daily.yml:117`).
- **`_grid()` anchors rather than recomputing from today** (A1), for a recorded
  reason.

Confirmed working, not assumed: `gh run list --workflow=daily.yml` shows the
eight most recent scheduled runs all `success`, and the bot has been committing
`data/floodwater.json`, so the Earth Engine credential is live and the pipeline
is healthy. A stale local checkout, not a pipeline failure, explains the gap
between my working tree and `origin/main`.

---

# Part B — Hydrology

## B1 — **High.** A spurious 78.91 m reading is published, and percentile-ranked at 100

Querying `data/crecida.duckdb` for stage readings outside
`validate.SANE_RANGES["m"] = (-10.0, 60.0)` returns exactly one row across
956,312 observations:

| series | gauge | day | value |
|---|---|---|---|
| 26254 | Libertador | **2026-08-22** | **78.91 m** |

Libertador's own record runs 0.00–~20 m (the 78.91 is its series maximum; the
next highest is far below). `validate.validate()` detects it: the row is out of
range, 1 of 17,961 rows is 0.006%, which is below
`OUT_OF_RANGE_SHARE`, and it is not the latest reading — so it is correctly
classified as an **isolated outlier** and raised as a `WARN`
(`validate.py:218-243`). That classification is right, and the reasoning at
`validate.py:185-188` is right: one bad row in twenty thousand is a glitch in
INA's archive and should be reported, not take the site down.

**But nothing removes it from the export.** The warning is a line in the run
log. The value is published:

`origin/main:site/data/csv/river_daily.csv`, line 648:

```
2026-08-22,26254,Libertador,main,187.7,78.91,100.0,regulated
```

So the pipeline publishes the bad reading **and** publishes a day-of-year
percentile of **100.0** for it — ranking a sensor glitch as the highest 22
August on record and shipping that in the CSV the method page offers for
download. The same value is in `river_stage.json`'s Libertador series, where it
will set the y-axis of any chart drawn from it.

Checked and clean: the outlier does **not** reach `drivers.json` (no Libertador
block), and Libertador is tier 3 `regulated`, so it is not a routing node and
cannot corrupt a travel time. But Libertador **is** one of the 47 series in
`data/climatology_parana.json`, so a climatology rebuild will admit 78.91 into
the 22 August distribution permanently.

**Affected files:** `src/parana_crecida/validate.py:218-243` (detects, does not
act), `src/parana_crecida/export.py:498-518` (`river_rows()`, publishes it),
`site/data/csv/river_daily.csv`, `site/data/river_stage.json`,
`data/climatology_parana.json`.

The gap is structural, not a typo: there is no path by which a `WARN` influences
what is written. A severity that cannot change an outcome is a comment.

## B2 — **High.** `track.json` still publishes the withdrawn arrival-date forecast

The project withdrew arrival dates on measured evidence and recorded the
decision carefully. `data/track_skill.json`:

```
inside_window_pct: 23,  median_error_days: 9,  mean_error_days: 13.9,
within_3_days_pct: 24,  within_7_days_pct: 46,
baseline_median_error_days: 14,  scored: 933,  occasions: 200
```

Commit `3e88351` and the README state the dates are "built, measured and are now
gone", on the correct reasoning that "an error the size of the thing being
predicted is not a forecast".

The **display** withdrawal is real and correctly implemented:
`wave.html`'s `renderAhead()` (`:436-453`) reads `track.projections` only to
decide whether to show the section, then renders `anchorLine` — where the crest
is and when it peaked — plus a `frontWhyNoDates` explanation. No projected date
is rendered. Verified in the markup (`wave.html:76-81`): the `#ahead` section
contains a heading, the anchor line and that explanation, nothing else.

But `origin/main:site/data/track.json` is a public file and it carries twelve
explicit projections:

```json
{"series_id": "26264", "name": "Bella Vista", "river_km": 786.0,
 "km_from_anchor": 68.8, "expected": "2026-10-30",
 "earliest": "2026-10-30", "latest": "2026-10-31", "days_ahead": -5, ...}
```

with a `note` that reads as a live product: *"When the crest now in the river is
due at the gauges below it, from travel times measured over the whole record. It
says when, not how high."* Nothing in the file marks the method as withdrawn or
quotes its 23% hit rate — though `skill` is embedded, so the evidence travels
alongside, which is better than nothing.

This is the project's own stated principle, applied to the forecast instead of
the mask. `export.py:525-528`, on the flooded-area CSV:

> "The water surface as published, not the superseded table. A CSV is the copy
> that gets downloaded, cited and kept, so it is the last place an old
> measurement should survive."

A JSON fetched from a public URL is that copy. Anyone scraping `track.json` —
or any model reading it — gets dated crest arrivals for a method the project
does not stand behind.

Two smaller leftovers from the same withdrawal: `expected: "2026-10-30"` against
`as_of: "2026-11-03"` gives `days_ahead: -5`, so some "projections" are already
in the past; and `wave.html:156` and `:207` still define the withdrawn table's
column headings (`colDue` "Llegaría"/"Due", `colWindow`, `colNow`), which no
`data-t` attribute references.

**Affected files:** `src/parana_crecida/track.py`,
`src/parana_crecida/export.py` (`export_track`), `site/data/track.json`,
`site/wave.html:156,207`.

## B3 — **Medium.** The unit of measurement is asserted from a hand-typed flag, and INA's own field is discarded

The `observation` table is `(series_id, day, value)` — **no unit column**,
confirmed by `PRAGMA table_info`. `validate._unit_expr()` therefore re-derives
it:

```sql
CASE WHEN g.flags LIKE '%flow%' THEN 'm3/s' ELSE 'm' END
```
(`validate.py:92-94`)

Meanwhile `packages/ina-a5` already models the authoritative value:
`UNITS = {"level": "m", "flow": "m3/s"}` keyed on the series' `family` field
from the a5 API (`ina_a5/models.py:36,75-76`). And `cli.py:808` goes further and
**overwrites** it:

```python
family="flow" if "flow" in gauge.flags else "level",
```

So there are two sources of truth for whether a series is stage or discharge —
INA's `family`, available at ingestion, and a hand-maintained flag in
`gauges.py` — and the pipeline validates against the hand-maintained one while
discarding the authoritative one. If they ever disagree, nothing notices.

This matters here specifically because the project has already met the failure.
The README records that INA's report lists a *stage* for Puerto Pilcomayo while
the series held under that name is discharge, and `data/ina_thresholds.csv`
shows it: `Paraguay / Puerto Pilcomayo, m, 5.35, 6` filed as
`unmatched_report_row`. The disagreement was caught — but by the threshold
matcher (B5), not by a unit check at ingestion, and only because the report
happened to name it.

Minor fragility in the same expression: `flags LIKE '%flow%'` is a substring
match, so any future flag containing the letters "flow" silently reclassifies a
gauge's unit. No such flag exists today.

**Affected files:** `src/parana_crecida/store.py:55-62` (schema),
`src/parana_crecida/validate.py:92-94`, `src/parana_crecida/cli.py:808`,
`packages/ina-a5/src/ina_a5/models.py:36,75-76`.

## B4 — **Medium.** Threshold matching ignores the river, and one threshold comes from a different river

`thresholds.py:213` builds the lookup as

```python
by_name = {normalise_name(g.name): g for g in roster.GAUGES}
```

— station name only. The report's river is parsed and carried into the CSV as
`ina_river`, and **never used as a matching constraint**, although the module's
own docstring names this exact hazard: "No fuzzy matching, because 'Parana' the
city, the river and the province all appear in these tables"
(`thresholds.py:19-20`).

The consequence is visible in the committed CSV. Of the 16 `official` rows, one
is:

| series | gauge | roster flags | INA river / station | alert | evac |
|---|---|---|---|---|---|
| 26294 | San Fernando | `tide_affected, unrouted` | **Lujan** / San Fernando | 3 | 3.5 |

A Luján-river threshold is attached to a Paraná estuary gauge because the
station names coincide. It may well be the same physical installation — San
Fernando sits at the Luján mouth on the Paraná de las Palmas system — but the
pipeline cannot know that from a name, and nothing in the repository records the
check. The river field is right there.

Every other official row's river is consistent with its gauge (`Paraná`,
`Paraná/Delta`, `Paraguay`, `Iguazú`).

**Affected files:** `src/parana_crecida/thresholds.py:205-245`,
`data/ina_thresholds.csv`.

## B5 — **Medium.** Three dam stations carry identical thresholds; worth checking against the PDF

`data/ina_thresholds.csv` gives Yacyretá afluente, Yacyretá efluente and
Confluencia all **25,000 / 30,000 m³/s**, and the two unmatched dam rows
(Guaíra Porto, Represa Itaipú) both **20,000 / 25,000 m³/s**. The module
promises "never a value carried over from another gauge"
(`thresholds.py:11-13`).

Two separate pairs each sharing a value makes a blanket INA convention for dam
flows more likely than a parser bug — if a row were being duplicated I would
expect one repeated value, not two distinct repeated values. But it is the
pattern the module exists to prevent, and the check is one look at
`reporte_diario_*.pdf`, a copy of which is in `.reports/`. Recorded as
unverified rather than as a defect.

**Supported, and the stronger half of this module:** the matcher **refuses on a
unit mismatch** rather than guessing — "mislabel every day as catastrophic.
Refuse on unit, do not guess" (`thresholds.py:224-230`) — which is why Puerto
Pilcomayo's stage threshold was not attached to a discharge series. The CSV also
retains every unparsed and unmatched report row with an explicit status, so it
can be checked line by line against the PDF. 16 `official`, 31
`not_in_report`, 9 `unmatched_report_row`, all accounted for.

## B6 — **Medium.** Travel times are resolution-limited on eleven of twelve legs, and the honest flag does not reach every surface

`data/travel_times.json`, all twelve legs:

| leg | km | median d | p25–p75 | n | resolution-limited |
|---|---|---|---|---|---|
| Corrientes → Empedrado | 57 | 1.0 | 1.0–1.0 | 38 | yes |
| Empedrado → Bella Vista | 69 | 2.0 | 2.0–3.0 | 17 | yes |
| Bella Vista → Goya | 84 | 1.0 | 1.0–2.0 | 25 | yes |
| Goya → Esquina | 131 | 3.0 | 2.5–4.0 | 27 | **no** |
| Esquina → La Paz | 85 | 2.0 | 2.0–4.0 | 19 | yes |
| La Paz → Santa Elena | 27 | 1.0 | 1.0–2.0 | **10** | yes |
| Santa Elena → Hernandarias | 37 | 1.0 | 1.0–1.75 | 14 | yes |
| Hernandarias → Paraná | 79 | 2.0 | 1.0–2.0 | 18 | yes |
| Paraná → Diamante | 59 | 2.0 | 1.0–2.0 | 39 | yes |
| Diamante → San Lorenzo | 81 | 2.0 | 1.0–3.0 | 12 | yes |
| San Lorenzo → Rosario | 21 | 2.0 | 1.75–2.0 | **8** | yes |
| Rosario → Villa Constitución | 50 | 2.0 | 1.0–2.5 | **7** | yes |

A median of 1.0 days with p25 = p75 = 1.0, fitted on daily data, is not a
measurement of celerity — it is "the next day", and the quantisation is the same
size as the signal. The project knows this, computes the flag, publishes it in
`travel_times.csv` and renders a note for it on the method page
(`method.html` `noteResolution`). That is the right handling.

The aggregate is nonetheless physically coherent, which is worth saying: the
medians sum to 21 days over 1,440 km, i.e. ≈68 km/day ≈ 0.79 m s⁻¹, inside the
usual range for Paraná flood-wave celerity. So the chain is not producing
nonsense; it is producing a plausible total out of twelve individually
uninformative legs, and three legs rest on 7–10 matched floods.

## B7 — **Medium.** No published stage–area relation exists for the live mask, and none of the existing ones is evidence of accuracy

Part B's instruction — do not treat correlation between gauge level and
satellite area as proof that either is accurate — is one the project already
states well. `export.py:573-583` explains that the correlation goes on the page
so "this stretch barely moved" reads as "we cannot tell here" rather than as a
statement about the flood, and the README's withdrawn area–stage section says
plainly that the 55% scatter finding "was partly a statement about the river and
partly a statement about the mask, and nothing here separates the two".

Two things follow that the project has not closed:

1. **Every published stage–area correlation belongs to the superseded VV
   product.** `_stage_correlation()` reads the raw `flooded.json` table
   (`export.py:585-608`), which is the old mask. Stage 1 established there is no
   equivalent for the live VH mask, no test asking whether the live product moves
   with the river, and that the one test that ever asked it covers the old product
   and skipped on this run. So the live headline product has **no published
   relation to the gauge record at all** — neither as validation nor as the
   explicit non-validation the project intends.
2. **The correlations themselves are not comparable across reaches.** They are
   computed against each reach's downstream gauge on the dates the mask happened
   to report, with n as low as 8 (`export.py:604-606`), pooled across rising and
   falling limbs. The project has already measured hysteresis in its own
   area–stage relation, so pooling the limbs is known to mix two relations. Any
   re-measurement on the live mask should separate them, as Stage 3 A2.6
   specifies.

The expected physics supports the project's caution rather than undercutting it:
extent at a given stage depends on local topography, channel–floodplain
connectivity, vegetation and whether the wave is rising or falling, so a weak or
reach-dependent correlation is the expected result, not a defect to be tuned
away. What is missing is the measurement, not a stronger claim.

## B8 — **Low.** Three different lengths are published for the same run of river

| source | Puerto Iguazú → Villa Constitución |
|---|---|
| `data/parana_chain.json` river_km (0.0 → 1,440.5) | **1,440.5 km** |
| `site/data/floodwater.json` `river_km`, the sum of corridor lengths | **1,477.3 km** |
| my measurement of the committed centrelines, Albers equal-area | **1,492 km** |

The site displays 1,477 km ("1,477 km de río, 8 tramos"). The corridor
centrelines are documented as "walked from the river topology"
(`flooded.py:121`) and a test asserts they match the chain
(`tests/test_flooded.py:80`), so a 37 km disagreement between the chain and the
corridor sum suggests that test checks endpoints rather than lengths. Not a
large error, but three numbers for one quantity, one of them on the front page.

## B9 — **Low.** `OUT_OF_RANGE_SHARE` is documented as a share and used as a percentage

`validate.py:40-42` reads:

```python
#: Above this share of a series being out of range, the series itself is
#: wrong rather than merely carrying a bad reading, and the export is blocked.
OUT_OF_RANGE_SHARE = 1.0
```

and `validate.py:216-217` computes `share = 100.0 * bad / total` before
comparing. So the gate blocks at **1%**, not at 100% as the comment reads. The
behaviour is the stricter, more sensible one; only the documentation is wrong.
Worth fixing because a reviewer reading `1.0` as "100% of the series" would
conclude the check can never fire.

## B10 — **Supported.** What the hydrological side gets right

Verified by direct query against `data/crecida.duckdb` (956,312 rows, 47
series, 1884-01-01 to 2026-10-28) and against the published payloads, not
assumed:

- **Exported matches displayed, exactly.** All 22 `river_stage.json` anchors
  equal the store's value for 2026-10-28, to the published precision. Zero
  mismatches.
- **No duplicate `(series_id, day)` pairs. No future-dated readings.** Both
  checks `validate.py` claims to enforce hold on the real data.
- **Tier-1 daily coverage is complete** for the last 365 days at twelve of
  thirteen gauges; Santa Elena is missing 17 days. Gaps are not silently
  interpolated — `store.series()` returns what exists.
- **The 1970-01-01 cluster is genuine, not an epoch artefact.** Twenty series
  begin on exactly 1970-01-01, which is the kind of coincidence that deserves
  suspicion; the first readings continue smoothly into the series (Libertad
  7.9 → 8.26 → 8.70 → 9.30), so these are real archive starts. Recorded because
  it looks alarming and is not.
- **Exact-zero readings are not used as nulls.** The highest share is Ibicuy at
  1.42% (198 rows), a tide-affected gauge where 0.00 near datum is a plausible
  reading.
- **Flow series are plausible and correctly separated**: Confluencia
  1,034–43,598 m³/s, Yacyretá afluente 5,100–42,800, efluente 5,700–39,000,
  Puerto Pilcomayo 508–12,579, Andresito 137–31,758 — all inside
  `SANE_RANGES["m3/s"]` and consistent with the Paraná's discharge regime.
- **The roster's tiers and flags are disciplined and documented**
  (`gauges.py:1-39`): `regulated`, `tide_affected`, `bad_snap`, `unrouted`,
  `flow`, each with a stated meaning and a stated consequence, and
  `is_routing_node` enforces them in code rather than by convention. Eleven
  gauges are flagged `tide_affected` and none of them is a routing node.
- **The chain is monotonic in river_km** (24 nodes, no inversions) and skips
  `unrouted` gauges. Its `up_skm` jumps from 951,665 to 2,110,625 km² at
  Corrientes, which is the Paraguay confluence — independent evidence the
  topology is right rather than merely ordered.
- **Dam regulation is handled hydraulically, not cosmetically.**
  `river_stage.json` carries `dams: [{name: Yacyreta, river_km: 429.68}]` and
  its note states "The line is cut at Yacyreta: the pool above the dam and the
  river below it are hydraulically separate." The twelve gauges above it are all
  flagged `regulated` and none is tier 1.
- **Gauge counts all reconcile**: 47 in the roster, 45 in `gauges.geojson`, 37
  in `river_today.json`, 24 chain nodes, 22 `river_stage.json` anchors. The ten
  absent from `river_today` are exactly the tier-3 off-main-stem and discharge
  series, which appear on `drivers.json` instead.
- **Thresholds carry provenance to the row**: `status`, `ina_station`,
  `ina_river`, `source_pdf`, `source_date`, with nothing invented and nothing
  dropped silently, and the unit refusal described in B5.
- **Rarity is published two ways that disagree, on purpose**: a day-of-year
  percentile and a Log-Pearson III return period, with the project's own note
  that both are wanted and they often disagree (`export.py:189-192`). The
  verdict's `rarest` block carries `years: 12.5` alongside `record_max: 6.92`,
  `record_max_year: 1998` and `record_years: 83`, so a reader gets the return
  period and the record it is drawn from.
- **The forecast was withdrawn on its own evidence**, which is the right call
  and rare. The remaining problem is distribution (B2), not judgement.

---

## Summary

**Scientifically supported, verified here**

Exported-to-displayed fidelity; no duplicates or future dates; tier-1 coverage;
flow/stage separation; roster tiers and flags; chain monotonicity and topology;
Yacyretá handling; tide-affected gauges excluded from routing; threshold
provenance and unit refusal; travel times published with n and a
resolution-limited flag; the forecast's withdrawal from the display; the
freshness check running after deploy; the hard export gate; gauge age surfaced
to the reader.

**Needs improvement**

| | |
|---|---|
| A1 | state that cycle dates are bin edges, not acquisitions |
| A2 | fix the 11-day window; filter or record relative orbit and platform |
| A3 | record acquisition dates, image counts and observed area per reach per cycle |
| A4 | date the satellite figure on `gauges.html` and drop the present tense |
| A5 | gate on the age of `latest.day` and of each reach's newest cycle, not on `fetched_on` |
| A6 | separate the five timestamps the product needs to distinguish |
| A7 | scope "three times a day" to the gauge data |
| A8 | gate `map_frames`, `river_film`, `track`, `wave`, `flooded` |
| B1 | let a `WARN` exclude a reading from the export, or filter the outlier |
| B2 | stop publishing `expected`/`earliest`/`latest` in `track.json`, or mark the file withdrawn |
| B3 | persist INA's `family` as the unit and assert it against the roster flag |
| B4 | require river agreement in threshold matching, or record the mismatch |
| B5 | check the three identical dam thresholds against the PDF |
| B6 | nothing to fix in the handling; the per-leg medians should not be read as celerity |
| B7 | measure the stage–area relation on the live mask, limbs separated, and keep framing it as context rather than validation |
| B8 | reconcile 1,440.5 / 1,477.3 / 1,492 km |
| B9 | fix the `OUT_OF_RANGE_SHARE` comment |

**Could not be verified in this stage**

- The real acquisition cadence per reach, and which relative orbits serve each
  corridor — no Earth Engine credential (A2, A3). One query each.
- Whether any relative orbit is systematically excluded by the 11-day window.
- The three identical dam thresholds against `reporte_diario_*.pdf` (B5).
- Whether INA's San Fernando is the same installation as the roster's (B4).
- Whether INA's `family` agrees with the roster's `flow` flags for all 47 series
  — it needs a live a5 call; the catalogue command fetches it
  (`cli.py:234-254`) but the comparison is not made.
- Anything about the published data after 2026-11-03, and anything in the store
  after 2026-10-28, which is where the local snapshot ends.

---

## Sources

- [Copernicus Data Space — S1C/D final orbital configuration achieved (24 June 2026)](https://dataspace.copernicus.eu/news/2026-6-24-s1cd-final-orbital-configuration-achieved)
- [Copernicus Data Space — Sentinel-1 orbital reconfiguration dates (28 May 2026)](https://dataspace.copernicus.eu/news/2026-5-28-sentinel-1-orbital-reconfiguration-dates)
- [Earth Engine Data Catalog — COPERNICUS/S1_GRD](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD) (ingestion latency; `relativeOrbitNumber_start`, `platform_number`, `angle`)
- [INA Sistema de Alerta Hidrológico](https://alerta.ina.gob.ar/) — the alert system the site links to and the source of the daily report PDF parsed into `data/ina_thresholds.csv`
