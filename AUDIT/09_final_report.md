# Stage 9 — Final verification and report

Dated 2026-11-05. Covers the whole audit: stages 1–4 (inspection and
review), 5 (register), 6–7 (fixes), 8 (tests), and the verification run
recorded here.

33 non-data commits. 49 files changed. The test suite went from 617
collected to 714, and from 614 passing to 706.

---

## A. Overall verdict

**Scientific readiness: not ready for operational use, and closer than it
was.** The instrument is a competent C-band threshold product and the
project's instinct — validate against something independent, publish the
score, withhold what cannot be scored — is the right one and rare. But the
single validation it has is in-sample: the threshold was tuned against the
same date, and three of the same reaches, that it is scored on. What is
scored is total water, of which the permanent channel is 56–93%, so most
of the 0.69 measures whether the mask can find a river whose location JRC
already supplies. And `flood_km2`, the figure the product is named for,
has never been scored at all. None of that is fixed by this audit; all of
it is now stated on the pages, in `docs/METHOD.md`, and in a committed
tuning manifest.

**Software reliability: materially improved, and the improvement is
load-bearing.** The defects found were not in the functions — they were in
the artefacts the functions produced, and nothing read those. A missing
observation became 0.0 km². A river-wide total summed corridors that
overlap by 17.7%. A sensor glitch of 78.91 m was detected, correctly
classified, and published anyway with a percentile of 100 attached. A
withdrawn forecast went on being served at a public URL for months after
the page stopped rendering it. Each is now fixed *and* guarded by a test
that reads the published file.

**Does the product support its claims?** Not yet for the whole-river
figure, which is why it is currently withheld rather than published wrong.
Per reach, with the caveats now printed beside them, yes — the per-stretch
areas are real measurements over their own corridors, each carrying its
validation score, and every published reach correlates positively with the
gauge below it. The gauge side of the product supports its claims fully
and always did.

**The honest one-line summary:** this is a good instrument with one
measurement of itself, taken on the data it was tuned on, and the site now
says so.

---

## B. Confirmed findings

Severities as in `05_defect_register.md`. "Fixed" means fixed and guarded
by a test; "open" means recorded and not fixed.

### Critical

| id | finding | evidence | files | consequence | status |
|---|---|---|---|---|---|
| D1 | Headline comparatives anchored on a failed pass | in-channel detection across 30 cycles: mean 5,219 km², sd 76 (1.5%); cycle 2026-06-30 at 4,470, **z = −9.8**, loss confined to two adjacent corridors (−50.7%, −25.3%) | `export.py:842-844`, `index.html`, `gauges.html` | `+87%` and `5.9×` on the front page, `1.9×` in the gauges verdict, all measured against a pass where the mask lost half the channel of two reaches | **fixed** — cycle rejected by `export._implausible`, anchors are a median over three cycles |
| D2 | Corridors overlap; every total was a plain sum | seven pairwise overlaps of 1,328–1,651 km²; sum 65,827 km² vs union 55,938; published `corridor_km2` 65,635 | `export.py:838,810-811`, `riverfilm.py`, `mapframes.py` | corridor area 17.7% too large with no estimation involved; ≈700 km² of channel double-counted in the headline | **fixed in code, open in data** — union measurement implemented; the total is withheld until Earth Engine runs |

### High — fixed

| id | finding | files |
|---|---|---|
| D3 | a null reduction returned 0.0 km²: no observation became no inundation | `floodwater.py:196` |
| D7 | 78.91 m at Libertador published with a percentile of 100 | `validate.py`, `export.py`, `river_daily.csv` |
| D8 | `track.json` served 11 withdrawn arrival-date projections | `track.py` |
| D9 | no coverage, scene count or acquisition date recorded anywhere | `floodwater.py` |
| D10 | the freshness gate watched a field that advances whether or not anything was measured | `cli.py` |
| D11 | the satellite figure undated and present-tense on `gauges.html` | `gauges.html` |
| D16 | the export gate ran eleven tests, all on the gauge store, none on the satellite product | `validate.py` |
| D17 | the only test asking whether a product tracks the river covered the superseded one, and was skipping | `test_flooded.py`, `test_floodwater.py` |
| D18 | README, map page and method page all listed the gorge as withheld; it publishes | `README.md`, `map.html`, `method.html` |
| D19 | the superseded VV pipeline still ran daily and served a contradicting figure | `daily.yml`, `export.py` |
| D20 | the "twelve-day" composite was eleven days, dropping one relative orbit from every cycle | `floodwater.py:128` |
| — | `river_daily.csv` named its column `level_m` and carried m³/s | `export.py` *(found by a stage-8 test)* |
| — | `export_flooded` wrote to the published directory before consulting the gate | `export.py` *(found by a stage-8 test)* |

### High — open, and deliberately so

| id | finding | why not fixed |
|---|---|---|
| D5, D6 | the validation is in-sample and dominated by the permanent channel | fixing it means running a held-out evaluation, not changing code. Stated on three pages, in `docs/METHOD.md` and in `data/tuning_manifest.json` |
| D4 | `flood_km2` has never been scored | same |
| D12 | −25 dB is 2–3 dB below the published optimum and near the IW noise floor | moving it changes every published figure on a literature value rather than a measurement of this river. A −30 dB noise floor *was* added, which changes no pixel's class |
| D13 | VH is the weaker channel for flooded vegetation; the regional literature recommends L-band/HH/steep angles | a method change, behind the same validation |
| D14 | the optical reference is blind under canopy, exactly where the radar is | no reference exists that is not; stated rather than fixed |
| D15 | orbits, look directions and platforms mosaicked and thresholded together | metadata is now recorded per cycle, which makes the question answerable; filtering would halve the data |
| D26 | no settlement mask, and a city reading as a lake is measured | would move published numbers |
| D28 | HAND from a 92.77 m grid with a global drainage threshold, cut hard at 10 m | `tune_mask.py` now sweeps it, including no limit |

### Medium and Low

All 36 remaining entries in `05_defect_register.md` were addressed except
D22 (the per-reach observed fraction on the page) and D35 (area bias and
intervals), both of which need data that does not exist yet. The
documentation contradictions — D25, D32, D33, D37, D38, D39, D40, D41,
D42, D43, D44 — are fixed and now guarded by
`tests/test_gates_and_consistency.py`, which reads the constant, the
exported metadata and the prose side by side.

---

## C. Changes made

| area | what changed |
|---|---|
| `src/parana_crecida/floodwater.py` | `area_km2` returns `float \| None`; window made inclusive; `observed_km2`, `acquisitions`, `union_geometry` added; `MIN_COVERAGE`, `VH_FLOOR_DB`; per-cycle `observation` and `gaps`; union-measured `river[]`; schema 3 |
| `src/parana_crecida/export.py` | `_river_series`, `_implausible`, `_anchor_cycles`; no summed total; suspect readings dropped; `export_flooded` withheld and gated; live stage correlation; `value`/`unit` CSV columns |
| `src/parana_crecida/validate.py` | `water_checks()`; `ValidationResult.suspect`; unit read from a column |
| `src/parana_crecida/cli.py` | freshness gates the newest cycle and three renders; `catalogue` compares units with INA; `animation` removed |
| `src/parana_crecida/gauges.py`, `store.py`, `thresholds.py` | `Gauge.unit` and `Gauge.river`; the unit persisted; thresholds require the river to agree |
| `src/parana_crecida/track.py` | schema 2: no arrival dates |
| `site/*.html` | every measurement labelled with window, age, unit, reach and status; gaps break the chart; withheld ≠ broken; disclaimers raised; titles follow the language; the algorithm described correctly |
| `docs/METHOD.md` | new: the production algorithm step by step, and the calibration/validation/exploration table the audit found missing |
| `data/tuning_manifest.json` | new: what the parameters were fitted on, and that the score is in-sample |
| `tests/` | +97 tests; `conftest.py` separates credential-dependent checks; four new files |
| `.github/workflows/daily.yml` | the superseded pipeline no longer runs |
| deleted | `src/parana_crecida/animation.py` |

---

## D. Scientific validation

### Newly computed in this audit

Two things were measured that had not been, both from data already in the
repository and neither needing a credential.

**1. The corridor overlap, exactly.** Reconstructing the nine corridor
polygons from `data/corridors.json` in an Albers equal-area projection
(standard parallels 26°S/33°S, buffer 20,000 m) reproduces every committed
`corridor_km2` to within 0.1–0.5%, which establishes the reconstruction is
faithful. It then gives:

- seven pairwise overlaps, 1,328–1,651 km² each
- sum of the eight publishing corridors: **65,827 km²**
- union: **55,938 km²**
- double-counted: **9,889 km², 17.7%**

The *water* double-counted is estimated at ≈700 km² (~7% of the headline)
by applying each reach's own channel-water-per-river-km to its overlapped
length. That is an estimate — it assumes water is spread evenly along a
reach, which is wrong where the Yacyretá reservoir sits — and
`tests/test_external.py::test_the_union_is_smaller_than_the_sum` measures
it properly with one query.

**2. The live mask's relation to the gauge record.** Every stage
correlation this project had ever published belonged to the superseded VV
product, because `_stage_correlation` was only called from
`export_flooded`. Computed now for the live VH mask, over the published
cycles, against the gauge at the downstream end of each reach:

| stretch | IoU (in-sample) | r with downstream gauge |
|---|---|---|
| puerto_iguazu_libertador | 0.83 | +0.30 |
| libertador_posadas | 0.90 | +0.12 |
| posadas_ita_ibate | 0.87 | **+0.56** |
| ita_ibate_corrientes | 0.64 | +0.66 |
| corrientes_goya | 0.48 | +0.66 |
| goya_la_paz | 0.53 | +0.78 |
| la_paz_parana | 0.67 | +0.51 |
| parana_villa_constitucion | 0.61 | +0.66 |

Every published reach is positive. The superseded VV mask had
`posadas_ita_ibate` at **−0.28** — the Yacyretá reach, where it read
353 km² of standing reservoir as flood water — and the VH mask's
permanent-water definition fixes exactly that.

**This is context, not validation, and is published as such.** Extent at a
given stage depends on topography, channel–floodplain connectivity,
vegetation and whether the wave is rising or falling, so a weak
correlation is an expected result on some reaches. What it establishes is
the negative: no published reach is measuring something that moves against
the river.

### Previously documented, not re-measured here

The IoU table in `data/mask_skill.json` — river-wide 0.69 (recall 0.80,
precision 0.82), per reach 0.48–0.90, against Sentinel-2 MNDWI on
2026-10-22. Reported as the repository states it. Its limits, now
published:

- **in-sample**: `tune_mask.py` and `validate_mask.py` both default to
  `--day 2026-10-22`, and the sweep covered three of the nine reaches —
  the three scoring 0.48, 0.53 and 0.61;
- **channel-dominated**: the permanent channel is 56–93% of the positive
  class (median 84%, ~75% at the cycle nearest the validation date);
- **partial**: the comparison covers only the optical footprint, 16% of
  `corrientes_goya` and 41% of `parana_villa_constitucion`;
- **not a same-day comparison**: the reference is a median of up to ten
  scenes within ±6 days;
- **blind where the radar is**: optical cannot see under canopy either, so
  the dominant error is outside the measurement by construction;
- **`flood_km2` unscored** entirely.

### The validation workflow of stage 3 was **not run**

It needs an Earth Engine credential, which this session did not have. No
held-out metric was computed, and none is claimed. `data/tuning_manifest.
json`'s `held_out` lists are empty and the leakage tests that guard them
pass trivially today.

### Remaining uncertainty

Unquantified: how much water sits under vegetation; the agricultural
false-positive rate; seasonal drift over the floodplain; how much of the
published variance is acquisition geometry; radar–optical co-registration
error; and whether HAND ≤ 10 m clips real water on the floodplain. None
has an error bar, and no published area has an interval.

---

## E. Tests

```
$ python -m pytest -q
706 passed, 8 skipped in 18.61s

$ python -m pytest --collect-only -q
714 tests collected

$ python -m pytest -q -rs -m needs_ee --run-external
SKIPPED [4] tests/test_external.py: --run-external given but no EE credential in the environment
4 skipped, 710 deselected

$ crecida validate
0 failed, 2 warnings, 17 checks
  warn no_isolated_outliers: 1 series carry isolated out-of-range readings
  warn river_measured_over_the_union: no union measurement; no whole-river total will publish

$ crecida freshness
river data: 2026-11-04 (1 days old)       rainfall:   2026-11-04 (1 days old)
river line: 2026-11-04 (1 days old)       water run:  2026-11-04 (1 days old)
map frames: 2026-10-30 (6 days old)       film:       2026-10-30 (6 days old)
crest:      2026-11-04 (1 days old)       water pass: None (0 cycles)
  STALE: no whole-river total is published; the union measurement is missing
Error: no whole-river total is published
```

The freshness check **fails**, correctly and by design: the whole-river
figure is withheld, and an absent figure still has to reach somebody.

Browser verification (fresh session, live site first, then three local
data states — currently published, post-stage-6 export, and a
union-measured series with injected gaps):

| check | result |
|---|---|
| console errors, 7 pages × 2 languages | none |
| local links and assets | all 200 |
| `document.title` vs `<html lang>` after a switch | match on all 7 |
| untranslated slots | none in either language |
| chart gap rendering | 3 subpaths, 2 markers, description names the count |
| map layer switch | slider relabels to the composite window |
| mobile 375×812 | no horizontal overflow |
| withheld state | notice shown, chart explains, per-stretch pages unaffected |
| `gauges.html` with no total | the satellite evidence line is absent rather than undated |

**Skipped or blocked:** 8 skips, all accounted for in `08_tests.md` §2 —
four need Earth Engine, two cannot exercise their subject until the union
measurement exists, one guards a deliberately withdrawn record, one is a
page with no error box by design.

---

## F. Remaining blockers before operational use

1. **An Earth Engine credential.** Without it the whole-river figure
   cannot be measured at all, and the four `needs_ee` checks cannot run.
   One exists in CI as `EE_SERVICE_ACCOUNT_KEY`.
2. **A rebuild of the water series.** The window fix and the union
   measurement both change what every cycle means, so the year must be
   re-measured rather than extended. Until then the headline is withheld
   and CI goes red.
3. **Reference imagery for a held-out evaluation.** Available in Earth
   Engine (JRC monthly history 1984–2021, Landsat 8/9, Sentinel-2) but not
   yet used. Nothing about the current score is out-of-sample.
4. **A reference that sees under canopy.** SWOT needs an Earthdata login;
   ALOS-2 is not in the public Earth Engine catalog; SLC coherence needs
   SLC, which Earth Engine does not ingest. Until one is obtained, recall
   in the vegetated stratum is **unmeasured**, not 0.80.
5. **Manual review of two items the audit could not settle**: whether
   INA's San Fernando is the same installation as the roster's, and
   whether the three identical dam thresholds are genuine. Both need a
   human and a PDF.
6. **Field validation.** None has been done, and none is claimed.

**Not a blocker but worth stating:** this product must not be used as a
flood-warning or emergency-alert system, which every page now says, with
INA named and linked.

---

## G. The five next steps, by impact

1. **Run `crecida floodwater` with a credential and rebuild the series.**
   One command. It restores the headline as a correct union measurement,
   turns CI green, fills in coverage and acquisition provenance for every
   cycle, and switches on the two skipped output tests. Nothing else on
   this list matters as much, because until it runs the product's main
   figure does not exist.
2. **Enumerate how many reach-dates clear a 60% optical-visibility bar.**
   Cheap, one credential, no new data — and it decides everything
   downstream. If the answer is that two reaches have three usable dates,
   this product cannot be validated to the standard its front page
   implies, and that is worth knowing before building the evaluation.
3. **Score inside and outside the permanent channel separately, and run
   the permanent-water-alone baseline.** Same date, same reference, no new
   imagery. It splits the published 0.69 into "found the river" and "found
   the flood" — and if the mask does not beat "the river is where it
   always is" by a wide margin beyond the channel, the product does not
   yet measure flooding. `tests/test_external.py` has the query.
4. **Build the held-out set from JRC monthly history, 1984–2021.** The
   only design available with zero temporal leakage and full spatial
   coverage, including the Delta, which has never been scored at all.
   Adding its dates to `data/tuning_manifest.json` turns the leakage
   guards on.
5. **Add VV alongside VH, and a settlement mask.** Both are method
   changes that move every published figure, so both belong behind (3)
   and (4). The evidence for each is already measured: VH carries less
   double-bounce than VV exactly where this product is weakest, and a city
   of half a million reading as a lake is recorded in `exposure.py`.
