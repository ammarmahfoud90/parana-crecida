# Stage 5 — Defect register and fix plan

Read-only. No code was changed. Dated 2026-11-05. Consolidates
[01](01_inspection.md), [02](02_science_review.md), [03](03_validation_and_area.md)
and [04](04_freshness_and_hydrology.md).

**Re-verification.** Every Critical and High item below was re-read in the code
or re-derived from `origin/main` before being listed as confirmed; line numbers
in this file are the re-verified ones and supersede any that drift from the
earlier stages. One earlier count is corrected: `track.json` carries **11**
projections, not twelve.

**Severity.** Critical = a published number is demonstrably wrong. High = a core
assumption is unsupported and plausibly biases published numbers, or a known
defect reaches a reader. Medium = a real error source, bounded or latent.
Low = documentation, dead code, cosmetics.

**Evidence.** *Proven* = settled from code or published data in this audit.
*Documented* = established against ESA/Copernicus/Earth Engine docs or
peer-reviewed literature. *Inferred* = follows from code reading, not measured.

---

## Critical

### D1 — The headline's comparative claims are anchored on a failed satellite pass

| | |
|---|---|
| **Severity** | Critical · **Evidence** Proven |
| **Evidence** | `water_km2 − flood_km2` is water inside the fixed permanent-channel mask. Across 30 of 31 published cycles it is stable at mean **5,219.4 km², sd 76.4 (1.5%)**. The cycle ending **2026-06-30** reads **4,469.9 km²** — deficit 750 km², **z = −9.8** — and the loss is confined to two *adjacent* corridors: `ita_ibate_corrientes` −50.7% (300.7 vs 609.8 median), `posadas_ita_ibate` −25.3% (1,283.1 vs 1,717.5). The other six move by <2%. A 20 km-wide corridor cannot lose half its channel for twelve days. |
| **Files** | `src/parana_crecida/export.py:842-844` (`lowest`/`highest` = plain `min`/`max`), `site/index.html:529,535-536,539-542,553`, `site/gauges.html:507` + `:209-210`, `site/data/floodwater.json`, `site/data/river_today.json` (`verdict.water`) |
| **Consequence** | `+87%` and "5.9 times as much" on the front page, and "1.9 times the driest pass" in the gauges-page verdict, are all computed against this cycle. On the next-lowest cycle (2026-03-26) they would read **+77%** and **11.2×** — wrong in opposite directions, because 2026-06-30's `flood_km2` of 733.7 is not the record minimum either (383.6 is). |
| **Fix** | (a) Exclude or flag the cycle; (b) replace `min`/`max` on a single row with a robust anchor (e.g. the 10th percentile of complete cycles, or a stated multi-cycle low) and publish which cycles it came from; (c) add the channel-plausibility guard in D3/D9 so such a cycle can never anchor anything again. |

### D2 — Adjacent corridors overlap and every river-wide total is a plain sum over them

| | |
|---|---|
| **Severity** | Critical · **Evidence** Proven (corridor area); Inferred (water volume) |
| **Evidence** | Each centreline is buffered 20 km (`flooded.py:61,163-167`) and neighbours share a gauge endpoint, so each overlap is a ~20 km-radius region **centred on the river**. Reconstructing the polygons locally in Albers equal-area reproduces every committed `corridor_km2` to within 0.1–0.5%, then gives seven pairwise overlaps of **1,328–1,651 km²**: sum of the eight corridors **65,827 km²**, union **55,938 km²**, duplicated **9,889 km² (17.7%)**. |
| **Files** | `src/parana_crecida/export.py:838` (`corridor_km2` sum), `:810-811` (water/flood sums), `src/parana_crecida/riverfilm.py:316-324`, `src/parana_crecida/mapframes.py:213-215`, `src/parana_crecida/flooded.py:163-167` |
| **Consequence** | The published `corridor_km2` of **65,635 km²** describes ground covering **55,938 km²** — certainly 17.7% too large, no estimation involved. The water totals are summed identically, so an estimated **≈700 km²** of channel water (~7% of the 9,728 km² headline) is counted twice; that figure assumes uniform water density along each reach and is order-of-magnitude only. All three published totals — headline, map-frame caption, film caption — carry it. |
| **Fix** | Reduce the mask over the dissolved union. `mapframes.union_geometry()` (`mapframes.py:68-74`) already does `.dissolve(maxError=1000)` and is used to *draw* the frames — the primitive is imported and simply not used for the number. Then publish the union area as `corridor_km2`. |
| **Note** | Commit `73a539c` reads the film's areas agreeing with the export "to a rounding" as corroboration. They agree because both sum the same overlapping corridors. Two implementations of one error are not a cross-check. |

---

## High

### D3 — A reduction over no observed pixels returns 0.0 km², not "no data"

Severity High · Evidence **Proven**. `src/parana_crecida/floodwater.py:196`:
`return float(next(iter(stats.values()), 0) or 0) / 1e6`. `reduceRegion` returns
`None` when no unmasked pixel lies in the region; `or 0` makes that **0.0 km²**.
`flooded.water_area_km2` correctly returns `None` instead (`flooded.py:227-228`).
**Consequence:** the one rule that must not be broken — missing observations are
not zero inundation — is broken at the lowest level, and partially observed
corridors silently under-report because unobserved pixels stay masked and
contribute nothing to the sum. This is the mechanism behind D1.
**Fix:** return `None` on a null reduction, propagate it as a gap, and name the
band explicitly instead of `next(iter(...))`.

### D4 — `flood_km2` has never been validated; only total water has

Severity High · Evidence **Proven**. `scripts/validate_mask.py:175` scores
`fw.water_mask(...)`; `flood_only` appears nowhere in the file.
**Files:** `scripts/validate_mask.py`, `data/mask_skill.json`,
`site/index.html:539-542`, `site/data/csv/flooded_area.csv`.
**Consequence:** the figure the product is named for, and the basis of the 5.9×
claim and the dashed chart series, carries no validation at all; its error is
that of the water mask *plus* the 1984–2021 permanent-water mask, and D1 shows
the two are not independent.
**Fix:** score inside and outside the permanent channel separately, and gate
publication on the beyond-channel score.

### D5 — The published IoU is dominated by the permanent channel

Severity High · Evidence **Proven**. From `site/data/floodwater.json`, the
permanent channel is **56–93% of the detected water class** (median 84%; ~75% at
the cycle nearest the validation date). So most of the 0.69 measures whether the
mask can find a river whose location JRC already supplies.
**Consequence:** the same defect the project correctly rejected in the
JRC-bajante calibration — "tunes a flood mask on the one thing it is not for"
(`floodwater.py:9-11`) — in a subtler form.
**Fix:** as D4; and publish the permanent-water-alone baseline, because the
margin over it is the real measure of what the radar adds.

### D6 — Tuning and evaluation share one date and three reaches, and the gate inherits it

Severity High · Evidence **Proven**. `scripts/tune_mask.py:36` and
`scripts/validate_mask.py:132` both default to `--day 2026-10-22`;
`tune_mask.py:38` defaults to `corrientes_goya,parana_villa_constitucion,goya_la_paz`
— which now score **0.48, 0.53, 0.61**, the three worst on the river.
`export.SKILL_FLOOR = 0.45` is applied to those same in-sample per-reach IoUs
(`export.py:792`); `corrientes_goya` clears it by 0.03.
**Consequence:** global parameters fitted to the hard cases and reported as
river-wide skill; a publication gate selecting partly on single-date noise.
**Fix:** Stage 3 A2 — freeze a tuning manifest, then evaluate on held-out dates
and reaches, and drive the gate from held-out scores.

### D7 — A spurious 78.91 m reading is published and percentile-ranked at 100

Severity High · Evidence **Proven**. One row in 956,312 falls outside
`SANE_RANGES["m"]`: Libertador, 2026-08-22, **78.91 m**, against a series
otherwise topping out near 20 m. `validate()` detects it and correctly
classifies it as an isolated outlier — a `WARN` by design
(`validate.py:218-243`). `origin/main:site/data/csv/river_daily.csv` line 648:
`2026-08-22,26254,Libertador,main,187.7,78.91,100.0,regulated`.
**Files:** `validate.py:218-243` (detects, cannot act),
`export.py:498-518` (publishes), `site/data/csv/river_daily.csv`,
`site/data/river_stage.json`, `data/climatology_parana.json` (Libertador is one
of its 47 series, so a rebuild admits it permanently).
**Consequence:** a sensor glitch published in the downloadable CSV **and** ranked
as the highest 22 August on record.
**Fix:** give `WARN` an effect — exclude flagged rows from the export, or filter
them at load with the exclusion recorded.
**Checked clean:** does not reach `drivers.json`; Libertador is tier 3
`regulated`, so it cannot corrupt a travel time.

### D8 — `track.json` still ships the withdrawn arrival-date forecast

Severity High · Evidence **Proven**. `origin/main:site/data/track.json` carries
**11** projections with `expected`, `earliest`, `latest`, `days_ahead` — e.g.
Bella Vista `expected 2026-10-30`, `days_ahead -5` — under a note reading "When
the crest now in the river is due at the gauges below it". The method was
measured at **23% inside window, 9-day median error** (`data/track_skill.json`)
and withdrawn.
**Files:** `src/parana_crecida/track.py`, `export.py` (`export_track`),
`site/data/track.json`, `site/wave.html:156,207` (dead `colDue`/`colWindow`/
`colNow` strings).
**Consequence:** the display withdrawal is real and correctly implemented —
`wave.html:436-453` renders only the anchor line and a why-no-dates note — but
the public JSON is the copy that gets scraped and cited, which is the project's
own argument about the flooded-area CSV (`export.py:525-528`).
**Fix:** stop emitting `expected`/`earliest`/`latest`, or mark the file
explicitly withdrawn and keep only the front position.

### D9 — Nothing records observed area, image count, or acquisition dates

Severity High · Evidence **Proven**. `floodwater.rebuild()` stores `days`,
`water_km2`, `flood_km2` and nothing else (`floodwater.py:316-318`). The
superseded module stored `images` (`flooded.py:406`), `window_starts` (`:403`),
a `seen` reduction (`:300-308`, `:550-555`) and enforced
`MIN_COVERAGE = 0.95` (`:250`).
**Consequence:** one number per reach per cycle with no denominator; the real
cadence per reach cannot be recovered from the product at all; D1 had to be
inferred from the stability of the channel term because the data carries nothing
better; and `method.html:372` asserts composites "cover the whole corridor every
time", which is the property the dropped check used to verify.
**Fix:** reduce `water_mask(...).mask()` in the same call; store `seen_km2`,
`images` and the acquisition dates; refuse to publish a cycle below a stated
coverage.

### D10 — The staleness gate watches a field that advances whether or not anything was measured

Severity High · Evidence **Proven**. `floodwater.rebuild()` sets
`fetched_on = date.today()` on every successful run by design
(`floodwater.py:291-295`); `crecida freshness` tests only that field, against
`max_rain_age_days` = 10 (`cli.py:964-972`), and never `latest.day`. Live:
`fetched_on` 2026-11-04 (1 day old) while `latest.day` is 2026-10-28 (**8 days**,
reaching **14** before the next cycle lands). Per-reach Earth Engine failures are
logged and not re-raised (`floodwater.py:306-309`).
**Consequence:** the gate cannot distinguish 8 days from 80; a reach can stall
indefinitely with a green run. The Earth Engine step is `continue-on-error`
(`daily.yml:83`), so a total failure surfaces only 10 days later.
**Fix:** gate on the age of `latest.day` and of each reach's newest cycle, with a
tolerance derived from the repeat cycle plus processing lag.

### D11 — On `gauges.html` the satellite figure is undated and in the present tense

Severity High · Evidence **Proven**. `site/gauges.html:507` renders
`evidenceWater(km2, ratio, river_km)` → "Radar measures 9,728 km² of water over
1,477 km of river, 1.9 times the driest pass" (`:209-210`), under the present-tense
headline "The Paraná is in a major flood". `v.water.day` ("2026-10-28") and
`v.water.driest_day` ("2026-06-30") are **in the payload and rendered nowhere**,
on the same page that labels the gauges "(1 day behind)" and styles them stale
above three days (`:535-542`).
**Consequence:** an intermittent satellite measurement presented as continuous
detection, with a multiple from the D1 cycle. `index.html:160-161,549` does this
correctly ("closed on {day}"), so the right wording already exists in the project.
**Fix:** render the pass date and an age; adopt wording such as
"satellite-derived open-water extent, updated when valid observations become
available".

### D12 — VH below −25 dB sits at or under the noise floor, with no lower bound

Severity High · Evidence **Documented**. `floodwater.py:95`:
`VH_THRESHOLD_DB = -25.0`. ESA's IW NESZ requirement is −22 dB or better, with
measured values −23 to −30 dB across incidence angles and cross-pol the more
noise-affected channel. The S1GBM reports the Bayesian-optimal VH water
threshold as **−22.9 dB** and that thermal noise becomes observable below
**−22 dB**. Earth Engine's own catalog example masks `< −30 dB` as edge noise;
`vh.lt(-25)` admits everything down to the band floor.
**Consequence:** part of the water class may be noise-dominated and
non-stationary across the swath; residual border-noise artefacts count as water.
**Fix:** raise toward the published optimum, add a lower bound (e.g. −30 dB), and
re-sweep including the value in use — which the committed sweep never tested
(`tune_mask.py:97` stops at −24).

### D13 — VH is the weaker polarisation for this product's dominant error, and for its worst reaches

Severity High · Evidence **Documented**. Flooded vegetation raises backscatter by
double bounce, which is weaker in cross-pol than co-pol; a Saale River comparison
found VV detecting 152 km² against VH's 67 km². The one dedicated SAR evaluation
of the **Paraná delta wetlands** concludes L-band, **HH**, and steeper incidence
angles perform best, citing penetration through *cortadera*. This project uses
C-band, **VH**, no angle control — the opposite on all three, in the reach that
study covers and where its scores fall to 0.48–0.67.
**Consequence:** the polarisation was chosen on wind sensitivity alone
(`floodwater.py:44-48`) with no accounting of the flooded-vegetation cost; both
effects are real and pull opposite ways.
**Fix:** use VV and VH together — the collection carries both and is already
filtered to dual-pol scenes — and state the trade-off.

### D14 — The validation reference shares the radar's blind spot

Severity High · Evidence **Documented**. Sentinel-2 MNDWI cannot see under a
canopy either; a SWOT assessment over this basin found SWOT detecting floodplain
inundation that multispectral products miss specifically under vegetation.
Separately, `optical_water()` builds the "truth" as a **±6-day median composite**
of up to 10 scenes (`validate_mask.py:69-90`) while `validate_mask.py:8-11`,
`index.html:294` and `method.html:370` describe water seen directly on a date.
**Consequence:** recall of 0.80 is recall against a reference blind where the
radar is blind, so the published IoU cannot bound the error the project itself
calls dominant.
**Fix:** stratify by land cover and label the vegetated stratum unmeasured;
score single dates, not composites; bound the canopy miss with L-band, SWOT or
coherence on a subset.

### D15 — Orbits, look directions and platforms are mosaicked and thresholded together

Severity High · Evidence **Documented**. `floodwater._vh()` filters only
`instrumentMode` and polarisation (`floodwater.py:121-130`) — no
`orbitProperties_pass`, no `relativeOrbitNumber_start`, no `platform_number` —
then `.mosaic()`s and applies one global threshold. The `angle` band and
`relativeOrbitNumber_start` are both in the collection and unused. Over water
backscatter falls with incidence angle while over land it is flat; the S1GBM
normalises to 38° and reports differences **up to 5 dB** from un-normalised
aggregation, roughly twice this product's land–water margin. Earth Engine's own
guidance is that filtering to a homogeneous subset is usually necessary.
**Consequence:** a geometry-dependent threshold presented as a single physical
constant. The nine per-reach VV thresholds the project discarded as a defect were
partly absorbing exactly this; the 6.5 dB gorge disagreement is close to the 5 dB
the literature attributes to geometry.
**Fix:** filter to one pass direction and ideally a fixed relative orbit per
corridor, or normalise with the `angle` band; record the platform.

### D16 — The export gate runs eleven gauge-only tests and none on the satellite product

Severity High · Evidence **Proven**. All checks in `validate.validate()`
(`validate.py:97-353`) query the `observation`, `gauge` and `threshold` tables;
grepping the module for `flood|water|radar|corridor` returns only a comment. Yet
`export_floodwater(result)` is gated on that `ValidationResult`
(`export.py:753-758`).
**Consequence:** the satellite product passes the gate unconditionally regardless
of content. D1 is `complete: true` with eight of eight reaches reporting.
**Fix:** add satellite data tests — channel plausibility, coverage floor,
monotonic series length, flood ≤ water, corridor-fraction bounds.

### D17 — No test asks whether the live product tracks the river

Severity High · Evidence **Proven**. `tests/test_flooded.py:367`
(`test_every_published_area_moves_with_the_river`) is the only such test; it
reads the **superseded** `data/flooded.json`, and on this run it **skipped**
("the store is behind the committed measurement", `test_flooded.py:428`;
584 passed, 3 skipped). `tests/test_floodwater.py` is entirely structural.
Relatedly, every published `stage_correlation` belongs to the old VV product
(`export.py:585-608`), so the live mask has no published relation to the gauge
record at all.
**Consequence:** the project's strongest scientific guard — and the lesson it
describes as hard-won — does not cover what it ships, and was not executing.
**Fix:** port the test to the live product; make the skip a failure; measure the
stage–area relation on the live mask with rising and falling limbs separated,
published as context rather than as validation.

### D18 — `README.md` still lists the gorge as withheld; it has published since 2026-10-30

Severity High · Evidence **Proven**. `README.md:26-28`: "area for the gorge above
Posadas and for the Delta (the measurement does not track the river there)".
`puerto_iguazu_libertador` (IoU 0.83) and `libertador_posadas` (0.90) are both in
the published `stretches` and contribute **597.0 km²** to the headline. The
parenthetical reason is also wrong for the Delta, whose code reason is "never
scored against optical water" (`export.py:789`).
**Fix:** rewrite the summary; it describes the pre-`73a539c` product.

---

## Medium

| ID | Defect | Evidence | Files | Consequence · Fix |
|---|---|---|---|---|
| D19 | The superseded VV pipeline still runs daily, commits, and serves `site/data/flooded.json`, which no page fetches | Proven | `daily.yml:97`, `flooded.py`, `export.py:861` | Two published figures for the same stretch by two methods (1,644.7 vs 1,691.9 km²), nothing saying which. Retire it or mark the file superseded |
| D20 | The window is 11 days, not 12 (`filterDate` end-exclusive), and the cycle grid is synthetic | Proven | `floodwater.py:104,128`, `:235-254` | The whole 31-date grid reproduces as `2026-10-30 − LAG_DAYS − 12i`, the build date — bin edges presented as passes. With a 12-day repeat an 11-day window can drop one relative orbit from *every* cycle. Fix the bound; label dates as window ends |
| D21 | No unclassified category; `complete: true` means every reach returned a row, not that any row is sound | Proven | `floodwater.py:134-146`, `export.py:818` | Unobserved ground is indistinguishable from dry. Add a third state and carry it to the page |
| D22 | Reported area covers the whole corridor; the validated area covers 16–98% of it | Proven | `floodwater.py:184`, `data/mask_skill.json` | `corrientes_goya` publishes a 9,474 km² corridor on a score over 1,503 km² (16%), cloud-selected and so biased drier. Require a minimum `seen` fraction; publish it |
| D23 | The unit is asserted from a hand-typed flag; INA's `family` is overwritten | Proven | `validate.py:94`, `cli.py:808`, `store.py:55-62`, `ina_a5/models.py:36,75-76` | Two sources of truth for stage vs discharge; a disagreement would go unnoticed. Persist `family` and assert it against the flag |
| D24 | Threshold matching ignores the river field | Proven | `thresholds.py:205-245`, `data/ina_thresholds.csv` | `San Fernando`'s official threshold comes from the **Luján**, matched on station name alone. Require river agreement or record the mismatch |
| D25 | `exposure.py` attributes its table to "the validated VH mask"; the code uses the superseded VV mask and JRC-occurrence-only permanent water | Proven | `exposure.py:5-7` vs `:92-101` | The recorded reason for withholding is not reproducible from the file. Correct the docstring or the code. (Correctly withheld today — keep it so) |
| D26 | `export.py` credits the urban-false-positive fix to change detection, which was rejected | Proven | `export.py:676-678` vs `exposure.py:17-21` | The live absolute-VH mask is **measured** to read Posadas as a lake; no settlement mask exists. Add one (ESA WorldCover class 50 or GHSL built-up) |
| D27 | `scripts/tune_mask.py` does not run at HEAD | Proven | `tune_mask.py:109` calls `fw.dry_reference`, removed in `73a539c` | The change-detection IoUs (0.38–0.45) cannot be regenerated; `−25 dB` was never swept and HAND never was. Restore or rewrite the sweep |
| D28 | HAND is used past its resolution, accuracy and drainage definition | Documented | `floodwater.py:79,101,146`, `MERIT/Hydro/v1_0_1` | 92.77 m grid against a 10 m band on a 60 m reduction; MERIT DEM has 2 m-or-better accuracy over 58% of land; the 0.5 km² drainage threshold is a global default its authors say to recalculate. Recalculate from `upa`, or state HAND is inert on the floodplain |
| D29 | Speckle filtering degrades the mask below its stated resolution | Inferred | `flooded.py:70-75`, `floodwater.py:137` | `focal_median(50 m)` on 10 m ≈ 110 m effective resolution against a 60 m grid, so shorelines move more than the "pixel or two" claimed. State it; estimate the shoreline area bias |
| D30 | `.mosaic()` makes each cycle a patchwork of dates and geometries, not an aggregate | Inferred | `floodwater.py:136` | During the October rise the series moves +39% in one cycle, so parts of one figure correspond to different stages. Use a defined composite statistic |
| D31 | No staleness gate on `map_frames`, `river_film`, `track`, `wave`, `flooded` | Proven | `cli.py:900-978` | The map's water overlay can go arbitrarily stale with a green run (all currently current). Gate them |
| D32 | "It updates itself, three times a day" | Proven | `method.html:220,329` | True of the gauges, false of the satellite product, which advances at most once per 12-day cycle. Scope the sentence |
| D33 | Three published values for the Delta's stage correlation | Proven | `README.md:715` (−0.39), `:837,857` (−0.44), `method.html:271,380` (−0.39), live `flooded.json` (**−0.23**) | Prose quotes a frozen number as a current measurement; `test_flooded.py:432` asserts only `< 0`. Recompute or cite with its date |
| D34 | Travel times are resolution-limited on 11 of 12 legs, with n as low as 7 | Proven | `data/travel_times.json` | A median of 1.0 d with p25=p75=1.0 on daily data is not celerity. Handling is already honest (flag + method-page note); the per-leg medians should not be read as measurements. The aggregate (≈68 km/day ≈ 0.79 m/s) is physically coherent |
| D35 | Reference and `seen` masks from the validation are not archived | Proven | `data/mask_skill.json` (8 fields/reach) | No confusion matrix, no TP/FP/FN areas, no interval — the comparison cannot be re-scored by anyone, including a later audit stage. Archive them |
| D36 | Reference cloud masking keeps SCL 0, 1, 2 and 7; no shadow or cloud-proximity buffer | Proven | `validate_mask.py:73-75,81` | Class 2 is topographic cast shadow, on the valley walls of the two best-scoring reaches; the scene filter is tile-level over 200 km corridors. Mask all nine non-surface classes; add s2cloudless and a shadow buffer |

---

## Low

| ID | Defect | Files |
|---|---|---|
| D37 | The "0.7–0.9 IoU" band the site measures itself against is uncited; comparable thresholding baselines score ≈0.55, so 0.69 is a good result quoted against the wrong yardstick | `index.html:293`, `method.html:370` |
| D38 | README sections "Only full passes count", "What it shows" (1,651 km², r = 0.57) and "The loop" read as current but describe the superseded product | `README.md:393-416` |
| D39 | `animation.py` is orphaned: default −16 dB VV threshold, JRC-occurrence-only permanent water, output `site/media/flood.gif` which does not exist and no page references | `animation.py:36,89,110`, `cli.py:1139`, `README.md:413-416` |
| D40 | Three published lengths for one run of river: 1,440.5 (chain) / 1,477.3 (published) / 1,492 km (measured centrelines) | `parana_chain.json`, `floodwater.json`, `tests/test_flooded.py:80` |
| D41 | `OUT_OF_RANGE_SHARE = 1.0` is documented as a share and compared against a percentage, so the gate blocks at 1%, not 100% | `validate.py:40-42,216-217` |
| D42 | `map_frames.json` publishes stale `calibrated: false` and `threshold_db: -17.0` for reaches that publish and are not measured with a VV threshold; `mapframes.py:132-143` carries two stacked comments, one describing the superseded rule, and `:161` a no-op `calibrated` set | `mapframes.py:161,213-215,223-227` |
| D43 | "the missing quarter of the water" against a measured recall of 0.80 (a fifth), and `floodwater.py:66` says four fifths | `index.html:206,292` |
| D44 | `limitGorge` translation key holds text entirely about the Delta; `wave.html:156,207` keep dead `colDue`/`colWindow`/`colNow` headings | `index.html:105`, `wave.html:156,207` |
| D45 | MERIT's `−9999` ocean sentinel satisfies `hnd ≤ 10`, so HAND passes ocean rather than rejecting it — latent if the product reaches the Delta. Also: the Earth Engine catalog describes `hnd` as geoid-referenced elevation, which is wrong; the code is right | `floodwater.py:146` |

---

## Unconfirmed

Listed so none is mistaken for a finding. Each needs a credential or data not
available in this audit.

| ID | Question | What would settle it |
|---|---|---|
| U1 | Is D1's 750 km² deficit missing acquisitions or mask failure? | Count IW VH scenes over `posadas_ita_ibate` and `ita_ibate_corrientes` for 19–30 June 2026 and reduce `water_mask(...).mask()` over each. **One EE query** |
| U2 | Is any relative orbit systematically excluded by the 11-day window (D20)? | `aggregate_array('relativeOrbitNumber_start')` per corridor per cycle |
| U3 | The exact double-counted water in D2 | Reduce the mask over `union_geometry(stretches)` for one cycle and compare with the sum |
| U4 | Does VH over these corridors approach NESZ (D12)? | Per-product noise vectors, or the backscatter histograms |
| U5 | The magnitude of the under-canopy miss (D13, D14) | L-band ALOS-2, SWOT L2 HR (needs Earthdata), or S1 SLC coherence (not in Earth Engine) |
| U6 | Does HAND ≤ 10 m clip real water on the floodplain (D28)? | Sweep HAND including ∞ on held-out dates |
| U7 | Agricultural false positives and seasonal drift over the floodplain | A dry/wet backscatter pair per corridor |
| U8 | Radar–optical co-registration error in the validation | Boundary offset over the permanent channel |
| U9 | Are Yacyretá afluente, efluente and Confluencia genuinely all 25,000/30,000 m³/s? | One look at `reporte_diario_*.pdf` (a copy is in `.reports/`) |
| U10 | Is INA's San Fernando the same installation as the roster's (D24)? | INA station metadata |
| U11 | Does INA's `family` agree with the roster's `flow` flags for all 47 series (D23)? | A live a5 catalogue call |
| U12 | Were −25 dB and HAND ≤ 10 m ever swept interactively? | Unknowable from the repository; the manifest must record them as unreproducible |

---

## Fix batches

Ordered within each batch by severity. Nothing here has been implemented.

### (a) Pipeline and data

| order | fix | defects |
|---|---|---|
| a1 | Reduce over the dissolved corridor union; publish the union as `corridor_km2` | D2 |
| a2 | Return `None` on a null reduction and propagate it as a gap | D3 |
| a3 | Record `seen_km2`, `images` and acquisition dates per reach per cycle; refuse a cycle below a coverage floor | D9, D21, D22 |
| a4 | Exclude or flag the 2026-06-30 cycle; replace `min`/`max` with a robust anchor | D1 |
| a5 | Fix the `filterDate` bound; label cycle dates as window ends | D20 |
| a6 | Filter by pass direction and relative orbit, or normalise with `angle`; record platform | D15 |
| a7 | Raise the VH threshold toward the published optimum and add a lower bound | D12 |
| a8 | Add VV alongside VH | D13 |
| a9 | Add a settlement mask | D26 |
| a10 | Let a `WARN` exclude a reading from the export | D7 |
| a11 | Stop emitting `expected`/`earliest`/`latest` from `track.json` | D8 |
| a12 | Persist INA's `family` and assert it against the roster flag | D23 |
| a13 | Require river agreement in threshold matching | D24 |
| a14 | Use a defined composite statistic instead of `.mosaic()` | D30 |
| a15 | Recalculate HAND from `upa` with a basin-specific drainage threshold | D28 |
| a16 | Retire the superseded VV pipeline, or mark `flooded.json` superseded | D19, D42 |
| a17 | Remove or rewrite `animation.py`; restore `tune_mask.py` | D27, D39 |

### (b) Website and user experience

| order | fix | defects |
|---|---|---|
| b1 | Date the satellite figure on `gauges.html`, drop the present tense, adopt "updated when valid observations become available" | D11 |
| b2 | Re-anchor and restate the `+87%` / `5.9×` / `1.9×` claims once a1–a4 land | D1 |
| b3 | Rewrite `README.md:26-28` and mark the superseded README sections | D18, D38 |
| b4 | Publish the per-reach `seen` fraction beside every score | D22 |
| b5 | State that the optical reference shares the radar's blind spot; stop calling a 13-day composite a day | D14 |
| b6 | Scope "three times a day" to the gauge data | D32 |
| b7 | Cite the 0.7–0.9 band or drop it | D37 |
| b8 | Reconcile the Delta correlation and the three river lengths | D33, D40 |
| b9 | Publish area bias and an interval beside every area | D35 |
| b10 | Rename published quantities: "open water detected by radar" and "open water beyond the permanent channel"; no surface to read as "flooded area" until the vegetated stratum has a bound | D4, D13, D14 |
| b11 | Cosmetics: `limitGorge` key, dead `wave.html` strings, the "missing quarter", stale `map_frames` fields | D42, D43, D44 |

### (c) Tests and validation gates

| order | fix | defects |
|---|---|---|
| c1 | Add satellite data tests to `validate()`: channel plausibility, coverage floor, flood ≤ water, corridor-fraction bounds, series alignment | D16, D1, D3 |
| c2 | Port the tracks-the-river test to the live product and make its skip a failure | D17 |
| c3 | Add a test that the river total equals the union-based area, not the sum | D2 |
| c4 | Freeze `data/tuning_manifest.json`, recording the unreproducible parameter choice | D6, U12 |
| c5 | Build the held-out evaluation sets of Stage 3 A2 and drive `SKILL_FLOOR` from them | D6 |
| c6 | Score inside and outside the permanent channel separately; add the permanent-water-alone baseline | D4, D5 |
| c7 | Archive reference and `seen` masks; publish the confusion matrix, area bias and a block-bootstrap interval | D35 |
| c8 | Mask all nine non-surface SCL classes; add s2cloudless and a shadow buffer | D36 |
| c9 | Add a test that no published figure is anchored on a single cycle | D1 |
| c10 | Fix the `OUT_OF_RANGE_SHARE` comment | D41 |
| c11 | Keep exposure withheld until it has a settlement stratum with its own confusion matrix | D26 |

---

## What the fixes need that is not available here

**An Earth Engine credential** is required for a1–a9, a14, a15, c3, c5–c8, and
for U1–U4, U6, U7. One exists in CI as `EE_SERVICE_ACCOUNT_KEY`
(`daily.yml:81`) and is demonstrably live; this session had none, which is why
nothing in this audit was measured. `docs/earth-engine-service-account.md`
documents the setup.

**Already in Earth Engine, no new acquisition needed**, for everything else:
`COPERNICUS/S1_GRD` (with `relativeOrbitNumber_start`, `platform_number` and
`angle`, all currently unused), `COPERNICUS/S2_SR_HARMONIZED`,
`COPERNICUS/S2_CLOUD_PROBABILITY`, `LANDSAT/LC08|LC09/C02/T1_L2`,
`JRC/GSW1_4/MonthlyHistory` (the out-of-period set, 1984–2021 — the only design
with zero temporal leakage), `JRC/GSW1_4/GlobalSurfaceWater`,
`ESA/WorldCover/v200`, `MERIT/Hydro/v1_0_1`.

**Not available, and the gap must be stated rather than filled.** Any reference
that sees under a closed canopy over these reaches on a date in the published
series. SWOT L2 HR needs a PO.DAAC Earthdata login; ALOS-2 PALSAR-2 ScanSAR is
not in the public Earth Engine catalog; S1 SLC coherence needs SLC, which Earth
Engine does not ingest. Until one of these is obtained, **recall in the
vegetated stratum cannot be measured and must be reported as unmeasured, not as
0.80**.

**Needs no credential at all** — doable immediately: a10–a13, a16, a17, all of
batch (b), and c1–c4, c9, c10. Including D2's fix, which is a one-line change to
reduce over a geometry the repository already constructs.

**Cheapest informative step.** Enumerate how many reach-dates clear a 60%
optical-visibility bar (Stage 3 A2.2). It needs one credential and no new data,
and if the answer is that two reaches have three usable dates, then this product
cannot be validated to the standard its front page implies — which is worth
knowing before any of the above is built.
