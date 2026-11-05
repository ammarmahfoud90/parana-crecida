# Stage 6 — Batch (a): pipeline, data and documentation changes

Dated 2026-11-05. Implements batch (a) of
[`AUDIT/05_defect_register.md`](05_defect_register.md). **Stopped early: the
session's usage limit was reached.** What is done is done and committed;
§3 lists exactly what is not, so the next session can pick it up without
re-deriving anything.

All 613 tests pass (3 skipped, the same three as before this work). No new
dependency was added. No parameter was changed to improve a score.

---

## 1. Commits, in order

| commit | defects | what changed |
|---|---|---|
| `8fbfb72` | D3, D21 | `area_km2` returns `float \| None`; a null reduction is no longer 0.0 km² |
| `a498b73` | D20, S12 | `filterDate` end-exclusive: the "12-day" window was 11 days |
| `71132ac` | D9, D21, D22, D15 part | coverage, scene count, acquisition dates, orbits and platforms recorded per cycle; `MIN_COVERAGE` restored |
| `5d0485f` | D1, D2 | river measured over the dissolved corridor union; no summed total; robust anchors |
| `dc9e297` | D7 | a reading the validation calls impossible is excluded from exports |
| `99eb9d3` | D16, D1 | satellite data tests; a cycle that cannot find the channel is rejected |
| `1130b15` | D8, D44 part | withdrawn arrival dates removed from `track.json` |
| `7209fa8` | D23 | the unit held in one place and checked against INA's `family` |
| `3ba2f59` | D24 | thresholds require the river to agree, not just the station name |
| `a69a779` | D10, D31 | freshness gates the newest measurement, and three ungated payloads |
| `a435f68` | D19 | the superseded VV pipeline stops running and publishing |
| `5612886` | D12 | a −30 dB noise floor under the water class; threshold unchanged |
| `5fb992e` | D27, D39 | `tune_mask.py` repaired and extended; `animation.py` deleted |
| `2c57d7d` | D25, D26, D41, D42 | source comments and payload metadata made to match the code |
| `3de8be9` | — | `docs/METHOD.md`: the production algorithm and the experiment register |
| `d80099c` | D18, D33, D37, D38, D39, D40 | README corrected where it contradicted the code |

## 2. Files modified

**New.** `docs/METHOD.md` — the statement of record: every production step
with its file and constant, then the table separating calibration,
validation and exploration and marking what is superseded, rejected,
withdrawn or deleted. Where the README, the method page or a docstring
disagrees with it, that file is right.

**Deleted.** `src/parana_crecida/animation.py` and its CLI command — the
−16 dB VV default with JRC occurrence alone as the permanent channel, so it
would have drawn Yacyretá as flood; output path absent, referenced by no
page, in no workflow.

| file | what changed |
|---|---|
| `src/parana_crecida/floodwater.py` | `area_km2` → `float \| None`, band read by name; `_vh` window inclusive; `observed_km2`, `acquisitions`, `union_geometry` added; `MIN_COVERAGE`, `VH_FLOOR_DB`; per-cycle `observation` and `gaps` blocks; union-measured `river[]`; schema 3 |
| `src/parana_crecida/export.py` | `_river_series`, `_implausible`, `_anchor_cycles`, `MIN_CHANNEL_FRACTION`, `ANCHOR_CYCLES`; no summed total; `corridor_km2` from the union; suspect readings dropped from the CSV; `export_flooded` withheld by default and stamps the stale published file; two docstrings corrected |
| `src/parana_crecida/validate.py` | `water_checks()` and the `water_path` argument; `ValidationResult.suspect`; `_unit_expr` reads the column; `OUT_OF_RANGE_SHARE` → `OUT_OF_RANGE_PERCENT` |
| `src/parana_crecida/cli.py` | freshness gates the newest cycle, the absent total and three renders, with `--max-water-age-days`; `catalogue` compares the roster's unit with INA's; `animation` removed; climatology reads `gauge.unit` |
| `src/parana_crecida/gauges.py` | `Gauge.unit` and `Gauge.river`; `UNIT_LEVEL`/`UNIT_FLOW`, `PARANA`/`PARAGUAY`/`IGUAZU`/`BERMEJO`; the four off-Paraná gauges stated |
| `src/parana_crecida/thresholds.py` | `rivers_agree()`; the matcher refuses a river mismatch; reads `gauge.unit` |
| `src/parana_crecida/store.py` | `gauge.unit` column, filled from the roster |
| `src/parana_crecida/track.py` | schema 2: `expected`, `earliest`, `latest`, `days_ahead`, `timing` removed; `withdrawn` added |
| `src/parana_crecida/riverstage.py` | `build(suspect=…)` excludes flagged readings |
| `src/parana_crecida/mapframes.py` | `None` is not an area; stale `calibrated`/`threshold_db` dropped; no-op filter removed; `total_overlaps` flag |
| `src/parana_crecida/riverfilm.py` | a frame's total blanks rather than reporting a short river |
| `src/parana_crecida/exposure.py` | docstring corrected: its table is not attributable to the VH mask |
| `scripts/tune_mask.py` | local `dry_reference` restored; VH sweep now contains the production value and −22.9; HAND swept, including no limit |
| `scripts/validate_mask.py` | note that both masks now see the same window |
| `.github/workflows/daily.yml` | `crecida flooded` no longer run; `data/flooded.json` no longer staged |
| `site/index.html` | handles the new `river[]` shape; says why a total is absent (ES + EN) |
| `site/wave.html` | four dead arrival-date strings removed |
| `README.md` | six corrections — see `d80099c` |
| `tests/` | 5 files; the three tests that encoded the summing defect rewritten to assert the contract; ~20 tests added |

## 3. What remains

### 3.1 Needs an Earth Engine credential, which this session did not have

**The committed data is not yet consistent with the code.** This is the
most important item here.

- `data/floodwater.json` is schema 2. It has no union measurement, no
  coverage, no scene counts, and its cycles were measured on 11-day
  windows. So `export_floodwater` now publishes the per-stretch series and
  **no whole-river total**, with the reason in `river_unavailable`, and
  `crecida freshness` reports that as a problem. That is the intended
  behaviour — a figure known to be 17.7% too high is worse than an absent
  one — but it means the headline is down until one run of
  `crecida floodwater` with a credential rebuilds the series.
- That rebuild is not incremental: the window fix and the union
  measurement both change what every cycle means, so the year needs
  re-measuring rather than extending. Suggested: move
  `data/floodwater.json` aside and let `_grid` rebuild all 31 cycles.
- Until then `site/data/floodwater.json` keeps its last good values and
  `index.html` shows the notice. Nothing publishes a number known to be
  wrong.

### 3.2 Deliberately not done, and why

- **D13, VV alongside VH.** Would change every published figure. The case
  for it is strong (double-bounce is weaker in cross-pol; the regional
  literature recommends L-band/HH) but it is a method change and belongs
  behind the held-out validation of `AUDIT/03`, not behind a literature
  citation.
- **D12, moving the threshold.** `−25 dB` is 2–3 dB below the published
  optimum and close to the IW noise floor. Not moved, for the same reason:
  the only scoring available is in-sample on one date. The reasoning is now
  in the code so the next person does not have to rediscover it. The
  −30 dB floor *was* added, because it rejects noise rather than retuning
  anything — no pixel between −30 and −25 changes class.
- **D15, filtering by orbit or platform.** The metadata is now recorded per
  cycle, which is what makes the question answerable; filtering would halve
  the data and change every figure.
- **D26, a settlement mask.** Same reasoning. The Posadas failure is
  measured, so this is the best-evidenced of the deferred changes, but it
  would move published numbers.
- **D28, recalculating HAND** from `upa` with a basin-specific drainage
  threshold. `tune_mask.py` now sweeps HAND including no limit, which is
  the measurement that should decide it.
- **D30, a defined composite statistic** instead of `.mosaic()`.

### 3.3 Batch (b) and (c), not attempted

All of batch (b) beyond the two minimal correctness changes `index.html`
needed to handle the new data shape. In particular **D11** — the satellite
figure rendered undated and in the present tense on `gauges.html`, with the
ratio from the rejected cycle — is untouched, and it is the most
reader-facing defect left. `index.html:160-161` already has the right
wording to copy.

Also not done from batch (b): the per-reach `seen` fraction on the page
(D22), the reference's blind spot stated on the page (D14), scoping "three
times a day" to the gauge data (D32, `method.html:220,329`), the method
page's own copy of the Delta correlation and the 0.7–0.9 band (D33, D37 —
fixed in the README, not in `method.html`), renaming the published
quantities away from "flooded area" (D4/B8), and the cosmetics (D43 "the
missing quarter" against a measured recall of 0.80, D44's `limitGorge`
key).

All of batch (c) beyond what landed with the code: the tuning manifest
(c4), the held-out evaluation sets (c5), scoring inside and outside the
channel separately with the permanent-water baseline (c6), archiving the
reference masks (c7), the SCL masking fix (c8), and porting the
tracks-the-river test to the live product and making its skip a failure
(c2, D17) — still the project's strongest scientific guard, still covering
only the superseded product, still skipping.

### 3.4 Unverified by this stage

Everything in §Unconfirmed of the register (U1–U12). The one worth doing
first is still U1: count IW VH scenes over `posadas_ita_ibate` and
`ita_ibate_corrientes` for 19–30 June 2026 and reduce the mask over each
corridor. It is one query, and it would say whether the 2026-06-30 failure
was missing acquisitions or the mask. The guards added here reject that
cycle either way, which is correct but is not the same as knowing why.
