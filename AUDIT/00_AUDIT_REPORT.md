# Paraná flood tracker — full audit report

A nine-stage audit of
[`parana-crecida`](https://github.com/ammarmahfoud90/parana-crecida), a
satellite-derived flood-monitoring product for the Argentine Paraná,
published at
[ammarmahfoud90.github.io/parana-crecida](https://ammarmahfoud90.github.io/parana-crecida/).

Conducted 2026-11-05 against commit `a9cf6bf`. Concluded at `f65a90f`.

This document is the whole audit in one place. The nine stage reports
beside it carry the working — 4,300 lines of it — and are referenced
throughout. Where this summary and a stage report disagree, the stage
report is the record.

| | |
|---|---|
| **Scope** | Sentinel-1 ingestion and processing, flood-area calculation, validation design, INA gauge ingestion, exports, website, CI, documentation |
| **Found** | 45 confirmed defects, 2 of them critical; 12 questions that could not be answered without a credential |
| **Fixed** | 33 commits, 49 files; every critical and high finding either fixed or recorded with the reason it was not |
| **Tests** | 617 → 714 collected, 614 → 706 passing; five of the new tests found real defects |
| **Not done** | the held-out validation, because it needs Earth Engine and this session had none |

---

## The verdict, in three sentences

The instrument is competent; the measurement of the instrument is not yet
adequate. The threshold was tuned against the same date and three of the
same reaches it is scored on, what is scored is total water of which the
permanent channel is 56–93%, and `flood_km2` — the figure the product is
named for — has never been scored at all. None of that is fixed by this
audit, all of it is now stated on the pages and in the repository, and the
one number that was demonstrably wrong is withheld rather than published.

**The project's own instincts were right throughout.** It validated
against an independent sensor, published the score, withheld what could
not be scored, withdrew a forecast on measuring it at a 23% hit rate, and
documented its failures in prose most projects would have deleted. The
defects found were not failures of intent. They were, almost without
exception, in the *artefacts* the code produced — and nothing read those.

---

## What the nine stages did

### Stage 1 — Inspect the repository and establish the true pipeline
→ [`01_inspection.md`](01_inspection.md) · commit `44a99b6`

Read-only. Traced every published metric from imagery to displayed value.

**The central finding: there were two complete radar pipelines, both
running daily, and nothing said which produced the published figures.**
The live one is `floodwater.py` — Sentinel-1 IW VH, mosaicked over a
12-day window, despeckled at 50 m, water where VH < −25 dB and MERIT HAND
≤ 10 m, permanent channel removed, summed at 60 m, gated per reach on an
IoU of 0.45 against Sentinel-2. The superseded per-reach VV pipeline still
ran, still committed, and still served `site/data/flooded.json` — a file no
page fetched, carrying a *different area for the same reach* on a nearby
date.

16 contradictions between README, method page, map, metadata and source
comments, each cited to file and line.

### Stage 2 — Independent scientific review of the Sentinel-1 processing
→ [`02_science_review.md`](02_science_review.md) · commit `ae82d7d`

17 findings against ESA, Copernicus, Earth Engine and peer-reviewed
sources. The one that mattered was settled from the published data alone,
with no credential:

> `water_km2 − flood_km2` is water detected inside the fixed
> permanent-channel mask. Across 30 of 31 published cycles it held at
> **5,219 km² with a standard deviation of 76** — 1.5%. The cycle ending
> **2026-06-30 read 4,470**: a 750 km² deficit, **z = −9.8**, with the
> entire loss confined to two *adjacent* corridors (−50.7%, −25.3%) while
> the other six moved by under 2%.

A 20 km-wide corridor does not lose half its channel for twelve days. And
that cycle was the one the site published as the driest pass on record,
against which the headline measured both of its comparisons.

Also established: −25 dB sits 2–3 dB below the published VH optimum and at
the IW noise floor; VH carries less double-bounce than VV, so the
polarisation choice trades away sensitivity to the flooded vegetation the
project names as its dominant error; the one dedicated SAR study of the
Paraná delta wetlands recommends **L-band, HH, steep incidence angles** —
the opposite on all three; and the optical reference is blind under canopy
exactly where the radar is.

### Stage 3 — Validation design and area calculation
→ [`03_validation_and_area.md`](03_validation_and_area.md) · commit `5b8b2a4`

**The second critical finding, and this one needed no estimation.** Each
corridor is a 20 km buffer around a centreline, and neighbouring
centrelines meet at a gauge — so every adjacent pair overlaps in a 20 km
radius *centred on the river*. Reconstructing the polygons locally
reproduced every committed corridor area to within 0.1–0.5%, then gave:

- seven pairwise overlaps, **1,328–1,651 km²** each
- sum of the eight publishing corridors: **65,827 km²**
- union: **55,938 km²**
- published `corridor_km2`: **65,635 km² — 17.7% too large**

The water totals were summed identically, so roughly 700 km² of channel
was counted twice in the headline. `mapframes.py` already had the
`.dissolve()` call and used it to *draw* the frames while the number
printed beside them summed the overlaps.

On validation: `validate_mask.py` scores total water and never calls
`flood_only`, so the flood figure had no validation at all; the permanent
channel is 56–93% of the positive class; and tuning and evaluation share a
date and three reaches. A full held-out design was specified.

### Stage 4 — Observation frequency, freshness, hydrology
→ [`04_freshness_and_hydrology.md`](04_freshness_and_hydrology.md) · commit `772eaa7`

**The gauge side verified clean**, by direct query rather than assumption:
22 of 22 published values matched the store exactly, no duplicates, no
future dates, tier-1 coverage complete, the chain's upstream-area jump at
Corrientes matching the Paraguay confluence, Yacyretá cut hydraulically,
eleven tide-affected gauges excluded from routing. The twenty series
starting on exactly `1970-01-01` were checked and are genuine.

Three defects on top of it: a spurious **78.91 m** reading at Libertador
published in the downloadable CSV *with a day-of-year percentile of 100*,
after being correctly detected and classified; `track.json` still serving
eleven withdrawn arrival-date projections; and the unit of measurement
asserted from a hand-typed flag while INA's own `family` field was
overwritten.

On freshness: the published 12-day cadence is **synthetic** — the entire
31-date grid reproduces as `2026-10-30 − LAG_DAYS − 12i`, the day the file
was first built. Nothing recorded an acquisition date, image count or
observed area. The staleness gate watched `fetched_on`, which advances
every run whether or not anything was measured.

### Stage 5 — Defect register
→ [`05_defect_register.md`](05_defect_register.md) · commit `327bbcf`

45 confirmed defects and 12 unconfirmed questions, ordered by severity and
evidence, with every critical and high item re-read in the code first.
Fixes grouped into three batches with credential needs marked per fix.

### Stage 6 — Pipeline, data and documentation
→ [`06_changes_pipeline.md`](06_changes_pipeline.md) · commits `8fbfb72`…`7ee33b6`

Sixteen commits. `area_km2` returns `None` rather than `0.0`. The window
made inclusive — it was eleven days, which with a 12-day repeat could drop
one relative orbit from *every* cycle. Coverage, scene counts, acquisition
dates, orbits and platforms recorded per cycle, with the coverage floor
restored. The river measured over the dissolved union, and a summed total
refused outright. Anchors made a median over three cycles. Satellite data
tests added to the gate. A flagged reading excluded from exports. The
withdrawn forecast removed. The unit held in one place and checked against
INA. Thresholds required to agree on the river. The freshness gate pointed
at the newest measurement. The superseded pipeline retired. A −30 dB noise
floor added without moving the threshold. `tune_mask.py` repaired and
extended; `animation.py` deleted. `docs/METHOD.md` written.

### Stage 7 — Website and user experience
→ [`07_changes_website.md`](07_changes_website.md) · commits `b7689f5`…`f761ce8`

Inspected the live site first, in a fresh browser session. Every
measurement now carries its window, age, unit, reach and validation
status. Gaps break the chart instead of being drawn across. A withheld
total no longer shares the `role="alert"` box with a failed fetch. The map
and method pages corrected. Disclaimers raised to "not an alert system"
everywhere. Five of seven pages never updated `document.title` on a
language switch.

Verified against three data states — what was published, the real
post-stage-6 export, and a union-measured series with injected gaps.

### Stage 8 — Regression tests and validation gates
→ [`08_tests.md`](08_tests.md) · commits `9345663`…`9d662ca`

+97 tests. A `conftest.py` separating credential-dependent checks, which
are written out rather than described. A committed tuning manifest with
twelve tests holding it true. Nineteen tests that read the *published
files* rather than exercising functions. Twenty-one that run every export
against a failing gate and read the constant, the metadata and the prose
side by side.

**Five of the new tests found real defects**, each fixed in the same
commit — including `river_daily.csv` naming its column `level_m` while
carrying m³/s, and `export_flooded` writing to the published directory
before consulting the gate.

### Stage 9 — Final verification
→ [`09_final_report.md`](09_final_report.md) · commit `f65a90f`

Suite, processing checks and browser behaviour re-run; documentation
reconciled; verdict, findings, changes, validation, tests, blockers and
priorities recorded in the A–G form.

---

## The two critical findings, and what happened to them

### 1. The headline rested on a failed pass

`+87%` on the front page, `5.9×` for the flood multiple, and `1.9×` in the
gauges verdict were all measured against the 2026-06-30 cycle — a
9.8-sigma failure in channel detection. On the next-lowest cycle they
would have read **+77%** and **11.2×**: wrong in opposite directions.

*Fixed.* `export._implausible` rejects any cycle finding less than 90% of
the series' median channel detection — tested against the real 31-cycle
record, where it rejects that cycle and nothing else, and keeps the
9,727.5 km² record flood. Anchors are now a median over three cycles and
publish the cycles they came from.

### 2. Every total was a sum over overlapping corridors

*Fixed in code; open in data.* `floodwater.union_geometry` dissolves the
publishing corridors and measures the river once over the union. Because
no arithmetic recovers a union figure from per-reach ones, the export
publishes **no whole-river total at all** until that measurement exists,
with the reason attached — a figure known to be 17.7% too large is worse
than an absent one.

**That is the current state of the live site.** The headline is withheld
and `crecida freshness` fails until `crecida floodwater` runs with an
Earth Engine credential and rebuilds the series.

---

## What was measured in this audit

Two things, both from data already in the repository, neither needing a
credential. These are **new results**, distinct from figures the project
had already documented.

**The corridor overlap, exactly** — 9,889 km², 17.7% of the summed area,
from a polygon reconstruction that reproduces every committed corridor
area to within half a per cent.

**The live mask's relation to the gauge record.** Every stage correlation
the project had ever published belonged to the superseded VV mask, because
`_stage_correlation` was only called from `export_flooded`. Computed for
the live VH mask:

| reach | IoU (in-sample) | r with downstream gauge |
|---|---|---|
| puerto_iguazu_libertador | 0.83 | +0.30 |
| libertador_posadas | 0.90 | +0.12 |
| posadas_ita_ibate | 0.87 | **+0.56** |
| ita_ibate_corrientes | 0.64 | +0.66 |
| corrientes_goya | 0.48 | +0.66 |
| goya_la_paz | 0.53 | +0.78 |
| la_paz_parana | 0.67 | +0.51 |
| parana_villa_constitucion | 0.61 | +0.66 |

Every published reach is positive. The superseded mask had
`posadas_ita_ibate` at **−0.28** — the Yacyretá reach, where it read
353 km² of standing reservoir as flood — and the VH mask's permanent-water
definition fixes exactly that.

This is **context, not validation**, and is published as such: extent at a
given stage depends on topography, connectivity, vegetation and the limb
of the wave, so a weak correlation is expected on some reaches. What it
establishes is the negative — no published reach is measuring something
that moves against the river.

**Reported as the repository states it, not re-measured:** the IoU table
(river-wide 0.69, recall 0.80, precision 0.82; per reach 0.48–0.90,
against Sentinel-2 MNDWI on 2026-10-22), with its six limits now printed
beside it.

**Not run:** the held-out validation of stage 3. It needs Earth Engine.
No out-of-sample metric is claimed anywhere.

---

## Verification

```
$ python -m pytest -q
706 passed, 8 skipped

$ crecida validate
0 failed, 2 warnings, 17 checks

$ crecida freshness
Error: no whole-river total is published      ← correct, by design
```

Browser, fresh session, seven pages × two languages: no console errors,
every local link and asset 200, titles and `lang` in agreement, no
untranslated slot, chart gaps rendered and described, map slider
relabelling to the composite window, no horizontal overflow at 375×812.

CI: `tests` green on Python 3.12 and 3.14.

The eight skips are each accounted for — four need Earth Engine, two
cannot exercise their subject until the union measurement exists, one
guards a deliberately withdrawn record, one is a page with no error box by
design. **No test was weakened or deleted to make the suite pass**, and
one test of mine that was wrong was fixed rather than the code it
accused.

---

## What remains

**Blockers before operational use**

1. An Earth Engine credential — without it the whole-river figure cannot
   be measured at all.
2. A rebuild of the water series: the window fix and the union measurement
   both change what every cycle means, so the year must be re-measured.
3. Reference imagery for a held-out evaluation. Available in Earth Engine
   and not yet used; nothing about the current score is out-of-sample.
4. A reference that sees under canopy. Until one exists, recall in the
   vegetated stratum is **unmeasured**, not 0.80.
5. Two items needing a human and a PDF: whether INA's San Fernando is the
   same installation as the roster's, and whether three identical dam
   thresholds are genuine.
6. No field validation has been done, and none is claimed.

**The five next steps, by impact**

1. **Run `crecida floodwater` with a credential and rebuild the series.**
   One command. Nothing else matters as much, because until it runs the
   product's main figure does not exist.
2. **Count how many reach-dates clear a 60% optical-visibility bar.**
   Cheap, and it decides everything downstream. If two reaches have three
   usable dates, this product cannot be validated to the standard its
   front page implies.
3. **Score inside and outside the permanent channel separately**, and run
   the permanent-water-alone baseline. Same date, no new imagery. If the
   mask does not beat "the river is where it always is" beyond the
   channel, it does not yet measure flooding.
4. **Build the held-out set from JRC monthly history, 1984–2021** — the
   only design with zero temporal leakage and full coverage, including the
   Delta, which has never been scored.
5. **Add VV alongside VH, and a settlement mask.** Both move every
   published figure, so both belong behind (3) and (4). The evidence for
   each is already measured.

---

## Rules this audit held to

Stated because they shaped what is and is not in it.

- Nothing was treated as correct because the code ran or the tests passed.
  The suite was green throughout, with the published total 17.7% wrong.
- No measurement, citation or metric was invented. Where a figure could
  not be computed it is marked unmeasured, not estimated.
- No correction is called validated for having been implemented. The
  union measurement is written and has not been run; the report says so.
- No threshold was moved to improve a score. The −30 dB noise floor was
  added because it changes no pixel's class; −25 dB stays where it is,
  with the reason now in the code.
- A documented limitation was preferred to an unsupported claim, and an
  absent figure to an invented one — which is why the headline is
  currently withheld.

---

## The audit set

| file | lines | what it holds |
|---|---|---|
| [`01_inspection.md`](01_inspection.md) | 652 | the production algorithm step by step; the experiment table; 16 contradictions with file and line |
| [`02_science_review.md`](02_science_review.md) | 776 | 17 findings on the SAR processing, with citations |
| [`03_validation_and_area.md`](03_validation_and_area.md) | 845 | the corridor overlap; the validation design; the area-calculation audit |
| [`04_freshness_and_hydrology.md`](04_freshness_and_hydrology.md) | 707 | cadence and freshness; the gauge record verified by query |
| [`05_defect_register.md`](05_defect_register.md) | 444 | 45 defects, 12 open questions, three fix batches |
| [`06_changes_pipeline.md`](06_changes_pipeline.md) | 150 | batch (a): what changed in the pipeline and what was deliberately not |
| [`07_changes_website.md`](07_changes_website.md) | 195 | batch (b): the pages, and what was verified in the browser |
| [`08_tests.md`](08_tests.md) | 225 | batch (c): the commands, the real outcomes, and what the tests found |
| [`09_final_report.md`](09_final_report.md) | 325 | the A–G report: verdict, findings, changes, validation, tests, blockers, priorities |

Working code and documentation produced alongside:
[`docs/METHOD.md`](../docs/METHOD.md) (the statement of record),
[`data/tuning_manifest.json`](../data/tuning_manifest.json) (what the
parameters were fitted on), `tests/test_external.py` (the four
measurements awaiting a credential).
