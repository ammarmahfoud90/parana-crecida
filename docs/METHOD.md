# The production method

What runs, in order, with the file and the constant for every step. This is
the authoritative description: where the README, the method page or a
docstring disagrees with this file, this file is right and the other is a
defect.

Written because the project had two complete radar pipelines, one of them
superseded and both running daily, and no single place said which produced
the published figures. The audit in `AUDIT/` has the findings; this is the
statement of record that follows from them.

Last reviewed 2026-11-05.

---

## 1. The water surface

Entry point `crecida floodwater` → `floodwater.rebuild()`. Run from the
daily job's Earth Engine step, which is skipped when no credential is set.

| step | what happens | where |
|---|---|---|
| 1 | Nine frozen centrelines, walked from the HydroRIVERS topology and reviewed by hand | `data/corridors.json`, `flooded.load_corridors()` |
| 2 | Each buffered by **20 km** into a corridor | `flooded.BUFFER_M`, `flooded.corridor_geometry()` |
| 3 | Which 12-day windows are missing, anchored to the last one attempted | `floodwater._grid()`, `floodwater.WINDOW_DAYS = 12` |
| 4 | `COPERNICUS/S1_GRD`, IW mode, dual-pol scenes carrying VH, `[start, end]` **inclusive** | `floodwater._vh()` |
| 5 | `.mosaic()` over the window, `focal_median` at **50 m**, clipped to the corridor | `flooded.SPECKLE_RADIUS_M` |
| 6 | Water where VH < **−25 dB** *and* VH > **−30 dB** *and* MERIT HAND ≤ **10 m** | `floodwater.VH_THRESHOLD_DB`, `VH_FLOOR_DB`, `HAND_LIMIT_M`, band `hnd` of `MERIT/Hydro/v1_0_1` |
| 7 | Permanent channel: JRC GSW `occurrence > 90` **or** `YearlyHistory` `waterClass == 3` in **2021** | `floodwater.permanent_water()` |
| 8 | Observed area, from the mosaic's own mask, at 200 m | `floodwater.observed_km2()` |
| 9 | Acquisition dates, scene count, relative orbits, pass directions, platforms | `floodwater.acquisitions()` |
| 10 | A cycle below **95%** observed is recorded as a gap, not an area | `floodwater.MIN_COVERAGE` |
| 11 | Areas by `pixelArea()` sum at **60 m**; a null reduction is `None`, never 0 | `flooded.AREA_SCALE_M`, `floodwater.area_km2()` |
| 12 | The **river** measured once over the dissolved union of the publishing corridors | `floodwater.union_geometry()` |
| 13 | 31 cycles kept (≈ a year) | `floodwater.KEEP_CYCLES` |
| 14 | Written to `data/floodwater.json`, schema 3, committed | — |

Then `crecida export` → `export.export_floodwater()`:

| step | what happens | where |
|---|---|---|
| 15 | A stretch publishes only if scored against optical water at IoU ≥ **0.45** | `export.SKILL_FLOOR`, `data/mask_skill.json` |
| 16 | Anything else goes to `withheld` with its reason | — |
| 17 | A cycle whose channel detection is below **90%** of the series median is rejected | `export.MIN_CHANNEL_FRACTION`, `export._implausible()` |
| 18 | `latest`, `lowest`, `highest`: the median of the **3** extreme cycles, not one | `export.ANCHOR_CYCLES` |
| 19 | No union measurement, or one for a different reach → **no river total**, with the reason | `export._river_series()` |
| 20 | Written to `site/data/floodwater.json` | — |

The renders — `crecida mapframes` and `crecida film` — use the same
`floodwater.water_mask()` and the same publishing set
(`riverfilm.published_keys()`), so the picture and the figure come from one
mask.

### What the figures are, and are not

`water_km2` is **open water detected by radar** within 20 km of the
centreline. `flood_km2` is **open water beyond the permanent channel**.
Neither is flood extent:

- water under vegetation is invisible at C band and no threshold recovers
  it, so every figure is a floor;
- anything else dark at C-band VH is included — smooth wet soil, tarmac,
  and at least one city on at least one pass (`exposure.py`);
- a cycle is a mosaic of several dates and viewing geometries, not an
  extent at an instant;
- cycle dates are **window ends on a grid**, not acquisition dates. The
  grid's phase comes from the day the series was first built. The
  acquisitions inside each window are recorded per cycle.

---

## 2. The gauge record

INA a5 API → `packages/ina-a5` → JSON cache → `data/crecida.duckdb` →
`validate.validate()` → `export_*`. `crecida daily` chains fetch, load,
test and export; a failed error-level test raises `ExportBlocked` and
nothing is written.

Stage or discharge is `Gauge.unit`, from the roster's `flow` flag, stored on
the gauge table and checked against INA's own `family` field by
`crecida catalogue`. Official alert and evacuation levels are parsed from
INA's daily report PDF into `data/ina_thresholds.csv`, matched on
normalised station name **and** river, and refused on a unit mismatch.

---

## 3. Calibration, validation, exploration — and what is rejected

The distinction that was missing. "Calibration" fits parameters,
"validation" scores them against something independent, "exploration" tried
something that may or may not have been adopted.

| artefact | role | fitted or scored against | status |
|---|---|---|---|
| `floodwater.water_mask`, VH < −25 dB ∧ > −30 dB ∧ HAND ≤ 10 m | **production mask** | tuned against Sentinel-2 MNDWI water, 2026-10-22 | **live**, and the tuning is in-sample — see below |
| `scripts/validate_mask.py` → `data/mask_skill.json` | **validation**, and the publishing gate | Sentinel-2 MNDWI water, 2026-10-22, one date | live; hand-run, not in CI |
| `floodwater.permanent_water()` | **reference layer** | JRC GSW occurrence ∪ 2021 water class | live |
| `floodwater.MIN_COVERAGE` | **quality filter** | observed share of the corridor | live |
| `export.MIN_CHANNEL_FRACTION` | **quality filter** | the series' own median channel detection | live |
| `flooded.water_mask`, per-stretch VV thresholds | calibration | JRC permanent water over the 2021 bajante | **superseded.** IoU 0.59 against 0.69. Not run, not published; kept because `validate_mask.py` scores the live mask against it |
| `flooded.SLOPE_LIMIT_DEG` slope mask | calibration aid, gorge only | terrain shadow | **superseded** by HAND |
| `flooded.MIN_COVERAGE`, `MIN_PLAUSIBLE_FRACTION` | quality filters | corridor coverage; JRC permanent water | **superseded.** The second never ran: it was only consumed by `area_series(floor_km2=)` while `build()` used `composite_series`. The reasoning survives in `export.MIN_CHANNEL_FRACTION` |
| `scripts/tune_mask.py` | **exploratory sweep** | Sentinel-2 MNDWI water, 2026-10-22, three reaches | live tool. Broken from `73a539c` to 2026-11-05; now sweeps VH including the production value, and HAND, which nothing ever had |
| VH change detection against a 2021 dry reference | exploration | correlation with stage; then IoU | **rejected.** Flood-only areas exceeded the old method's total water, which is impossible. The IoU range quoted for it (0.38–0.45) was unreproducible for a week and is again reproducible |
| `scripts/radar_trial.py` | exploration | stage correlation, scatter at a given stage | kept as the record of the trial |
| Arrival-date projections | exploration | observed crests since 1998 | **rejected.** 23% inside window, 9-day median error. Removed from the page, and from `track.json` in schema 2 |
| `areastage.py`, `scripts/area_stage_history.py` | analysis | 398 composites, 2016–2026 | **withdrawn.** Measured on the superseded mask; needs re-measuring |
| `exposure.py` | analysis | GHSL population grid | **withheld.** A city read as a lake on one pass; the series does not follow the flood. Needs a settlement mask |
| `src/parana_crecida/animation.py` | render | — | **deleted** 2026-11-05. Used the −16 dB VV default and JRC occurrence alone, so it would have drawn Yacyretá as flood; output path did not exist, no page referenced it |

### The validation is in-sample, and that is the largest open weakness

`scripts/tune_mask.py` and `scripts/validate_mask.py` both default to
`--day 2026-10-22`, and `data/mask_skill.json` records that date. So the
published IoU of 0.69 is a goodness-of-fit on one date, not an out-of-sample
score — and the **per-stretch** IoU from that same fit is what decides which
reaches publish an area at all.

Three further limits on it, all of which should be read with the number:

- the mask is scored on **total** water, of which the permanent channel is
  56–93% of the positive class (median 84%), so most of the figure measures
  whether the mask can find a river whose location JRC already supplies;
- `flood_km2` — the figure the product is named for — has **never been
  scored at all**;
- the comparison covers only the footprint a cloud-free optical scene could
  see, which ranges from 16% to 98% of a corridor.

`AUDIT/03_validation_and_area.md` specifies the held-out design that would
replace it. Nothing in this repository has run it, and no parameter should
be changed on the strength of the in-sample score.

---

## 4. Where the numbers come from

| published figure | source |
|---|---|
| whole-river water and flood area | `floodwater.json` → `river[]`, measured over the dissolved union |
| per-stretch area series | `floodwater.json` → `stretches[].water_km2` / `.flood_km2`, each over its own corridor |
| per-stretch IoU, recall, precision | `data/mask_skill.json`, one date, in-sample |
| map overlays and the film | `mapframes`/`riverfilm`, same mask, same publishing set |
| daily stage, percentile, return period | `data/crecida.duckdb`, `data/climatology_parana.json`, `data/return_periods.json` |
| official alert and evacuation levels | `data/ina_thresholds.csv`, from INA's daily report PDF |
| travel times | `data/travel_times.json` — 11 of 12 legs resolution-limited on daily data |
| crest position | `track.json`. **No arrival dates**: measured at a 23% hit rate and withdrawn |

**Corridor areas must not be added together.** The corridors are 20 km
buffers around centrelines that meet at a gauge, so each adjacent pair
overlaps in a 20 km-radius region centred on the river — 1,328 to 1,651 km²
per junction, 9,889 km² over the eight that publish, 17.7% of their summed
area, every km² of it channel. `union_corridor_km2` is the area of the
dissolved union, 55,938 km² against the 65,827 km² the sum gives.

---

## 5. Known limits, in one place

Carried here so a reader does not have to assemble them from five files.

- **Water under vegetation is not seen.** C-band VH is the weaker channel
  for it: the double-bounce return that reveals flooded vegetation is
  stronger in co-polarisation, and the one dedicated SAR study of the
  Paraná delta wetlands recommends L-band, HH and steeper incidence angles.
  Every figure is a floor, and how far below the truth is **unmeasured** —
  the optical reference is blind in the same place.
- **The threshold is absolute and the geometry is not controlled.** No
  orbit, pass-direction or platform filter is applied, and the `angle` band
  is recorded but unused. Backscatter over water falls with incidence angle
  while over land it is flat, and un-normalised aggregation is worth up to
  5 dB in the literature — roughly twice this product's land-water margin.
  The constellation also changed inside the published record.
- **−25 dB is close to the instrument noise floor** over part of the swath,
  and 2 to 3 dB below the published optimum for VH water.
- **HAND comes from a 92.77 m grid** with a globally uniform 0.5 km²
  drainage threshold its own authors recommend recalculating per region, cut
  hard at 10 m against a DEM with 2 m-or-better accuracy over 58% of land.
- **The speckle filter degrades resolution to ≈110 m**, coarser than the
  60 m grid the areas are computed on.
- **Urban and smooth dry surfaces read as water.** Measured, not
  hypothetical: a city of half a million appeared as a lake on one pass.
  There is no settlement mask.
- **A cycle is a mosaic**, so one published figure can mix river stages.
- **Stage and area are not two measurements of one thing.** Extent at a
  given stage depends on topography, channel-floodplain connectivity,
  vegetation, and whether the wave is rising or falling. A weak or
  reach-dependent correlation is the expected result. There is currently
  **no published stage-area relation for the live mask**; every one that
  exists was measured on the superseded one.
