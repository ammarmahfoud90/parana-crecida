# Stage 1 — Repository inspection and the true production pipeline

Read-only audit. No code was changed. Dated 2026-11-05.

**Scope of what was inspected.** Working tree at `a9cf6bf`, plus `origin/main`
at `84440a8`. The eight commits between them are all `github-actions[bot]`
"datos" commits touching only `site/data/`, `data/`, `reports/` and
`site/media/`; no source file differs between the local checkout and the
published branch, so the code audited below is the code that is running.
Where a published *number* is quoted, it was read from `origin/main` (the
branch GitHub Pages deploys) and is noted as such.

**What this stage did not do.** Nothing here was re-measured. No Earth Engine
credential was used, so every backscatter value, HAND value, optical-water
mask, area and IoU in this repository is reported as the repository states it,
not as verified. Section 5 lists what that leaves unverified.

---

## 1. The production algorithm, step by step

There are **two complete radar pipelines in the repository and both run every
day in CI**. Only one reaches the pages. The live one is `floodwater.py`.

### 1.1 The live water-surface pipeline

Entry point: `crecida floodwater` (`src/parana_crecida/cli.py:1000`), invoked
from `.github/workflows/daily.yml:96`, inside the `continue-on-error: true`
Earth Engine step.

1. **Corridors.** `flooded.load_corridors()`
   (`src/parana_crecida/flooded.py:120`) reads the nine frozen centrelines in
   `data/corridors.json`. `floodwater.rebuild()` iterates all nine.
   `flooded.corridor_geometry()` (`flooded.py:163`) buffers each centreline by
   `BUFFER_M = 20_000` m (`flooded.py:61`), giving corridors of 6,625–9,621 km²
   (total 65,635 km² over the eight that publish).

2. **Which cycles to measure.** `floodwater._grid()`
   (`src/parana_crecida/floodwater.py:235`) anchors the 12-day grid to the last
   cycle already in `data/floodwater.json` and appends a new cycle only once a
   full `WINDOW_DAYS = 12` (`floodwater.py:110`) has elapsed since it. The
   series is trimmed to `KEEP_CYCLES = 31` (`floodwater.py:232`), i.e. 372 days.

3. **Image selection.** `floodwater._vh()` (`floodwater.py:121`) takes
   `COPERNICUS/S1_GRD`, filters to the corridor bounds, to
   `instrumentMode == "IW"`, to products whose
   `transmitterReceiverPolarisation` contains `VH`, and to
   `filterDate(start, end)` where `start = end - 11 days`
   (`floodwater.py:200`, `floodwater.py:280`). It selects the `VH` band only.
   No orbit-direction filter, no relative-orbit filter, no incidence-angle
   normalisation, no radiometric terrain correction.

4. **Compositing and despeckle.** `floodwater.water_mask()`
   (`floodwater.py:134`) calls `.mosaic()` over the window, then
   `.focal_median(50, "circle", "meters")` (radius from
   `flooded.SPECKLE_RADIUS_M`, `flooded.py:72`), then `.clip(corridor)`.
   `mosaic()` keeps the last image in the collection per pixel; it is not a
   mean or median over passes.

5. **The water test.** `floodwater.water_mask()` returns
   `vh.lt(VH_THRESHOLD_DB).And(hand.lte(HAND_LIMIT_M))` with
   `VH_THRESHOLD_DB = -25.0` dB (`floodwater.py:100`) and
   `HAND_LIMIT_M = 10.0` m (`floodwater.py:106`), HAND being band `hnd` of
   `MERIT/Hydro/v1_0_1` (`floodwater.py:79`). One threshold for all nine
   corridors. No slope mask, no change detection, no per-stretch parameter.

6. **Permanent channel.** `floodwater.permanent_water()`
   (`floodwater.py:148`) is the union of two layers:
   `JRC/GSW1_4/GlobalSurfaceWater` `occurrence > 90`, **or**
   `JRC/GSW1_4/YearlyHistory` `waterClass == 3` for `year == 2021`
   (`BAJANTE_YEAR`, `floodwater.py:94`). `flood_only()` (`floodwater.py:176`)
   is `water_mask().And(permanent_water().Not())`.

7. **Area.** `floodwater.area_km2()` (`floodwater.py:181`) applies
   `.selfMask()`, multiplies by `ee.Image.pixelArea()`, and reduces with
   `Reducer.sum()` at `scale = flooded.AREA_SCALE_M = 60` m
   (`flooded.py:75`), `maxPixels = 1e10`, `bestEffort = True`. Two numbers per
   stretch per cycle: `water_km2` (all water) and `flood_km2` (outside the
   permanent channel). Written to `data/floodwater.json` and committed by CI
   (`daily.yml:117`).

8. **Validation table.** `scripts/validate_mask.py` scores both masks against
   Sentinel-2 and writes `data/mask_skill.json`. It is **not** in the daily
   workflow; it is run by hand. The committed table is dated `2026-10-22` and
   was last written on 2026-10-30 (`git log data/mask_skill.json`). Inside it:
   optical truth is `optical_water()` (`validate_mask.py:55`) — every
   `COPERNICUS/S2_SR_HARMONIZED` scene within ±6 days with
   `CLOUDY_PIXEL_PERCENTAGE < 20`, SCL-masked for cloud/shadow/snow, B3 and
   B11 reduced by `.median()`, MNDWI `> 0` (`MNDWI_WATER`,
   `validate_mask.py:50`). `agreement()` (`validate_mask.py:95`) computes
   recall, precision and IoU by area at 60 m, **restricted to the `seen` mask**
   — the footprint where the optical composite has data.

9. **The publishing gate.** `export.export_floodwater()`
   (`src/parana_crecida/export.py:730`) joins `data/floodwater.json` to
   `data/mask_skill.json` by stretch key. A stretch publishes only if it has a
   `now.iou` and that IoU is `>= export.SKILL_FLOOR = 0.45`
   (`export.py:720`, applied at `export.py:792`). Everything else goes to a
   `withheld` list with its reason. The river-wide total is summed **here, not
   in the page** (`export.py:803-823`), and a cycle is marked
   `complete: true` only where all publishing stretches reported.
   `latest`/`lowest`/`highest` are taken from complete cycles only.
   Output: `site/data/floodwater.json`.

10. **Renders.** `riverfilm.py` (`crecida film`) and `mapframes.py`
    (`crecida mapframes`) both call `floodwater.water_mask()` and
    `floodwater.permanent_water()` (`riverfilm.py:212,217`;
    `mapframes.py:85,91`) and both restrict themselves to
    `riverfilm.published_keys()` (`riverfilm.py:97`), which applies the same
    `SKILL_FLOOR`. So the GIF, the map overlays and the headline are drawn from
    one mask and one publishing rule.

### 1.2 The second, superseded pipeline — still running

`crecida flooded` (`cli.py:1099`) is invoked at `daily.yml:97`, immediately
after `crecida floodwater`. It runs `flooded.build()` (`flooded.py:349`), which
uses `flooded.water_mask()` (`flooded.py:170`): despeckled **VV** below a
**per-stretch** `threshold_db` from `data/corridors.json` (−14.5 to −17.0 dB),
with an optional `SLOPE_LIMIT_DEG = 3.0` slope mask on the two gorge reaches,
and `permanent_water_km2()` using JRC `occurrence > 90` alone. It writes
`data/flooded.json`, and `export.export_flooded()` (`export.py:861`) writes
`site/data/flooded.json`.

**No page fetches `site/data/flooded.json`.** Every `fetch()` of a data file in
`site/*.html` is accounted for, and `flooded.json` is not among them
(`flood.html:448`, `index.html:618` and `method.html:561` all fetch
`floodwater.json`). The file is nonetheless rebuilt daily, committed
(`daily.yml:117`) and served at a public URL, carrying a 2026-10-31 figure of
1,644.7 km² for `corrientes_goya` from the rejected VV mask, while
`floodwater.json` carries 1,691.9 km² for the same stretch at 2026-10-28 from
the live mask. Two published figures for the same quantity, by two different
methods, with nothing on either file saying which is which.

### 1.3 The gauge pipeline (supporting evidence)

INA a5 → `packages/ina-a5` → `data/crecida.duckdb` → `validate.validate()` →
`export_*`. `crecida daily` (`cli.py:1562`) chains fetch → load → export.
`thresholds.py` parses INA's daily report PDF into `data/ina_thresholds.csv`
(56 rows: 16 `official`, 31 `not_in_report`, 9 `unmatched_report_row`) and the
module's stated rules — nothing invented, nothing dropped silently, exact
normalised-name matching only (`thresholds.py:9-20`) — are borne out by the
code. `ina_a5.quality` judges each gauge against its own record rather than
absolute levels. This half of the project is disciplined and I found no
contradictions in it.

---

## 2. Calibration, validation, exploration — and what is rejected

| Artefact | Role | Target it was fitted or scored against | Status |
|---|---|---|---|
| `flooded.water_mask`, VV at per-stretch `threshold_db` (`data/corridors.json`) | **Calibration** | JRC GSW `occurrence > 90` over the 2021 bajante, 1 Aug – 15 Sep | **Superseded** as the published mask. Still built daily, still committed, still served at `site/data/flooded.json`, read by no page. |
| `flooded.SLOPE_LIMIT_DEG = 3.0` slope mask | Calibration aid, gorge reaches only | Terrain shadow on valley walls | **Superseded**; replaced by HAND. Still live in `flooded.py` and in `exposure.py`. |
| `flooded.MIN_COVERAGE = 0.95` | Quality filter | Corridor area observed per pass | **Dropped** from the live pipeline. No coverage test exists anywhere in `floodwater.py`. See finding C7. |
| `flooded.MIN_PLAUSIBLE_FRACTION = 0.7` (`flooded.py:258`) | Quality filter | JRC permanent water per pass | **Dead code even in the superseded pipeline.** It is only consumed via `area_series(floor_km2=...)` (`flooded.py:336`), and `build()` uses `composite_series()` instead (`flooded.py:377`), which never applies it. |
| `scripts/tune_mask.py` | **Exploratory parameter sweep** | Sentinel-2 MNDWI water, 2026-10-22, on 3 stretches (`corrientes_goya`, `parana_villa_constitucion`, `goya_la_paz`) | Partly **broken at HEAD**. See C5. Swept VH cuts are −20, −22, −24 (`tune_mask.py:97`). |
| `scripts/radar_trial.py` | **Exploratory trial** — VH change detection, HAND ≤ 25 m, drop > 3 dB | Correlation with gauge stage, and scatter at a given stage | **Rejected.** README:965-985 records why: flood-only areas exceeded the old method's total water. Script still runs (self-contained). |
| VH change detection as scored by IoU ("0.38 to 0.45", `floodwater.py:60-64`) | Exploratory | Sentinel-2 MNDWI water, 2026-10-22 | **Rejected.** Not reproducible at HEAD — see C5. |
| `floodwater.water_mask`, VH < −25 dB ∧ HAND ≤ 10 m | **Live mask** | Tuned and scored against the same Sentinel-2 composite of 2026-10-22 | **In production.** In-sample — see C1. |
| `scripts/validate_mask.py` → `data/mask_skill.json` | **Validation**, and the publishing gate | Sentinel-2 MNDWI water, 2026-10-22, one date | Live. Hand-run, not in CI. |
| `areastage.py` + `scripts/area_stage_history.py` → `data/area_stage.json` | Analysis (area vs stage, 398 observations) | — | **Withdrawn.** `export_area_stage(publish=False)` by default (`export.py:633`) and `export()` never passes `publish=True`. Correctly withheld, with the reason in the docstring. |
| `exposure.py` → `data/exposure.json` | Analysis (population under water) | GHSL population grid | **Withheld.** `export_exposure(publish=False)` (`export.py:685`). Correctly withheld. But the module is built on the superseded VV mask while claiming otherwise — see C3. |
| `animation.py` → `site/media/flood.gif` | Render | — | **Obsolete and orphaned.** Uses `fl.water_mask(mosaic)` with the module-default −16 dB VV threshold and JRC-occurrence-only permanent water (`animation.py:89,110`). Output path does not exist; no page references it; not in CI. Described as current at README:413-416. |
| `backtest.py` → arrival-date skill | Analysis | Observed crests since 1998 | **Rejected feature, honestly recorded.** 23% hit rate, 9-day median error; the projection was removed and the backtest kept as the reason. |
| `data/track_skill.json`, `data/travel_times.json`, `data/return_periods.json` | Gauge-side analysis | Observed record | Live, published with n and spread. |

**Withheld in the live product:** exactly one stretch,
`villa_constitucion_ibicuy` (the Delta), reason
`"never scored against optical water"`. Verified in `origin/main`'s
`site/data/floodwater.json`.

---

## 3. Imagery → displayed value, per published metric

Values below read from `origin/main` (`fetched_on: 2026-11-04`).

### 3.1 `index.html` headline — "9,728 km² of the Paraná valley under water"

`COPERNICUS/S1_GRD` IW VH, products in `[2026-10-17, 2026-10-28)`
→ `_vh()` filter+select (`floodwater.py:121`)
→ `.mosaic()` → `focal_median(50 m)` → `.clip(corridor)` (`floodwater.py:134`)
→ `.lt(-25)` ∧ `MERIT hnd .lte(10)` (`floodwater.py:146`)
→ `.selfMask() × pixelArea`, `sum` at 60 m (`floodwater.py:181`)
→ per-stretch `water_km2` in `data/floodwater.json` (`floodwater.py:316`)
→ gated on `mask_skill.json` IoU ≥ 0.45 (`export.py:792`)
→ summed across the 8 publishing stretches into `river[].water_km2`, marked
`complete` (`export.py:803-823`)
→ `latest` = last complete cycle (`export.py:838`)
→ `site/data/floodwater.json`
→ `render()` → `document.getElementById("area").textContent =
num(Math.round(latest.water_km2))` (`index.html:536`).

**This is total water, channel included, not flood.** The caption
("km² del valle del Paraná bajo agua" / "of the Paraná valley under water",
`index.html:160`) is literally true and the distinction is made in the line
below it (`floodNote`), but the lede number is not the flood figure.

Composition of the 9,727.5 km²: 7.9% (768.7 km²) from the two reaches scoring
IoU 0.83–0.90, and **32.3% (3,139.9 km²) from the two reaches scoring 0.48 and
0.53**. No uncertainty is published on the total.

### 3.2 `index.html` "+87%" and "5.9 times as much"

`rise` = `(latest.water_km2 / lowest.water_km2 - 1) × 100` (`index.html:552`),
`lowest` = min over complete cycles (`export.py:839`) = 5,203.6 km² at
2026-06-30. `floodNote` = `latest.flood_km2 / low.flood_km2`
(4,301.9 / 733.7 = 5.9, `index.html:543`). Both are ratios of the same
mask against itself; neither carries the mask's error.

The page calls 2026-06-30 "la pasada más seca del registro" / "the driest pass
on the record" (`index.html:161`). The record is `KEEP_CYCLES = 31` cycles =
372 days, spanning 2025-11-02 to 2026-10-28. See C8.

### 3.3 `index.html` skill table and `method.html` validation table

`data/mask_skill.json` → `export_floodwater` copies it verbatim into
`out["skill"]` (`export.py:836`) → `drawSkill()` (`index.html:487`) and the
`#calib` table (`method.html:499`). Columns rendered: stretch, river_km,
`clear_scenes`, `now.recall`, `now.precision`, `now.iou`, `previous.iou`,
publishes yes/no. **`seen_km2` and `truth_km2` are present in
`mask_skill.json` and rendered nowhere.** See C2.

### 3.4 `flood.html` per-stretch series

Same `site/data/floodwater.json`; `stretches[].days` / `water_km2` /
`flood_km2` plus the `iou`/`recall`/`precision` attached at `export.py:796`,
and the `withheld` list with reason (`flood.html:388-395`).

### 3.5 `map.html` overlays and per-frame totals

`mapframes.build()` (`mapframes.py:124`) → same `floodwater.water_mask()` →
painted RGB, land transparent → PNG per cycle in `site/media/map/` →
`site/data/map_frames.json` with `frames[].km2` per stretch and `total_km2`.
Verified: the last frame's `total_km2` is 9,727.5 km², identical to the
headline, and equals the sum of all eight rendered stretches. The
`calibrated` set at `mapframes.py:161` is `{s.key for s in stretches}`, so the
`if k in calibrated` filter at `mapframes.py:214` is a no-op — harmless, but
the name is misleading and the stale fields it implies are published (C6).

### 3.6 `index.html` animation caption

`riverfilm.build()` → `site/media/river.gif` (31 frames) +
`site/data/river_film.json` (`river_km`, frame dates) →
`describeFilm()` (`index.html:361`). Areas are recomputed independently in
`riverfilm.py:320` via the same `floodwater.area_km2`.

### 3.7 `data/csv/flooded_area.csv`

Built from `site/data/floodwater.json`, not from the superseded table
(`export.py:526`), one row per stretch per cycle with `iou`, `recall`,
`precision` attached. Correct and consistent with the pages.

### 3.8 Gauge metrics

`river_today.json` (stage, day-of-year percentile from
`data/climatology_parana.json`, Log-Pearson III return period from
`data/return_periods.json`, official alert/evacuation from
`data/ina_thresholds.csv`), `wave.json`, `river_stage.json`, `track.json`,
`drivers.json`. All trace to INA a5 readings in `data/crecida.duckdb` and are
gated by `validate.validate()`.

---

## 4. Contradictions and defects

Ordered by how much they affect a published number.

### C1 — The live threshold was tuned on the data it is scored against, and the page does not say so

`scripts/tune_mask.py` sweeps candidate masks against Sentinel-2 water on
`--day` default `2026-10-22` (`tune_mask.py:36`). `scripts/validate_mask.py`
scores the chosen mask against Sentinel-2 water on `--day` default
`2026-10-22` (`validate_mask.py:132`). `data/mask_skill.json` records
`"day": "2026-10-22"`. The published IoU of 0.69 is therefore an in-sample
goodness-of-fit on a single date, not an out-of-sample validation — and the
**per-stretch IoU from that same fit is what decides which stretches publish
an area at all** (`export.py:792`).

Nothing published says this. `README.md:13-17`, `site/method.html:368` and
`site/index.html:293` all present 0.69 as what the method "scores" when
"checked against" optical water. `method.html:368` states in one sentence both
that the threshold was "tuned against water rather than against the channel"
and that it "scores an IoU of 0.69", without noting these are the same water
on the same day.

A second-order consequence: the per-stretch IoUs span 0.48–0.90 on a single
date. There is no second date, so there is no estimate of how much of that
spread is reach character and how much is that day's conditions, and no
confidence interval on any of it.

### C2 — The gating IoU for the worst reach was measured over 16% of its corridor, and the coverage is never shown

`agreement()` restricts the comparison to the `seen` mask — where the ±6-day
Sentinel-2 composite has data (`validate_mask.py:99-101`). `mask_skill.json`
records that footprint as `seen_km2`. Against the corridor areas in
`site/data/floodwater.json`:

| stretch | corridor km² | optical `seen_km²` | share scored | IoU | publishes | km² published, latest |
|---|---|---|---|---|---|---|
| puerto_iguazu_libertador | 7,979 | 5,100 | 64% | 0.83 | yes | 232.1 |
| libertador_posadas | 6,625 | 6,278 | 95% | 0.90 | yes | 364.9 |
| posadas_ita_ibate | 7,551 | 7,103 | 94% | 0.87 | yes | 2,360.0 |
| ita_ibate_corrientes | 7,781 | 4,960 | 64% | 0.64 | yes | 1,430.7 |
| **corrientes_goya** | 9,474 | **1,503** | **16%** | 0.48 | yes | 1,691.9 |
| goya_la_paz | 9,501 | 8,938 | 94% | 0.53 | yes | 1,448.0 |
| la_paz_parana | 7,103 | 6,967 | 98% | 0.67 | yes | 838.9 |
| parana_villa_constitucion | 9,621 | 3,979 | 41% | 0.61 | yes | 1,361.0 |

`corrientes_goya` — the reach the project's own documentation calls the
hardest, and the one whose floodplain motivates the whole product
(`flooded.py:3-6`) — publishes a full-corridor figure on the strength of a
score computed over one sixth of that corridor, against 230.7 km² of optical
truth. `parana_villa_constitucion` publishes on 41% coverage from 2 clear
scenes. The optical footprint is cloud-selected, so it is not a random sample
of the corridor.

`seen_km2` is in the committed table and is rendered on no page. The method
page's validation table shows `clear_scenes` (`method.html:506`), which does
not convey this. A reader sees "6 clear scenes, IoU 0.48, publishes: yes".

### C3 — `exposure.py` states its table came from the validated VH mask; its code uses the superseded VV mask

`src/parana_crecida/exposure.py:5-7`:

> "Tried three times, on three different water masks, and failed the same way
> each time. **On the validated VH mask**, over 31 repeat cycles from November
> 2025 to October 2026:"

`src/parana_crecida/exposure.py:92-101`:

```
def flood_only_mask(corridor, start, end, stretch):
    water = fl.water_mask(
        mosaic, threshold_db=stretch.threshold_db, slope_mask=stretch.slope_mask
    )
    permanent = ee.Image(fl.GSW).select("occurrence").gt(PERMANENT_OCCURRENCE).unmask(0)
    return water.And(permanent.Not())
```

That is the per-stretch VV threshold plus the JRC-occurrence-only permanent
water — the definition that `floodwater.permanent_water()` exists to replace,
and that the project records as having read 353 km² of the Yacyretá reservoir
as flood water in every frame (`floodwater.py:150-160`). The quoted figures
(the 48,959 spike, the 2,217 → 5,918 km² flood areas) cannot be reproduced
from this file. The product does not publish, so no reader is misled, but the
recorded reason for not publishing is attributed to a mask the code does not
implement.

### C4 — Two docstrings disagree on whether the live method solves the city-as-lake failure

`src/parana_crecida/export.py:676-678`:

> "The change-detection mask cannot: a street is dark in August and dark in
> June, so it never drops, so it is never flooded. **That is the clearest
> argument for the method there is.**"

`src/parana_crecida/exposure.py:17-21`:

> "On that one pass the urban surface fell below the water threshold and a city
> of some half a million read as a lake. The old VV mask did this too, at
> Posadas, at 97,498 people. **A different band, the same city, the same
> failure.**"

Change detection was rejected (README:965-985; `floodwater.py:58-64`). The
live method is an **absolute** VH threshold, which has no structural defence
against a smooth dry surface falling below −25 dB — and `exposure.py` records
that it did exactly that. `export.py` credits the fix to a method that is not
in use, so the one documented safeguard against urban false positives applies
to nothing that runs. I found no settlement mask, no urban exclusion and no
test for this in the live pipeline.

### C5 — `scripts/tune_mask.py` cannot run at HEAD, so the rejection of change detection is not reproducible

`scripts/tune_mask.py:109`:

```
references = {s.key: fw.dry_reference(c) for s, c, _, _ in prepared}
```

`floodwater.dry_reference` **does not exist**. `git log -S` shows it was added
in `3e88351` and removed in `73a539c`; the call site survived. The line is
unconditional and sits after the VH sweep, so the script raises
`AttributeError` before printing any change-detection row.

Consequence: the IoU range "0.38 to 0.45" that `floodwater.py:58-64` and
`method.html` rely on to reject change detection cannot be regenerated from
this repository. It was reproducible at `3e88351`; it is not now.

Two further gaps in the same script, both reproducibility rather than
correctness:

- **The chosen threshold was never in the committed sweep.** `tune_mask.py:97`
  sweeps `for cut in (-20, -22, -24)`. Production uses −25.0
  (`floodwater.py:100`), set in the same commit (`73a539c`) that wrote the
  sweep. −25 is outside the swept set and outside its range.
- **The HAND claim has no code behind it.** `floodwater.py:102-106` states
  "Ten was the best of 10, 15 and 25". `tune_mask.py` holds HAND fixed at
  `fw.HAND_LIMIT_M` (`tune_mask.py:102,119`) and sweeps only the VH cut and
  the change-detection drop/ceiling. No committed script sweeps HAND.

### C6 — `README.md` still lists the gorge as withheld; it has published since 2026-10-30

`README.md:26-28`:

> "**What is deliberately not here:** arrival dates …, **area for the gorge
> above Posadas** and for the Delta (the measurement does not track the river
> there), and any claim that the numbers are totals."

`puerto_iguazu_libertador` (IoU 0.83) and `libertador_posadas` (IoU 0.90) are
both in the published `stretches` of `origin/main`'s
`site/data/floodwater.json` and contribute 597.0 km² to the headline. Their
reinstatement is the headline change of commit `73a539c`, which the same README
describes at length further down. The summary at the top was not updated.

The parenthetical reason is wrong for the Delta too: the code's reason is
`"never scored against optical water"` (`export.py:789`), not tracking.

Related stale metadata, same cause: `site/data/map_frames.json` publishes, for
every stretch, `calibrated` and `threshold_db` from `data/corridors.json`
(`mapframes.py:223,227`) — so the live map data file states
`"calibrated": false, "threshold_db": -17.0` for `puerto_iguazu_libertador`,
which both publishes and is not measured with a VV threshold at all. No page
reads those two fields today, so nothing is displayed wrongly; the file is
wrong on its face.

`src/parana_crecida/mapframes.py:132-143` carries two stacked comment blocks,
the first describing the superseded "marked uncalibrated" rule and the second
the current skill-floor rule.

### C7 — The live pipeline has no coverage check, and the method page asserts the property the dropped check used to verify

`site/method.html:372` (EN; ES at `:263`):

> "Two filters used to discard passes: one covering less than 95% of the
> corridor, and one finding less than 70% of the permanent channel. Neither is
> needed now. Everything is measured on twelve-day composites, **which cover
> the whole corridor every time**…"

The superseded pipeline measured that: `composite_series()` computes a `seen`
area per window and drops the cycle below `MIN_COVERAGE = 0.95`
(`flooded.py:550-561`), with a comment at `flooded.py:245-250` spelling out the
failure mode — "unobserved ground reduces to zero water, which reads as the
river shrinking."

`floodwater.py` contains no equivalent. There is no `seen` reduction, no
coverage ratio, no threshold, nothing. In `area_km2()` (`floodwater.py:181`)
unobserved pixels are masked and `.selfMask()` + `sum` contributes zero for
them, so an incomplete VH mosaic silently under-reports area with no flag on
the page and no flag in the data. The claim that composites always cover the
corridor is an assertion carried over from VV coverage statistics
(`flooded.py:85-90`); I found no VH measurement of it in the repository.

The same section's second claim — that the 70% plausibility filter is no longer
needed — is about a filter that was already dead code in the superseded
pipeline (see §2), so "it used to discard passes" overstates what it did.

### C8 — Three different values are published for the Delta's stage correlation

| Source | Value |
|---|---|
| `README.md:837` and `README.md:857` | **−0.44** ("the number that decided it") |
| `README.md:715` | **−0.39** |
| `site/method.html:271` (ES) and `:380` (EN) | **−0.39** |
| `tests/test_flooded.py:367` docstring | **−0.39** |
| `site/data/flooded.json`, as computed today | **−0.23** |

`_stage_correlation()` (`export.py:585`) recomputes this value from the store on
every run, so it drifts as the record grows. `tests/test_flooded.py:432` asserts
only `delta < 0`, which does not catch drift. The prose quotes a frozen number
as though it were the current measurement.

### C9 — "The missing quarter" versus a measured recall of 0.80

`site/index.html:292` (EN; ES at `:206`): "That is **the missing quarter** of
the water, and no threshold recovers it."

`src/parana_crecida/floodwater.py:66`: "**Four fifths** of the water is found
and four fifths of what is called water is water."

`mask_skill.json` recall = 0.80, so a fifth is missed, not a quarter. The
sentence also attributes the whole miss to canopy; recall loss inside the
`seen` footprint has not been partitioned between vegetation, wind, incidence
angle and the HAND cut-off anywhere in the repository.

### C10 — The freshness gate measures "we looked today", not "the measurement is recent"

`floodwater.rebuild()` sets `fetched_on = date.today()` on every successful run
whether or not a cycle was added, and the docstring states this is deliberate
(`floodwater.py:291-295`). `crecida freshness` tests `fetched_on` against
`max_rain_age_days = 10` (`cli.py:966-977`) and never looks at
`stretches[].days[-1]`.

Live evidence: `origin/main`'s `site/data/floodwater.json` has
`fetched_on: 2026-11-04` and `latest.day: 2026-10-28` — the newest measurement
is 7 days old and the gate reports everything current. Seven days is normal for
a 12-day cycle; the point is that the gate cannot distinguish 7 days from 70.
Per-stretch Earth Engine failures are caught and logged but not re-raised
(`floodwater.py:306-309`), so a stretch can stop advancing indefinitely while
`fetched_on` keeps moving. The only visible signal is `latest.day` on the page.

### C11 — The 12-day composite is an 11-day composite, and excludes the date it is labelled with

`floodwater._vh()` uses `.filterDate(start.isoformat(), end.isoformat())`
(`floodwater.py:128`). Earth Engine's `filterDate` is start-inclusive,
end-exclusive. `rebuild()` sets `start = end - timedelta(days=window_days - 1)`
(`floodwater.py:280`), so the window is `[end-11, end)` — 11 days, and the
cycle's own labelled end date is not in it.

`flooded.py:202` compensates explicitly for the same semantics
(`(end + timedelta(days=1)).isoformat()`), which shows the convention was
understood in the older module. Everything published as a "twelve-day
composite" (`method.html:373`, `index.html:291`, `floodwater.py` note string,
`map_frames.json` note) is an eleven-day composite in the data.

### C12 — The head-to-head 0.59 versus 0.69 is scored on different numbers of radar days

In `validate_mask.py:168-175`, the old mask is built from
`fl._collection(corridor, start, end)` and the new one from
`fw.water_mask(corridor, start, end)` with `start = day-6`, `end = day+6`.
Via C11, the old mask sees `[day-6, day+7)` = 13 days and the new one
`[day-6, day+6)` = 12 days. The comparison the whole method change rests on is
not on identical input windows.

Separately, the production window is a 12-day block **ending** at the cycle
date, while the validation window is centred on the validation date and
includes 6 days of future imagery. The mask is therefore scored on a composite
the production pipeline never forms.

### C13 — Validation "truth" is itself a 13-day median composite, described as a cloud-free day

`validate_mask.py:8-11`: "Sentinel-2 sees water directly … so **on a
cloud-free day** it is the closest thing to truth available here."
`index.html:294` and `method.html:370` repeat "optical sees water directly, so
it is the closest thing to truth available here."

`optical_water()` (`validate_mask.py:55-92`) takes every qualifying scene in
`[day-6, day+6]`, up to 10 of them per stretch in the committed table, and
reduces B3 and B11 with `.median()` before computing MNDWI. The truth layer is
a 13-day median composite of a moving flood, not a day. Agreement between two
multi-day composites of the same moving water is not the same quantity as
agreement on a date, and the direction of the bias is not established anywhere.

No uncertainty is attached to MNDWI > 0 as a water definition (turbidity, wet
soil, cloud shadow), so the published IoU has no error budget.

### C14 — `validate.validate()` gates the satellite product on gauge-only tests; nothing tests the satellite data

`export.py:3-7` states "The export is the gate. It takes a `ValidationResult`
and refuses to write anything unless it passed." `export_floodwater()` does
enforce that (`export.py:753-758`). But all eleven checks in
`validate.validate()` (`validate.py:97-353`) query the DuckDB `observation`,
`gauge` and `threshold` tables: row count, nulls, non-finite values, duplicate
days, future days, unit ranges, threshold units, required gauges, freshness,
latest-within-record, thresholds loaded. A grep for `flood|water|radar|corridor`
in `validate.py` returns only a comment. The radar product passes the gate
unconditionally regardless of what it contains.

### C15 — The only test that asks whether a radar product tracks the river covers the superseded product, and it skipped

`tests/test_flooded.py:367`, `test_every_published_area_moves_with_the_river`,
asserts `r > -0.2` between each stretch's area and its downstream gauge. Its
docstring calls this "the sufficient half" that calibration does not give you.
It reads `data/flooded.json` — the superseded product.

Running the suite locally: **584 passed, 3 skipped**, and one of the skips is
this test, at `tests/test_flooded.py:428`, reason "the store is behind the
committed measurement" — the guard at `test_flooded.py:401-403` drops any
stretch whose newest pass has no stage in the store, and when that empties the
set the test skips rather than fails.

`tests/test_floodwater.py` has no equivalent. It tests sum consistency, gap
handling, the withholding rules, flood ≤ total water, the permanent channel not
counting as flood year-round, and grid anchoring — all structural. **No test
anywhere asks whether the live VH product moves with the river**, and no
correlation between the live areas and gauge stage is published in any file or
on any page. The project's strongest scientific guard, and the one it describes
as its own hard-won lesson, does not cover what it ships.

### C16 — Orphaned and misdescribed code paths

- `src/parana_crecida/animation.py` uses `fl.water_mask(mosaic)` with the
  module-default −16 dB VV threshold — not even the per-stretch calibrated
  values — and JRC `occurrence > 90` alone (`animation.py:89,110`). Output
  `site/media/flood.gif` (`animation.py:36`) does not exist on disk; no page
  references it; `crecida animation` is not in CI. `README.md:413-416`
  describes it as the site's loop ("twenty real observations of the same 212 km
  of river"); the site's loop is `site/media/river.gif`, 31 frames over
  1,477 km, from `riverfilm.py`.
- `README.md:393-412`, the sections "Only full passes count" and "What it
  shows", are not marked superseded although the section above them
  (`README.md:367-370`) marks the fixed-threshold discussion as such. "What it
  shows" quotes 1,651 km² on 25 October and r = 0.57 — superseded VV figures —
  while the live product publishes 9,727.5 km² at 2026-10-28.
- `README.md:380-391` and `flooded.py:27-31` both state the threshold "is fixed
  at −16 dB". In the code −16.0 is a fallback (`flooded.py:64`) and every
  stretch overrides it from `data/corridors.json` (`flooded.py:147`) with
  values −14.5 to −17.0. This was already internally inconsistent before the
  method changed.
- `site/index.html:105` uses the translation key `limitGorge` for a bullet
  whose text is entirely about the Delta — a leftover from when the gorge was
  the withheld reach. Cosmetic.

---

## 5. What I could not verify, and why

**Everything requiring Earth Engine.** No credential was used in this stage, so
none of the following was independently measured: the VH backscatter
distribution over these corridors; that −25 dB separates water from land there;
the MERIT Hydro `hnd` values; the Sentinel-2 composites of 2026-10-22; the
recall/precision/IoU table in `data/mask_skill.json`; any area in
`data/floodwater.json` or `data/flooded.json`; and the claim that 12-day VH
composites cover each corridor. All are reported above as the repository states
them. Stage 2 or 3 will need a credential to test any of them.

**Whether −25 dB and HAND ≤ 10 m were ever swept.** `floodwater.py:102-106`
asserts 10 m beat 15 and 25. No committed script sweeps HAND, and the committed
VH sweep stops at −24. The sweep may have been done interactively; I can only
report that it is not reproducible from the repository.

**The quantitative claims inside rejected-experiment prose.** The change
detection IoU range (0.38–0.45), the +0.71 and +0.47 correlations at
README:959-963, the 353 km² Yacyretá figure, the 97,498 and 48,959 people
figures, the 4,445 km² Otsu result, the 536/521 km² bajante agreement, and the
398-observation area-vs-stage table. Some are traceable to scripts that still
run (`radar_trial.py`, `area_stage_history.py`); the change-detection IoUs are
not (C5); the exposure figures are attributed to a mask the code does not
implement (C3). None were re-run.

**The deployed site.** I read the repository and `origin/main`, not
`ammarmahfoud90.github.io/parana-crecida`. The headline, IoU table and withheld
list quoted above come from `origin/main:site/data/floodwater.json`, which is
what the Pages workflow uploads, but I did not load the live page.

**Why `corrientes_goya`'s optical footprint is 1,503 km².** The committed table
records it; whether that reflects persistent cloud over that reach on the
validation date, Sentinel-2 tiling against a corridor oriented across the tile
grid, or the SCL masking is not determinable without re-running the query.

**Whether the Earth Engine step runs in CI with a real credential — verified,
not assumed.** `git log data/floodwater.json` shows
`github-actions[bot]` commits on 2026-10-31 and 2026-11-02, and
`gh run list --workflow=daily.yml` shows the eight most recent scheduled runs
all `success`. So `EE_SERVICE_ACCOUNT_KEY` is set and the step executes. The
local working tree is 8 bot commits behind `origin/main`; that gap is a stale
checkout, not a pipeline failure.

**Nothing was fixed.** Per the stage instruction, this is inspection only.
No code, data, page or documentation was modified. C5 in particular means
`scripts/tune_mask.py` is currently broken at HEAD and will stay broken until a
later stage addresses it.
