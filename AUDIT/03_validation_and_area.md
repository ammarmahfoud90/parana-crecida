# Stage 3 — Validation design and flooded-area calculation audit

Read-only, design only. No code was changed, and nothing was measured in
Earth Engine. Dated 2026-11-05. Follows
[`AUDIT/01_inspection.md`](01_inspection.md) (what the pipeline is) and
[`AUDIT/02_science_review.md`](02_science_review.md) (whether the physics is
sound).

Everything quantitative below is derived either from reading the code or from
arithmetic on the **published** data files in `origin/main`, plus one geometric
reconstruction of the corridor polygons done locally with shapely and pyproj.
Where a number needs Earth Engine it is marked as such and a query is given.

Severity scale is the one used in Stage 2: **Critical** (a published number is
demonstrably wrong), **High** (a core assumption is unsupported and plausibly
biases published numbers), **Medium**, **Low**, **Supported**.

**Two results carry this stage.** The river-wide total double-counts water,
because adjacent corridors overlap by 1,328–1,651 km² each and the total is a
plain sum over them — the published `corridor_km2` of 65,635 km² is 17.7%
larger than the 55,938 km² its own geometry covers. And the validation that
gates publication has never looked at flood water at all: it scores the total
water mask, of which the permanent channel is 56–93% of the positive class, so
the headline skill figure is mostly a measure of whether the mask can find the
river.

---

# Part A — Validation

## A1. What the existing validation does and does not establish

### A1.1 — **High.** `flood_km2` has never been validated; only total water has

`scripts/validate_mask.py:175` scores `fw.water_mask(corridor, start, end)` —
**total** detected water, channel included. It never calls
`fw.flood_only()`.

But the site's flood claims all come from `flood_km2`:

- `index.html:543` — "Outside the permanent channel … 4,302 km² today against
  734 km² on that same dry pass, **5.9 times as much**"
- the dashed series on the index chart (`keyFlood`)
- `data/csv/flooded_area.csv`'s `beyond_permanent_km2` column

`flood_km2 = water_mask ∧ ¬permanent_water`. Its error is the error of
`water_mask` **plus** the error of the 1984–2021 permanent-water mask, and the
two are not independent: a mask that under-detects inside the channel transfers
that deficit into the flood term with the opposite sign. Stage 2 S1 is the
demonstration — on 2026-06-30 `water_km2` hit a record low while `flood_km2`
*rose*, because the missing 750 km² came out of the channel.

So the number the product is named for carries no validation at all, and its
error cannot be inferred from the published IoU.

### A1.2 — **High.** The published IoU is dominated by the permanent channel, which is the easy part

Computed from `origin/main:site/data/floodwater.json`, the permanent channel as
a share of the detected water class:

| | share of positive class that is permanent channel |
|---|---|
| minimum | **56%** (2026-10-28, flood peak) |
| median across 31 cycles | **84%** |
| maximum | **93%** (2026-03-26, dry season) |
| 2026-10-16 (nearest cycle to the validation date) | **75%** |

The validation date, 2026-10-22, falls between the 2026-10-16 and 2026-10-28
cycles, so roughly **two thirds of the positive class being scored is river
channel** — water that is always there, whose location is already known from
JRC, and which any threshold will find.

This is the same error the project correctly identified in its own history. It
rejected the JRC-bajante calibration because that "tunes a flood mask on the
one thing it is not for - the channel, at low flow"
(`floodwater.py:9-11`). Scoring the total mask against total optical water on a
flood date is a better test than that, but it is still a test whose answer is
set mostly by channel detection. The correction is not a different date; it is
to score **inside** and **outside** the permanent channel separately and
publish both.

### A1.3 — **High.** Tuning and evaluation share one date and overlapping reaches

Already recorded as Stage 2's framing; here is the leakage, precisely.

| | tuning (`scripts/tune_mask.py`) | evaluation (`scripts/validate_mask.py`) |
|---|---|---|
| date | `--day` default **2026-10-22** (line 36) | `--day` default **2026-10-22** (line 132) |
| reaches | `--keys` default `corrientes_goya,parana_villa_constitucion,goya_la_paz` (line 38) | all nine (line 154) |
| reference | `optical_water()` from `validate_mask` (line 44) | `optical_water()` (line 158) |
| radar window | ±4 days (`PAIR_DAYS = 4`, line 30) | ±6 days (`PAIR_DAYS = 6`, line 43) |

**Temporal leakage: total.** One date, used for both. There is no held-out
date anywhere in the repository.

**Spatial leakage: partial but decisive.** Three of nine reaches were used to
choose the parameters, and because the parameters are *global* the selection is
in-sample for the whole product, not just those three. The threshold was chosen
to maximise mean IoU over `corrientes_goya`, `goya_la_paz` and
`parana_villa_constitucion` — the three reaches that now score **0.48, 0.53 and
0.61**, the three worst on the river. The parameters are fitted to the hard
cases and then reported as river-wide skill.

**The gate inherits the leakage.** `export.SKILL_FLOOR = 0.45` is applied to
the same in-sample per-reach IoUs (`export.py:792`). A gate that selects on
in-sample scores selects partly on noise: a reach can clear 0.45 because that
date suited it. `corrientes_goya` clears it by 0.03.

**Reference-window mismatch.** The reference is a ±6-day composite while the
tuning radar window was ±4 days and the evaluation radar window ±6 days, and
production uses a backward-looking 11-day window (Stage 1 C11, Stage 2 S10).
Four different windows; no two of the three stages compare like with like.

### A1.4 — **Medium.** Reference cloud masking is incomplete in a way that matters for the best-scoring reaches

`scripts/validate_mask.py:73-75`:

```python
ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
```

That excludes SCL 3 (cloud shadow), 8 (cloud medium probability), 9 (cloud high
probability), 10 (thin cirrus) and 11 (snow/ice). It **keeps**:

| kept class | meaning | why it matters here |
|---|---|---|
| **0** | no data | fill pixels enter the median |
| **1** | saturated or defective | corrupt radiometry enters MNDWI |
| **2** | topographic cast shadow (pre-2022 products: "dark area pixels") | **dark in green and SWIR; MNDWI is unstable and can read as water.** Present on the valley walls of the gorge above Posadas — the two reaches now reported at IoU 0.83 and 0.90 |
| **7** | unclassified | unknown radiometry enters the reference |

Class definitions from the
[Sentinel-2 L2A Scene Classification legend](https://custom-scripts.sentinel-hub.com/sentinel-2/scene-classification)
and the
[Earth Engine COPERNICUS/S2_SR_HARMONIZED catalog](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED).

Two further gaps in the same function:

- **No cloud-shadow buffer and no cloud-proximity buffer.** SCL's shadow class
  is known to under-detect; standard practice is to project shadows from cloud
  geometry and solar angle, or to dilate the cloud mask. Neither is done, so
  partly shadowed pixels adjacent to masked cloud stay in the reference.
- **The scene filter is tile-level.** `CLOUDY_PIXEL_PERCENTAGE < 20`
  (line 81) is a property of a ~110 km tile, while the corridors are ~200 km
  long. A tile at 19% cloud can be completely clouded over one corridor and
  clear elsewhere.

`COPERNICUS/S2_CLOUD_PROBABILITY` (s2cloudless) exists in Earth Engine and is
not used. Neither is SCL class 6, Sentinel-2's own water class, which would be
a free second opinion on the reference.

### A1.5 — **Medium.** The reference is not archived, so the comparison cannot be re-scored or audited

`data/mask_skill.json` records eight fields per reach:

```
clear_scenes, from_name, key, now, previous, seen_km2, to_name, truth_km2
```

with `now`/`previous` each holding only `recall`, `precision`, `iou`. There is
**no confusion matrix**, no TP/FP/FN areas, no per-date repeat, no interval, and
no stored reference raster or `seen` mask. `agreement()` computes the areas it
needs (`validate_mask.py:114-125`) and then discards everything except the
three ratios.

Consequences: nobody — including a later stage of this audit — can recompute
the metrics, decompose an IoU into omission and commission area, check the area
bias, or confirm that the reference was what it is said to be. A published
measurement whose evidence is not retained is not reproducible.

### A1.6 — **Supported.** What the existing validation got right

Recorded so none of it is lost in a rewrite:

- Scoring against an **independent sensor** rather than against the channel at
  low flow. This was the right correction and remains the project's best
  decision.
- Restricting the comparison to the footprint the optical scene can actually
  see (`seen`), rather than counting unobserved ground as dry. The code comment
  at `validate_mask.py:60-61` records that the first version did the wrong thing
  and reported zero water in a flood.
- Reporting **recall and precision separately** rather than a single accuracy
  figure, with the correct explanation of what each failure mode means
  (`validate_mask.py:17-25`).
- Reporting **per reach**, and publishing the per-reach table rather than only a
  river-wide mean.
- Scoring the **old and new masks on the same reference**, so the comparison is
  like-for-like in that one respect.
- Using **MNDWI rather than NDWI**, with the stated reason that NDWI does not
  hold up on a river this turbid. Zero is a defensible threshold; Otsu-optimal
  MNDWI thresholds on Sentinel-2 fall close to it, and a global multi-sensor
  assessment found MNDWI metrics stay above 80% across thresholds from −0.20 to
  +0.20 ([*Scientific Data*, 2025](https://www.nature.com/articles/s41597-025-05562-z)).
- **Withholding** a reach that could not be scored, with the reason travelling
  to the page.

---

## A2. The validation design

A specification, not a result. Nothing below has been run.

### A2.0 Principle

The mask must be scored on data that played no part in choosing its
parameters, over enough dates and reaches to separate reach character from date
noise, separately inside and outside the permanent channel, and separately in
the strata where the sensor is known to be blind. Every published figure must
be traceable to a retained confusion matrix.

### A2.1 Freeze a tuning manifest first

Nothing can be held out until what was used is written down. Create
`data/tuning_manifest.json`, committed, recording: the parameter sweep's dates,
reaches, reference definition, the swept values and the selection rule. For the
current parameters, from the code and git history, that is: date 2026-10-22,
reaches `corrientes_goya`, `goya_la_paz`, `parana_villa_constitucion`,
reference MNDWI > 0 from a ±6-day S2 median, swept VH ∈ {−20, −22, −24} with
HAND fixed, selected value −25 dB — which, as Stage 1 C5 established, is
outside the swept set, so the manifest must say that the selection of −25 dB and
of HAND ≤ 10 m is **not reproducible from committed code**. A manifest that
records an unreproducible choice is more useful than one that implies a
reproducible one.

Any date or reach in the manifest is permanently ineligible for evaluation.

### A2.2 Three evaluation sets, in order of strength

**Set 1 — out-of-period, Landsat/JRC (strongest for leakage, weakest for currency).**

`JRC/GSW1_4/MonthlyHistory` gives per-month water / not-water / no-data at 30 m
for 1984–2021, from Landsat, by the JRC expert system — a different sensor
family, a different algorithm, a different team, and **temporally disjoint from
the tuning date by four to ten years**. Sentinel-1 begins 2014-10-03, so
2015–2021 gives roughly 84 candidate months per reach.

Run the current mask over 12-day windows aligned to each candidate month,
restrict to the month's non-no-data footprint, and score. This is the only
available design with **zero temporal leakage and full spatial coverage of every
reach, including the Delta**, which has never been scored at all. It also spans
real flood conditions: the 2015–16 and 2019 highs and the 2020–21 bajante.

Limitations to state: GSW is optical, so it shares the canopy blindness (A2.5);
monthly aggregation is coarser than a 12-day window; and no-data from cloud is
extensive in the wet season. It does not test the mask on the 2026 flood.

**Set 2 — held-out dates, Sentinel-2 and Landsat (the main operational set).**

Enumerate every date in the Sentinel-1 era where a reach has (a) ≥60% of its
corridor visible in a *single-date* optical scene after full masking (A2.4) and
(b) a radar window within ±3 days. Add Landsat 8 and 9
(`LANDSAT/LC08/C02/T1_L2`, `LANDSAT/LC09/C02/T1_L2`) — 16-day repeat each,
offset by 8 days, 30 m — which roughly doubles the candidate dates over
Sentinel-2 alone, at coarser resolution.

Exclude 2026-10-22 and any date within ±12 days of it (one repeat cycle, so no
shared radar acquisition can enter both sets). Stratify the remaining dates by
flood condition (A2.6) and sample so that each reach gets at least three dates
spanning at least two conditions, where the imagery allows. Report the realised
count per reach; reaches that cannot reach three dates publish "insufficient
reference", not a figure.

**Set 3 — leave-one-out cross-validation, for the parameters only.**

Repeat the parameter sweep under leave-one-reach-out and leave-one-date-out,
recording the optimal threshold per fold. Two outputs, both findings in
themselves:

- the **spread of the optimal threshold** across folds. If it spans more than
  about 2 dB, a single global threshold is not supported by the data, which
  speaks directly to Stage 2 S6 (geometry) and to the project's own history of
  nine per-reach thresholds.
- the **held-out score**, which is the honest estimate of skill. Expect it to
  be below 0.69.

Sweep HAND as well as VH, since no committed code ever has, and sweep over a
range that contains the value in use: VH ∈ {−18 … −27} at 0.5 dB steps, HAND ∈
{5, 10, 15, 20, 25, ∞} m, where ∞ measures what HAND contributes at all.

### A2.3 The splits, stated as rules

1. No date in the tuning manifest, nor within ±12 days of one, may appear in any
   evaluation set.
2. No reach in the tuning manifest may contribute to the headline skill figure.
   Those three reaches are reported separately and labelled in-sample.
3. The publication gate (`SKILL_FLOOR`) must be driven by held-out per-reach
   scores from Set 2, never by the tuning score.
4. The threshold is chosen once, on the manifest data, and then frozen. If a
   later sweep changes it, every evaluation set becomes tuning data and a new
   held-out set must be found.
5. Spatial blocks for resampling (A2.7) must not straddle the corridor overlaps
   identified in Part B, or the same ground enters two folds.

### A2.4 How the optical reference mask is generated

Per candidate date and reach, from `COPERNICUS/S2_SR_HARMONIZED`
(and the Landsat C02 L2 equivalents):

1. **Single date, not a composite.** One acquisition date per comparison, so
   the reference is an instant and the time offset to the radar is a stated
   number rather than a 13-day blur. This is the main change from the current
   `optical_water()`.
2. **Mask with every non-surface class**: exclude SCL 0, 1, 2, 3, 7, 8, 9, 10,
   11 — adding 0, 1, 2 and 7 to what is excluded now (A1.4).
3. **Add s2cloudless**: drop pixels with `COPERNICUS/S2_CLOUD_PROBABILITY`
   above about 40%.
4. **Project cloud shadows** from the cloud mask using the scene's
   `MEAN_SOLAR_AZIMUTH_ANGLE` / `MEAN_SOLAR_ZENITH_ANGLE` and a nominal cloud
   height range, and dilate the union of cloud and shadow by 300 m.
5. **MNDWI** = (B3 − B11)/(B3 + B11), threshold at 0, with B11 (20 m) and B3
   (10 m) brought to a common 20 m grid explicitly rather than by default
   resampling.
6. **Second opinion**: compute SCL class 6 (water) and AWEI<sub>nsh</sub> as
   well. Where the three disagree, that disagreement is the reference's own
   uncertainty and must be carried into the metrics as a band, not averaged away.
7. **`seen` mask** = pixels surviving 2–4. Require `seen ≥ 60%` of the corridor
   for the reach-date to be scored at all, and publish the realised fraction
   next to every score. Stage 1 C2 found `corrientes_goya` currently publishes a
   full-corridor figure on a score measured over **16%** of it; a minimum seen
   fraction is the fix.
8. **Co-registration check**: before scoring, estimate the offset between the
   radar water boundary and the optical water boundary over the permanent
   channel, where both sensors agree on a stable feature, and report it. If it
   exceeds one 60 m cell, scoring cells must be coarsened until it does not.

**Stated limitations of the reference, to be published with the numbers:**

- It cannot see water under a canopy. A SWOT assessment over this basin found
  SWOT detects floodplain inundation that multispectral products miss
  specifically under vegetation
  ([*Scientific Reports*, 2026](https://www.nature.com/articles/s41598-026-53807-7)),
  and the Paraná RADARSAT-2 work notes optical maps misallocate herbaceous
  wetland ([*Remote Sensing* 8(3):174](https://www.mdpi.com/2072-4292/8/3/174)).
  So this reference cannot measure the project's dominant error (Stage 2 S3,
  S4), and the recall it reports is recall over open water only.
- Cloud cover is correlated with flooding, so the reference footprint is a
  biased sample of the corridor: the wettest conditions are the least visible.
- MNDWI > 0 has no published error budget over turbid flood water (Stage 2 S4).
- Sediment plumes, aquatic macrophyte mats (common on the Paraná) and shallow
  water over bright substrate all depress MNDWI and read as land.
- Landsat at 30 m and Sentinel-2 at 20 m resolve the braided shoreline
  differently; scores must not be pooled across sensors without reporting the
  sensor.

### A2.5 Handling the stratum the reference cannot see

No optical reference will bound the under-canopy miss, so it must be isolated
rather than averaged in:

1. **Stratify by land cover** using ESA WorldCover 10 m — tree cover (10),
   shrubland (20), grassland (30), cropland (40), built-up (50), herbaceous
   wetland (90), permanent water (80) — and report every metric per stratum.
   Recall in the tree-cover and herbaceous-wetland strata is explicitly a
   measure against a blind reference and must be labelled as uninformative
   rather than published as skill.
2. **Bound the miss with an independent sensor on a subset.** Options, in
   order of practicality: SWOT L2 HR water-mask products from PO.DAAC, which
   cover this basin and see under vegetation; ALOS-2 PALSAR-2 L-band ScanSAR,
   which is what the Paraná delta SAR evaluation recommends for this reach
   ([*Revista de Teledetección*](https://polipapers.upv.es/index.php/raet/article/view/25725));
   Sentinel-1 SLC interferometric coherence, which separates flooded vegetation
   by the coherent water/stem interface but is not available in Earth Engine.
   Even one date over one reach converts "we cannot see it" into a number.
3. **Until then, state the floor as a floor with no measured depth.** The site
   already says every figure is a floor. What it must stop doing is implying
   that 0.69 characterises the instrument when the dominant error is outside the
   measurement by construction.

### A2.6 Flood-condition strata

The project has the gauge record, the climatology and the fitted travel times,
so conditions can be defined without new data. For each reach-date, label by
the stage at the reach's downstream gauge:

| stratum | definition |
|---|---|
| low | stage below the 25th day-of-year percentile |
| normal | 25th–75th |
| rising | above the 75th and rising over the preceding 5 days |
| peak | within 2 days of a local maximum, above the INA alert level |
| falling | above the 75th and falling |

Separating **rising** from **falling** is not optional here: the project has
already measured hysteresis in its own area–stage relation (README's withdrawn
section records the falling limb sitting above the rising one at the same
stage), so a single figure pooled across limbs will hide it.

Report the reach × condition matrix of each metric, and the count behind every
cell. Cells with fewer than three dates carry the count and no summary
statistic.

### A2.7 Metrics, and how to get an honest interval

Per reach × date × stratum, compute the confusion matrix **in area**, inside
the `seen` footprint, and **separately for the permanent-channel and
beyond-channel partitions** (A1.1, A1.2):

| quantity | definition |
|---|---|
| TP, FP, FN, TN | km², from `pixelArea()` sums |
| recall (producer's accuracy) | TP / (TP + FN) |
| precision (user's accuracy) | TP / (TP + FP) |
| omission error | FN / (TP + FN) = 1 − recall |
| commission error | FP / (TP + FP) = 1 − precision |
| IoU | TP / (TP + FP + FN) |
| F1 | 2·TP / (2·TP + FP + FN) |
| **area bias** | A_test − A_truth, km² |
| **relative area error** | (A_test − A_truth) / A_truth |

Area bias and relative area error are absent from the current validation and
are the metrics that bear most directly on what the site publishes, which is an
**area**, not a map. A mask can have zero area bias and poor IoU (errors
cancelling in place) or good IoU and a systematic area bias (a boundary shift —
Stage 2 S9 gives the mechanism, a ~110 m effective resolution against a 60 m
grid). Publishing IoU alone cannot distinguish them, and the number on the
front page is the one IoU does not constrain.

**Intervals must come from a spatial block bootstrap, not from pixel counts.**
A corridor holds ~2.6 × 10⁶ cells at 60 m; a binomial interval on that is
essentially zero width and would be a false precision, because 60 m cells on a
river are strongly autocorrelated. Resample 5 km × 5 km blocks with
replacement, 1,000 draws, respecting rule 5 in A2.3. Report the 2.5/97.5
percentile interval on every metric. This is almost certainly why no interval
is published today; the answer is a block bootstrap, not no interval.

**Variance decomposition.** With several dates per reach, fit the simplest
possible model to each metric — reach effect plus date effect plus residual —
and report how much of the spread is reach and how much is date. The site
currently attributes a 0.48–0.90 spread entirely to reach character
(`index.html:493-495`), on one date, where reach and date are not separable.
This is the measurement that decides whether that story is true.

### A2.8 Baselines

A score means little without something to beat. Score all of these on the same
held-out sets, with the same metrics:

| baseline | why |
|---|---|
| **the superseded per-reach VV threshold** | already implemented (`flooded.water_mask`); the comparison the project has been publishing, but on held-out data |
| **VV at a single global threshold** | isolates the polarisation choice from the per-reach calibration — currently confounded, since the change altered both at once (Stage 2 S3) |
| **VV + VH combined** | the collection carries both; the pairing is standard, and Stage 2 S3 gives the reason to expect it to beat either alone |
| **Otsu or a bimodal fit per scene** | the textbook method the project rejected on VV evidence (`flooded.py:13-31`); the rejection was never retested on VH |
| **JRC GSW permanent water alone** | the trivial "the river is where the river always is" predictor. **Essential**: given that the channel is 56–93% of the positive class (A1.2), this baseline will score surprisingly well, and the margin over it is the real measure of what the radar adds |
| **HAND ≤ 10 m alone** | the trivial terrain predictor, to show the radar is contributing |
| **change detection against a dry reference** | rejected on evidence no longer reproducible (Stage 1 C5); it addresses the seasonal-drift problem (Stage 2 S13) and deserves a fair retest |

The GSW-alone baseline is the one to run first. If the published mask does not
beat "permanent water, unchanged" by a wide margin on the **beyond-channel**
partition, the product does not yet measure flooding.

Also: cite or drop the "0.7–0.9" band the site compares itself to
(Stage 2 S15). Comparable thresholding baselines on Sen1Floods11 score around
0.55 IoU, with trained networks near 0.73
([Bonafilia et al., CVPRW 2020](https://openaccess.thecvf.com/content_CVPRW_2020/papers/w11/Bonafilia_Sen1Floods11_A_Georeferenced_Dataset_to_Train_and_Test_Deep_Learning_CVPRW_2020_paper.pdf);
[arXiv:2302.08180](https://arxiv.org/pdf/2302.08180)).

### A2.9 What gets published, and what gets withheld

Replace the six-number-per-reach `mask_skill.json` with a record that retains
the evidence: per reach × date × stratum × partition, the full confusion matrix
in km², the `seen` fraction, the sensor, the radar–optical time offset, the
co-registration estimate, and the bootstrap interval. Archive the reference and
`seen` masks as assets so the comparison can be re-scored (A1.5).

Publication rules, which the current gate approximates but on the wrong data:

- A reach publishes an area only with **held-out** scores on **at least three
  dates** spanning **at least two flood conditions**, each with `seen ≥ 60%`.
- The gate must be on the **beyond-channel** IoU, not the total-water IoU,
  because the beyond-channel figure is what is published.
- A reach that fails shows an explicit **unvalidated** or **insufficient
  reference** status and no number — which the project already does well, and
  should keep.
- Publish the area bias and the interval beside every area. An area with no
  interval is not a measurement.
- Do not publish exposure until the mask is validated **for that use**, which
  means a settlement stratum with its own confusion matrix. `exposure.py`
  already records the measurement that forbids it — a city of half a million
  reading as a lake on one pass — and it is correctly withheld today. Keep it
  withheld.

---

# Part B — Area calculation

Tracing classified pixels to square kilometres, at
`src/parana_crecida/floodwater.py:181-193` and
`src/parana_crecida/export.py:803-823`.

## B1 — **Critical.** Adjacent corridors overlap, and the river-wide total is a plain sum over them

`flooded.corridor_geometry()` buffers each centreline by `BUFFER_M = 20_000` m
(`flooded.py:61,163-167`). Adjacent reaches share a gauge endpoint, so each
pair of neighbouring corridors necessarily overlaps in a ~20 km-radius region
**centred on a gauge, which sits on the river** — so the overlap contains main
channel, not empty floodplain.

I reconstructed the nine corridor polygons locally from `data/corridors.json`
in an Albers equal-area projection (standard parallels 26°S/33°S) and buffered
them by 20,000 m. The reconstruction reproduces each committed `corridor_km2`
to within 0.1–0.5%, so the geometry is faithful:

| stretch | reconstructed km² | published `corridor_km2` | diff |
|---|---|---|---|
| puerto_iguazu_libertador | 7,989 | 7,979 | +0.1% |
| libertador_posadas | 6,648 | 6,625 | +0.3% |
| posadas_ita_ibate | 7,587 | 7,551 | +0.5% |
| ita_ibate_corrientes | 7,817 | 7,781 | +0.5% |
| corrientes_goya | 9,492 | 9,474 | +0.2% |
| goya_la_paz | 9,525 | 9,501 | +0.3% |
| la_paz_parana | 7,127 | 7,103 | +0.3% |
| parana_villa_constitucion | 9,643 | 9,621 | +0.2% |

Pairwise overlaps between the eight published corridors:

| pair | overlap |
|---|---|
| puerto_iguazu_libertador × libertador_posadas | 1,328 km² |
| libertador_posadas × posadas_ita_ibate | 1,392 km² |
| posadas_ita_ibate × ita_ibate_corrientes | 1,422 km² |
| ita_ibate_corrientes × corrientes_goya | 1,394 km² |
| corrientes_goya × goya_la_paz | 1,367 km² |
| goya_la_paz × la_paz_parana | 1,334 km² |
| la_paz_parana × parana_villa_constitucion | 1,651 km² |

- sum of the eight corridors: **65,827 km²**
- **union** of the eight corridors: **55,938 km²**
- double-counted corridor area: **9,889 km², a 17.7% inflation**

### What is certainly wrong

`export.py:837` publishes `corridor_km2` as `sum(s.get("corridor_km2") ...)`.
The published value in `origin/main:site/data/floodwater.json` is **65,635 km²**
for ground that covers **55,938 km²**. No estimation is involved; this number
is 17.7% too large.

### What is wrong by the same mechanism, magnitude estimated

The river-wide water total is also a plain sum. `export.py:810-816`:

```python
row["water_km2"] += water[index]
row["flood_km2"] += flood[index] if index < len(flood) else 0.0
```

Each stretch's area comes from `area_km2(corridor, …)` reduced over **its own**
corridor (`floodwater.py:181-193`), so water in an overlap lens is measured by
both owners and added twice.

Estimating the magnitude: 371 km of the 1,492 km of centreline (25%) lies
inside a neighbouring corridor. Applying each reach's own median in-channel
water per river-km to its overlapped length gives ≈1,413 km² of channel water
lying in overlap regions, counted by both owners — so **≈700 km² duplicated
once** in the river total, about **14% of the channel term** and **~7% of the
9,728 km² headline**. Floodplain water in the lenses adds to this during a
flood.

**This is an estimate, not a measurement.** It assumes water is distributed
uniformly along each reach, which is wrong for `posadas_ita_ibate` in
particular, where the Yacyretá reservoir is a localised feature and which
contributes 535 km² of the 1,413 km². Treat the figure as order-of-magnitude:
somewhere between a few hundred and ~1,400 km². The exact value needs one Earth
Engine query — reduce the mask over the dissolved union and compare with the sum
over corridors (B9).

### The repository already has the fix and uses it for the picture, not the number

`mapframes.union_geometry()` does exactly the right thing —
`ee.Geometry.MultiPolygon([...]).dissolve(maxError=1000)` (`mapframes.py:68-74`)
— and that dissolved geometry is used to render the frames. So the **map and the
film draw the union correctly while the number printed beside them sums the
overlaps.** The primitive needed is already imported.

### Why no test caught it, and why the cross-check was not one

Commit `73a539c` states that the film's areas "are computed independently of the
export and agree with it to a rounding", and reads that agreement as
corroboration. They agree because `riverfilm.py:316-324` sums
`fw.area_km2(corridor, …)` over the same overlapping corridors that
`export.py:810` does. Two independent implementations of the same error agreeing
with each other is not a cross-check. `mapframes.py` sums the same way
(`mapframes.py:213-215`), so all three published totals — headline, map frame
caption, film frame caption — carry the same duplication.

`river_km` is unaffected: reach lengths do not overlap, and the published
1,477.3 km matches my 1,492 km of centreline to within 1%.

## B2 — **High.** A reduction over no observed pixels returns 0.0 km², not "no data"

`floodwater.py:193`:

```python
return float(next(iter(stats.values()), 0) or 0) / 1e6
```

`reduceRegion` returns `None` for a band with no unmasked pixels in the region.
`or 0` converts that to `0`, and the function returns **0.0 km²**. The
superseded module does the opposite and correctly returns `None` when there is
nothing to measure (`flooded.py:227-228`).

This is the explicit rule — *never treat missing observations as zero
inundation* — violated at the lowest level of the calculation. It is also the
mechanism behind Stage 2 S1 in its partial form: where **some** of a corridor is
unobserved, the reduction returns a valid-looking number that is too small by
the area of the gap, because `water_mask` leaves unobserved pixels masked,
`.selfMask()` keeps them masked, and masked pixels contribute nothing to the
sum. There is no flag, no count and no way to tell such a cycle from a genuinely
dry one.

Secondary fragility in the same line: `next(iter(stats.values()))` takes an
arbitrary dictionary value. It is safe today because
`vh.lt(…).And(hand.lte(…)).selfMask().multiply(pixelArea())` carries a single
band, but it will silently take the wrong band if that ever changes.

## B3 — **High.** Nothing measures observed area, so partial coverage is invisible

The superseded pipeline measured the observed fraction and dropped a window
below 95% (`flooded.py:300-308` and `550-561`, `MIN_COVERAGE = 0.95`), with a
comment stating the exact failure mode. `floodwater.py` computes **no**
equivalent: no `seen` reduction, no coverage ratio, no threshold, and —
unlike `flooded.py`, which stored `images` per window (`flooded.py:406`) — no
image count in the output.

So the published series records, per reach per cycle, a single number with no
denominator. A reader cannot tell 1,400 km² of water over a fully observed
corridor from 1,400 km² over two thirds of one. Nor can an auditor: Stage 2 S1
had to infer the 2026-06-30 failure from the stability of the channel term,
because the data carries nothing better.

Minimum fix, and it is cheap: reduce `water_mask(...).mask()` over the corridor
in the same call, store `seen_km2` and `images` beside every area, publish the
observed fraction, and refuse to publish a cycle below a stated coverage.

## B4 — **High.** There is no "unclassified" category; unobserved ground is folded into "not water"

The mask is binary over observed pixels and absent elsewhere, so the
calculation has two states where it needs three:

| needed | present |
|---|---|
| water | yes |
| not water | yes |
| **not observed** | **no — indistinguishable from "not water"** |

Every downstream consumer inherits this. `export.py`'s `complete` flag
(`export.py:818`) detects a **missing row** — a reach that produced no entry for
a cycle — which is a real and useful guard, and the test suite covers it
(`tests/test_floodwater.py:105`). But it cannot detect a **bad row**: a reach
that reported a number computed over half its corridor is `complete: true`. The
2026-06-30 cycle is `complete: true` with eight of eight reaches reporting.

## B5 — **Medium.** The reported area covers the whole corridor; the validated area covers a fraction of it

`area_km2` reduces over the **entire** corridor geometry. The validation
measured agreement over the optical `seen` footprint only, which Stage 1 C2
quantified: 16% of `corrientes_goya`, 41% of `parana_villa_constitucion`, 64% of
two more. So the published figure is an extrapolation from the scored fraction
to the whole corridor, by assumption, with no statement of that assumption and
no uncertainty attached.

The extrapolation is not obviously safe, because the optical footprint is
cloud-selected and cloud is correlated with rainfall and therefore with
flooding. The scored sample is biased toward the drier, clearer parts of each
corridor.

## B6 — **Medium.** Three grids, one of them coarse, combined without explicit resampling

Sentinel-1 at 10 m orthorectified against SRTM 30 m, MERIT Hydro HAND at
**92.77 m** ([Earth Engine catalog](https://developers.google.com/earth-engine/datasets/catalog/MERIT_Hydro_v1_0_1)),
JRC GSW at 30 m, reduced at 60 m. Earth Engine reprojects on demand with
nearest-neighbour by default, so the HAND decision boundary is resampled from a
grid 1.5× coarser than the analysis scale and 9× coarser than the radar, with no
`reproject` or `resample` call anywhere to make the choice explicit. Combined
with the ~110 m effective resolution of the speckle filter (Stage 2 S9), the
mask's real resolution is coarser than the grid its areas are computed on.

**Verified correct, and worth recording as such:** `ee.Image.pixelArea()` gives
true per-pixel area, so the km² conversion is right; `Reducer.sum()` is weighted
by default, so boundary pixels partially inside the corridor contribute
fractionally, which is the desired behaviour for an area; and
`bestEffort=True` with `maxPixels=1e10` cannot silently coarsen the scale here,
because a ~9,500 km² corridor at 60 m needs ~2.6 × 10⁶ pixels, three orders of
magnitude inside the limit. The `/1e6` m²→km² conversion is correct.

## B7 — **Supported, with one latent trap.** Permanent-water separation is clean

`flood_only = water_mask ∧ ¬permanent_water` (`floodwater.py:176-178`), with
`permanent_water()` unioning JRC `occurrence > 90` and `YearlyHistory`
`waterClass == 3` for 2021, both `.unmask(0)` (`floodwater.py:166-174`). The
defensive `unmask(0)` is correct and prevents mask propagation from the
reference layers, so the three-way split of the *classified* pixels — channel,
beyond-channel, neither — is clean, and `tests/test_floodwater.py:207` asserts
flood ≤ total water.

The latent trap is in the HAND term, and the project is on the right side of it
by luck rather than by construction. MERIT Hydro's `hnd` **is** height above
nearest drainage — a relative height, as the
[MERIT Hydro documentation](https://global-hydrodynamics.github.io/MERIT_Hydro/)
states, which is what the code assumes and is correct. But the Earth Engine
catalog page describes `hnd` as "hydrologically adjusted elevations … referenced
to the EGM96 geoid", conflating it with the `elv` band; a reader trusting the
catalog would conclude `hand.lte(10)` selects ground below 10 m above sea level,
which over reaches at 55–150 m asl would select almost nothing. Worth a comment
in the code, because the next person to read that catalog page will not be able
to tell.

The real trap: MERIT Hydro uses **−9999 as an ocean sentinel rather than
nodata**. So `hand.lte(10)` is *true* over ocean — HAND passes it rather than
rejecting it, and `hnd` is never masked, so the `.And()` loses no pixels
inland. Harmless for the eight published inland reaches; it would silently
disable the HAND constraint if the product ever extended into the Delta and the
Río de la Plata, which is the one reach currently withheld.

## B8 — **High.** What the measurement can be called

The published label is "km² del valle del Paraná bajo agua" / "km² of the Paraná
valley under water" (`index.html:160`). What the pipeline computes is:

> the area, within a 20 km buffer of the reach centreline, of pixels whose
> despeckled Sentinel-1 VH σ° falls below −25 dB and whose MERIT HAND is ≤ 10 m,
> in a mosaic of acquisitions over an 11-day window, among pixels that were
> observed, summed over corridors that overlap.

That is **detected open-water area**, not flood extent. The gap is not
rhetorical:

- it excludes water under vegetation, which the project agrees is its dominant
  miss and which no threshold recovers (Stage 2 S3);
- it includes anything else dark at C-band VH — smooth wet soil, tarmac, and at
  least one city on at least one pass, which `exposure.py:17-21` measured
  (Stage 2 S11);
- it is an 11-day mosaic of several dates and geometries, not an extent at an
  instant (Stage 2 S10);
- it is bounded by a corridor envelope that is an analysis choice, so "the
  valley" means "within 20 km of the centreline";
- unobserved ground is counted as dry (B2, B3);
- overlaps are counted twice (B1).

`flood_km2` is **detected open-water area outside a 1984–2021 permanent-water
mask**, which is nearer to flood extent and is the figure with no validation at
all (A1.1).

The project's own documentation is mostly careful about this — "every figure
here is a floor" appears in the module docstring, the method page and the index
page. The front-page caption is not, and the published CSV column is named
`water_km2` (correct) and `beyond_permanent_km2` (correct), which is better than
the page. Recommended wording, usable as is: **"open water detected by radar"**
for `water_km2`, **"open water beyond the permanent channel"** for `flood_km2`,
and nothing on any surface that reads as "flooded area" until the vegetated
stratum has a measured bound.

## B9 — The queries that settle what I could not

Four Earth Engine queries, each small, in priority order:

1. **The duplication, exactly.** For one cycle, reduce
   `water_mask` over `union_geometry(stretches)` (already implemented at
   `mapframes.py:68`) and compare with the sum over corridors. The difference is
   the duplicated water, measured rather than estimated (B1).
2. **The 2026-06-30 failure.** Count IW VH scenes intersecting
   `posadas_ita_ibate` and `ita_ibate_corrientes` in 19–30 June 2026, and reduce
   `water_mask(...).mask()` over each corridor to get the observed fraction.
   This separates missing acquisitions from mask failure (Stage 2 S1).
3. **The coverage series.** Reduce `.mask()` over every corridor for all 31
   published cycles. One pass, and it retro-fits the denominator the series has
   never had (B3). It will also show whether early single-satellite cycles are
   systematically less observed than later two-satellite ones (Stage 2 S14).
4. **The channel-only score.** Re-run `agreement()` on the 2026-10-22 reference
   restricted to inside and outside `permanent_water()` separately. Same date,
   same leakage, no new data — but it decomposes the published 0.69 into "found
   the river" and "found the flood", which is the single most informative thing
   available without new imagery (A1.2).

---

# What is needed to run the validation

**Credentials and access.**

- An **Earth Engine credential**. This session had none, which is why nothing
  here was measured. One exists in CI as `EE_SERVICE_ACCOUNT_KEY`
  (`.github/workflows/daily.yml:81`) and is demonstrably live — the bot has been
  committing `data/floodwater.json` — so the requirement is access to it or an
  equivalent, locally. `docs/earth-engine-service-account.md` documents the
  setup.
- A **PO.DAAC Earthdata login**, only for the SWOT option in A2.5. Not needed
  for the main design.

**Already available in Earth Engine, no new acquisition needed.**

| dataset | role |
|---|---|
| `COPERNICUS/S1_GRD` | the mask under test; `relativeOrbitNumber_start`, `platform_number` and the `angle` band are all present and all currently unused |
| `COPERNICUS/S2_SR_HARMONIZED` | reference, Set 2 |
| `COPERNICUS/S2_CLOUD_PROBABILITY` | s2cloudless masking (A2.4), currently unused |
| `LANDSAT/LC08/C02/T1_L2`, `LANDSAT/LC09/C02/T1_L2` | reference, Set 2, roughly doubles the candidate dates |
| `JRC/GSW1_4/MonthlyHistory` | reference, Set 1 — the out-of-period set, 1984–2021, and the only design with zero temporal leakage |
| `JRC/GSW1_4/GlobalSurfaceWater` | the permanent-water baseline in A2.8 |
| `ESA/WorldCover/v200` | land-cover strata (A2.5) and a settlement mask |
| `MERIT/Hydro/v1_0_1` | HAND, and `upa` for recalculating it with a basin-specific drainage threshold (Stage 2 S8) |
| the DuckDB gauge store | flood-condition strata (A2.6); already in the repository |

**Not available, and the gap must be stated rather than filled.**

- Any optical or SAR reference that sees under a closed canopy over these
  reaches on a date in the published series. Without it, the recall of the
  vegetated stratum cannot be measured and must be reported as unmeasured, not
  as the 0.80 figure.
- A tuning manifest. It does not exist and cannot be reconstructed exactly:
  Stage 1 C5 established that the committed sweep never tested the value in use
  (−25 dB, outside a sweep that stops at −24) and never swept HAND at all, and
  that `scripts/tune_mask.py` does not run at HEAD because it calls a function
  removed in `73a539c`. So the manifest must record the parameter choice as
  **not reproducible**, and the first evaluation is therefore a test of
  parameters whose provenance is partly unknown.
- Archived reference and `seen` masks from the 2026-10-22 comparison. Not
  retained (A1.5), so that comparison cannot be re-scored and must be redone
  rather than re-analysed.

**Estimated cost.** Set 1 is roughly 84 month-windows × 9 reaches ≈ 750
reductions, each comparable to one existing cycle measurement. Set 2 depends on
how many dates clear the 60% `seen` bar, which is itself unknown until the
enumeration in A2.2 is run — and that enumeration is the cheapest useful thing
in this document, because if the answer is "two reaches have three usable dates"
then the honest conclusion is that this product cannot be validated to the
standard its front page implies, and that is worth knowing before anything else
is built.

---

## Sources

- [Earth Engine Data Catalog — COPERNICUS/S1_GRD](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD)
- [Earth Engine Data Catalog — COPERNICUS/S2_SR_HARMONIZED](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED)
- [Earth Engine Data Catalog — MERIT Hydro v1.0.1](https://developers.google.com/earth-engine/datasets/catalog/MERIT_Hydro_v1_0_1)
- [MERIT Hydro — official documentation (variable definitions, `hnd` and ocean sentinels)](https://global-hydrodynamics.github.io/MERIT_Hydro/)
- [Sentinel-2 L2A Scene Classification Map — class legend](https://custom-scripts.sentinel-hub.com/sentinel-2/scene-classification)
- [Yamazaki et al. — MERIT Hydro, *Water Resources Research* 55 (2019)](https://www.researchgate.net/publication/333438388_MERIT_Hydro_A_High-Resolution_Global_Hydrography_Map_Based_on_Latest_Topography_Dataset)
- [Bonafilia et al. — Sen1Floods11, CVPRW 2020](https://openaccess.thecvf.com/content_CVPRW_2020/papers/w11/Bonafilia_Sen1Floods11_A_Georeferenced_Dataset_to_Train_and_Test_Deep_Learning_CVPRW_2020_paper.pdf)
- [Cross Modal Distillation for Flood Extent Mapping, arXiv:2302.08180](https://arxiv.org/pdf/2302.08180)
- [Evaluation of SAR systems for hydrological monitoring of wetlands in the Paraná River delta, *Revista de Teledetección*](https://polipapers.upv.es/index.php/raet/article/view/25725)
- [Mapping Plant Functional Types in Floodplain Wetlands: RADARSAT-2, *Remote Sensing* 8(3):174 (2016)](https://www.mdpi.com/2072-4292/8/3/174)
- [Assessing SWOT capabilities for monitoring floodplain water dynamics in the Paraná Basin, *Scientific Reports* (2026)](https://www.nature.com/articles/s41598-026-53807-7)
- [A Global Multi-Sensor Dataset of Surface Water Indices from Landsat-8 and Sentinel-2, *Scientific Data* (2025)](https://www.nature.com/articles/s41597-025-05562-z)
