# Stage 2 — Independent scientific review of the Sentinel-1 processing

Read-only. No code was changed. Dated 2026-11-05. Builds on
[`AUDIT/01_inspection.md`](01_inspection.md), which established the production
pipeline; this stage asks whether that pipeline is scientifically sound.

**Method.** The code was read against current ESA, Copernicus and Earth Engine
documentation and against peer-reviewed SAR flood-mapping literature. No Earth
Engine credential was used, so no backscatter, mask or area was recomputed.
Where a finding is quantitative it is derived from the **published data files**
in `origin/main` — which is enough to settle some questions outright, including
the most serious one.

**Severity scale.**

| | meaning |
|---|---|
| **Critical** | a currently published number is demonstrably wrong or cannot be supported |
| **High** | a core methodological assumption is unsupported, and it plausibly biases published numbers |
| **Medium** | a real error source, bounded or partly mitigated, but undocumented |
| **Low** | technical imprecision or stale documentation |
| **Supported** | the choice is defensible and the reasoning is sound — recorded so a later stage does not "fix" it |

**Headline conclusion.** The mask is a competent C-band threshold product and
the project's instinct to validate it against optical water was the right one.
But the single number the site leads with rests on a comparison against one
satellite pass that the published data itself shows to be corrupt, and the
polarisation choice is, for the half of the river where the product performs
worst, the opposite of what the regional SAR literature recommends. Neither is
visible from the IoU, because the validation design cannot see either.

---

## S1 — **Critical.** The "driest pass on the record" is a failed measurement, and two headline claims rest on it

`site/index.html` leads with 9,728 km² and then makes two comparative claims,
both anchored on `lowest` — the cycle ending **2026-06-30**:

- `riseLabel` → **+87%** (`index.html:552`)
- `floodNote` → **"5.9 times as much"** flood water (`index.html:543`)

`lowest` is selected as the complete cycle with the smallest `water_km2`
(`export.py:839`).

### The evidence

`water_km2 − flood_km2` is water detected **inside the permanent-channel mask**.
That mask is a fixed geometric layer (`floodwater.permanent_water()`), and the
Paraná's channel is wet in every cycle, so this quantity should be close to
constant. In the published series it is:

| | in-channel water, km² |
|---|---|
| mean of the 30 other cycles | **5,219.4** |
| standard deviation of those 30 | **76.4** (1.5%) |
| cycle 2026-06-30 | **4,469.9** |
| deficit | **−750 km²**, **z = −9.8** |

Thirty of thirty-one cycles sit within ±2.4% of the median. One sits 14.1%
below it. Localised per stretch against each stretch's own median:

| stretch | in-channel 2026-06-30 | its own median | change |
|---|---|---|---|
| puerto_iguazu_libertador | 198.0 | 201.4 | −1.7% |
| libertador_posadas | 337.6 | 334.1 | +1.1% |
| **posadas_ita_ibate** | **1,283.1** | **1,717.5** | **−25.3%** |
| **ita_ibate_corrientes** | **300.7** | **609.8** | **−50.7%** |
| corrientes_goya | 622.9 | 619.7 | +0.5% |
| goya_la_paz | 610.2 | 616.0 | −0.9% |
| la_paz_parana | 508.2 | 507.0 | +0.2% |
| parana_villa_constitucion | 609.2 | 611.8 | −0.4% |

Two **adjacent** corridors lose half and a quarter of their permanent channel
in a single 12-day window while the other six move by less than 2%. The river
did not leave a 20 km-wide corridor and come back twelve days later. This is
the signature of **ground not observed**, or of a mask that failed over part of
the swath — and because unobserved pixels are masked, `.selfMask()` and `sum`
score them as **zero water** (`floodwater.py:181-193`), exactly the failure
mode the superseded module documents in its own comments:

> "A pass that crosses only part of the corridor leaves the rest with no data,
> and unobserved ground reduces to zero water -- which reads as the river
> shrinking." — `src/parana_crecida/flooded.py:245-247`

The live pipeline has **no coverage check** and **no channel-plausibility
check** (Stage 1 findings C7 and C14); `floodwater.rebuild()` does not even
record an image count per cycle, unlike `flooded.py`, which stored `images`
(`flooded.py:406`). So the published data cannot be interrogated for this
without re-querying Earth Engine.

### Effect on what is published

| anchor cycle | water km² | published rise | flood multiple |
|---|---|---|---|
| **2026-06-30** (used) | 5,203.6 | **+87%** | **5.9×** |
| 2026-03-26 (next lowest) | 5,509.9 | +77% | 11.2× |
| 2026-04-07 | 5,553.6 | +75% | 9.9× |

The two claims are wrong in **opposite** directions: the rise is overstated
(the anchor's total is depressed by the 750 km² of missing channel), and the
flood multiple is understated (that same cycle's `flood_km2` of 733.7 is not
the record minimum — 2026-03-26 is, at 383.6 km², which would give 11.2×).
A reader cannot tell, because `min`/`max` over a series with one bad row is not
a robust statistic and nothing flags the row.

### A date coincidence worth testing, not yet a cause

The window is **19–30 June 2026**. In that window the Sentinel-1 constellation
was being reconfigured: Sentinel-1C operations were suspended from 9 to 23 June
2026 for repositioning, the nominal six-day repeat was re-established on
**24 June 2026**, and **Sentinel-1A operations were terminated on 29 June 2026**
([Copernicus Data Space, 28 May 2026](https://dataspace.copernicus.eu/news/2026-5-28-sentinel-1-orbital-reconfiguration-dates);
[Copernicus Data Space, 24 June 2026](https://dataspace.copernicus.eu/news/2026-6-24-s1cd-final-orbital-configuration-achieved)).
The pipeline applies no `platform_number` filter and no relative-orbit filter,
so whatever the constellation was doing flows straight into the mosaic.

I state this as a hypothesis. **Proven** from published data: the 2026-06-30
cycle is a 9.8-sigma outlier in channel detection, localised to two adjacent
corridors, and the site's two comparative claims are anchored on it.
**Not proven**: that missing acquisitions are the cause. One Earth Engine query
— count IW VH scenes intersecting those two corridors in 19–30 June 2026 and
reduce the mosaic's `mask()` over each — would settle it.

---

## S2 — **High.** VH below −25 dB sits at or under the instrument noise floor, and there is no lower bound

ESA's requirement for Sentinel-1 is a Noise Equivalent Sigma Zero (NESZ) of
**−22 dB or better**; measured IW performance is reported in the range −23 to
−30 dB across incidence angles, with the worst values at near and far range,
and the Mission Performance Centre notes that **cross-polarised (VH/HV)
channels are the more noise-affected** because the returned signal is weaker
([ESA Charter Mapper — Sentinel-1](https://docs.disasterscharter.org/missions/sar/sentinel-1/);
[eoPortal — Copernicus Sentinel-1](https://www.eoportal.org/satellite-missions/copernicus-sentinel-1);
[S-1 MPC, Thermal Denoising of Products Generated by the Sentinel-1 IPF](https://sentinels.copernicus.eu/documents/247904/2142675/Thermal-Denoising-of-Products-Generated-by-Sentinel-1-IPF.pdf)).

The Sentinel-1 Global Backscatter Model team report the Bayesian-optimal water
threshold as **−22.9 dB in VH**, and note explicitly that thermal noise becomes
observable when backscatter falls below about **−22 dB**
([Wagner et al., *Scientific Data* 8:277, 2021](https://www.nature.com/articles/s41597-021-01059-7)).
Published fixed-threshold practice clusters above the project's value: −19.5 to
−22.3 dB from scene histograms in a Ganges-basin study
([Springer, 2025](https://link.springer.com/article/10.1007/s44397-025-00012-2)),
and −23.6 dB as the best fixed VH threshold reported on the Sen1Floods11
benchmark.

`floodwater.VH_THRESHOLD_DB = -25.0` (`floodwater.py:100`) is therefore **2 to 3
dB below the published optimum and at or below the noise floor over part of the
IW swath**. Two consequences:

1. Pixels classified as water in the far-range and near-range parts of the
   swath may be noise-dominated rather than dark-target returns, and the
   classification will not be spatially stationary across the corridor.
2. **There is no lower bound on the water class.** `vh.lt(-25)` admits
   everything down to the band's floor. Earth Engine's own catalog page for
   this collection demonstrates the opposite practice — its worked example
   masks low-backscatter edge pixels with `image.lt(-30.0)` before use
   ([Earth Engine Data Catalog — COPERNICUS/S1_GRD](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD)).
   Residual GRD border noise and denoising artefacts, which land in exactly
   that range, are counted as water here.

**Supported part of the reasoning.** The project's stated reason for preferring
VH over VV — that wind roughening lifts VV backscatter toward the land value
much more than it lifts VH — is correct and well attested; see S5.

**Needs improvement.** The threshold value itself, the absence of a lower
bound, and the absence of any per-swath or per-orbit treatment.

**Cannot be verified here.** Whether the specific VH scenes over these
corridors actually approach NESZ requires reading the per-product noise vectors
or inspecting the backscatter histograms, neither of which is possible without
a credential. Stage 1 already recorded that −25 dB was never in the committed
parameter sweep, which stops at −24 dB (`scripts/tune_mask.py:97`).

---

## S3 — **High.** VH is the wrong polarisation for flooded vegetation, which is this product's dominant error — and for the reaches where it performs worst

The project states that water under vegetation is its main miss and that every
figure is a floor (`floodwater.py:68-72`, `method.html:374`,
`index.html:292`). That is the correct physical account. The problem is that
the polarisation chosen makes it worse, and the reaches where it matters are
precisely the ones scoring 0.48–0.67.

Flooded vegetation does not read as dark water at all: the water surface and
the standing stems form a **double-bounce** path that *raises* backscatter, so
no low threshold in any polarisation will find it. Detecting it requires either
the backscatter *increase* against a dry reference, longer wavelength, or
polarimetric/coherence information. And the double-bounce enhancement is
**weaker in cross-polarisation than in co-polarisation**:

- "In most cases, cross-polarized (HV or VH) signals have a lower double-bounce
  effect than co-polarized ones (HH and VV) on vegetated areas"
  ([*Water* 12(10):2745, 2020](https://www.mdpi.com/2073-4441/12/10/2745)).
- A Saale River (Germany) multi-temporal comparison found VV consistently
  detected a larger flood extent than VH — 152 km² against 67 km² at maximum
  inundation
  ([ResearchGate record](https://www.researchgate.net/publication/279283735_Backscatter_Analysis_Using_Multi-Temporal_and_Multi-Frequency_SAR_Data_in_the_Context_of_Flood_Mapping_at_River_Saale_Germany)).
- Flooding beneath dense canopy is not retrievable from C-band SAR and optical
  features alone
  ([*Remote Sensing* 12(23):3892, 2020](https://www.researchgate.net/publication/345944723_Flood_Mapping_in_Vegetated_Areas_Using_an_Unsupervised_Clustering_Approach_on_Sentinel-1_and_-2_Imagery)).

**The regional literature is explicit, and points the other way on all three
instrument choices.** The one dedicated SAR evaluation of the Paraná Delta
wetlands concludes that **L-band, HH polarisation and steeper incidence angles**
perform best for tracking hydrological dynamics there, attributing it to
penetration through *cortadera* stands
([*Revista de Teledetección*](https://polipapers.upv.es/index.php/raet/article/view/25725)).
C-band polarimetric work on the lower Paraná floodplain likewise relies on
contrasting incidence angles to separate plant functional types, and notes that
optical vegetation maps overestimate forest at the expense of herbaceous
wetland
([*Remote Sensing* 8(3):174, 2016](https://www.mdpi.com/2072-4292/8/3/174)).

This project uses **C-band, VH, with no incidence-angle control**. That is the
opposite of each recommendation, in the reach the recommendations were derived
from. The project's own per-stretch scores are consistent with it: 0.83–0.90
above Posadas, where the channel is confined and vegetation is not the problem,
and 0.48–0.67 below Corrientes, where the floodplain is wide and vegetated.
`floodwater.py:66-67` attributes that split to canopy; the literature says the
band and polarisation choice compound it.

**Supported.** The physical explanation of the limitation, and publishing the
figures as a floor.

**Needs improvement.** The polarisation choice is justified in the code solely
on wind sensitivity (`floodwater.py:49-53`) with no accounting of the
flooded-vegetation cost. Both effects are real and they pull in opposite
directions; only one was weighed. Using both VV and VH — the collection carries
them in the same product — would let each be used for what it is good at, and
the collection is already filtered to dual-pol scenes
(`floodwater.py:121-130`).

**Cannot be verified here.** How much water the mask misses under canopy. See
S4: the validation reference is structurally incapable of measuring it.

---

## S4 — **High.** The validation reference shares the radar's main blind spot, so the IoU cannot bound the dominant error

`scripts/validate_mask.py` scores the mask against Sentinel-2 MNDWI > 0
(`validate_mask.py:50,92`). Optical reflectance cannot see water under a
canopy either. A SWOT assessment over this same basin found that SWOT detects
floodplain inundation that multispectral products miss **specifically under
vegetation**
([*Scientific Reports*, 2026](https://www.nature.com/articles/s41598-026-53807-7)),
and the Paraná RADARSAT-2 work notes optical maps misallocate herbaceous
wetland
([*Remote Sensing* 8(3):174](https://www.mdpi.com/2072-4292/8/3/174)).

So recall of 0.80 is recall **against a reference that is blind in the same
place the radar is blind**. The published IoU therefore measures agreement over
open water and says nothing about the floor's depth. The site's statement that
the figures are a floor is honest; the claim that 0.69 characterises the
instrument is not, because the error the project itself calls dominant is
outside the measurement's reach by construction.

Two further reference-side issues, both compounding:

- **The "truth" is a 13-day median composite, described as a day.**
  `optical_water()` takes every qualifying scene in ±6 days and reduces B3 and
  B11 with `.median()` (`validate_mask.py:69-90`). `validate_mask.py:8-11`,
  `index.html:294` and `method.html:370` all describe this as water seen
  "directly" on a date. Agreement between two multi-day composites of moving
  water is not agreement on a date, and the sign of the bias is not
  established anywhere in the repository.
- **MNDWI > 0 carries no error budget.** Zero is a defensible convention —
  Otsu-optimal MNDWI thresholds on Sentinel-2 fall close to it, and a global
  multi-sensor assessment found MNDWI metrics stay above 80% across thresholds
  from −0.20 to +0.20
  ([*Scientific Data*, 2025](https://www.nature.com/articles/s41597-025-05562-z);
  [*Remote Sensing* 14(22):5721](https://www.mdpi.com/2072-4292/14/22/5721))
  — but those assessments are for permanent water, not for turbid flood water
  over a sediment-laden river. I found no study quantifying MNDWI > 0 error over
  turbid flood water; the project treats it as error-free.

**Supported.** Scoring against optical water at all, rather than against the
channel at low flow, was the right correction and is the single best decision
in the project's history.

**Needs improvement.** The reference's independence is overstated, and no
uncertainty is attached to it or propagated to the IoU.

**Cannot be verified here.** The magnitude of the under-canopy miss. It needs a
different sensor (L-band ALOS-2/NISAR, or SWOT) or interferometric coherence,
not a better threshold.

---

## S5 — **Supported, with a caveat.** The wind argument for VH is correct; the claim that it removes the need for a filter is not established

The physics is right and attested: roughened water raises VV backscatter
markedly more than VH, so VH is the less wind-sensitive channel for water
detection. Practice in the literature is nonetheless to **screen** scenes
rather than to rely on the band: the Bragg-scattering threshold for C-band is
about 12 km/h (3.3 m s⁻¹) at 10 m height, and studies exclude scenes above it
([*Remote Sensing* 12(10):1614, 2020](https://www.mdpi.com/2072-4292/12/10/1614);
[Ribe, Denmark case study, 2025](https://www.sciencedirect.com/science/article/pii/S1569843225004637)).
Wind and rain roughening is a documented source of **omission** error in
heterogeneous flood areas
([Operational Flood Detection Using Sentinel-1 SAR Data over Large Areas](https://www.researchgate.net/publication/332441802_Operational_Flood_Detection_Using_Sentinel-1_SAR_Data_over_Large_Areas)).

`site/method.html:372` states that the old 70% plausibility filter is "not
needed now" because "VH is far less sensitive to wind than VV". Less sensitive
is not insensitive, no wind screen replaced it, and Stage 1 established that
the filter it refers to was dead code even in the superseded pipeline
(`flooded.MIN_PLAUSIBLE_FRACTION`, never reached from `build()`). So the
sentence overstates both what was removed and what replaced it. S1 is what the
removal costs: a filter of that shape, had it been live, would have caught the
2026-06-30 cycle.

---

## S6 — **High.** Multi-orbit, multi-geometry data are mosaicked and compared with a single absolute threshold

Over water, backscatter **falls** with increasing incidence angle; over land it
is comparatively flat. A fixed water/land threshold is therefore
geometry-dependent, and the standard mitigation is to group acquisitions by
relative orbit — which fixes the local incidence angle — or to normalise to a
reference angle. The S1GBM normalises to 38° and reports differences of **up to
5 dB** when un-normalised backscatter is aggregated over time
([*Scientific Data* 8:277, 2021](https://www.nature.com/articles/s41597-021-01059-7);
[*Land* 10(7):680, 2021](https://doi.org/10.3390/land10070680)).

Five decibels is roughly twice the entire land–water margin the project is
working with (`flooded.py:19-22` records VV medians 4.7 dB apart; the VH
separation is of the same order).

What the code does (`floodwater.py:121-146`):

- filters on `instrumentMode` and polarisation — **no** `orbitProperties_pass`,
  **no** `relativeOrbitNumber_start`, **no** `platform_number`;
- `.mosaic()`s whatever is left, which composites by taking the last image in
  collection order per pixel, so adjacent parts of one corridor can come from
  different orbits, different look directions and different platforms, with a
  hard seam between them;
- applies one threshold across all of it;
- never reads the `angle` band, which the collection carries (20 km nominal,
  "approximate incidence angle from the ellipsoid"), nor
  `relativeOrbitNumber_start`, which the collection also carries
  ([Earth Engine Data Catalog — COPERNICUS/S1_GRD](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD)).

Earth Engine's own guidance for this collection is that "the data is
heterogeneous across instrument configurations and both ascending and
descending orbits, so filtering to a homogeneous subset is usually necessary
before processing"
([Earth Engine — Sentinel-1 Algorithms](https://developers.google.com/earth-engine/guides/sentinel1)).
That filtering is not done.

Note that this interacts with the project's own history. The per-stretch VV
thresholds it abandoned — "nine different numbers", read as a defect
(`floodwater.py:12-15`) — were partly absorbing exactly this geometry variation.
Replacing them with one number did not remove the variation; it removed the
compensation for it. The gorge reaches "wanting thresholds 6.5 dB apart"
(`floodwater.py:27-29`) is close to the 5 dB the literature attributes to
un-normalised geometry, which suggests the old calibration was reading a real
effect and misattributing it to terrain.

**Needs improvement.** Filter to one pass direction, and ideally to a fixed
relative orbit per corridor, or normalise with the `angle` band. The superseded
module's own comment records that "the two relative orbits that cover this
corridor pass on different days" (`flooded.py:274-277`), so the project knows
there are two.

**Cannot be verified here.** How much of the published variance is geometry.
Measurable without new data: group the existing cycles by relative orbit and
compare the in-channel detected area, which S1 shows is otherwise stable to
1.5%.

---

## S7 — **Supported.** Earth Engine has already done the radiometric work, and the project correctly does not redo it

The project applies no calibration, no thermal-noise removal and no
orthorectification of its own, and that is right: `COPERNICUS/S1_GRD` arrives
with orbit file applied, GRD border-noise removal (from 12 January 2018),
thermal-noise removal, radiometric calibration to σ°, and Range-Doppler terrain
correction against SRTM 30 m, converted to dB as 10·log₁₀(σ°)
([Earth Engine — Sentinel-1 Algorithms](https://developers.google.com/earth-engine/guides/sentinel1)).
Re-applying any of it would be an error. This is the "do not apply
preprocessing mechanically" test and the project passes it.

Two genuine gaps follow from what Earth Engine does **not** do, and neither is
acknowledged anywhere in the repository:

- **No radiometric terrain flattening.** Earth Engine states plainly:
  "Radiometric Terrain Flattening is not being applied due to artifacts on
  mountain slopes." The product is **σ°, not γ°**. Residual local-incidence-angle
  dependence therefore remains, and it remains largest exactly in the gorge
  above Posadas — the two reaches the project now publishes as its best, at IoU
  0.83 and 0.90. Those two scores were measured over the only dates available,
  on terrain where σ° is least comparable between looks. Residual angle
  dependence persists even after a σ°→γ° conversion
  ([*Land* 10(7):680](https://doi.org/10.3390/land10070680)), so flattening
  alone would not fully fix it either.
- **Terrain correction is against SRTM 30 m, while the HAND mask comes from
  MERIT (≈90 m).** Two different elevation models are in play in one mask, at
  different resolutions — see S8.

**Low-severity note, recorded as correct.** `focal_median` and the `.median()`
dry references are rank-preserving, so taking them in dB space gives the same
answer as in power space. Had the code used `.mean()` in dB it would have been
wrong. It does not. (`COPERNICUS/S1_GRD_FLOAT` exists for power-domain work if
a later stage needs averaging.)

---

## S8 — **Medium.** The HAND mask is reasonable in intent, used past its resolution and accuracy, and never validated

`HAND_LIMIT_M = 10.0` against band `hnd` of `MERIT/Hydro/v1_0_1`
(`floodwater.py:79,106,146`).

**What is supported.** The reasoning is sound and it is the right kind of
constraint: ground well above its nearest drainage cannot hold standing water,
and radar shadow on a valley wall is exactly such ground read as dark. Using
HAND rather than slope is better physics for this purpose, and the project's
own evidence — the two gorge reaches going from unmappable to 0.83/0.90 —
supports it. Terrain shadow and layover are documented false-positive sources
that a terrain mask is the standard answer to
([UN-SPIDER, Recommended Practice: Radar-based Flood Mapping](https://un-spider.org/advisory-support/recommended-practices/recommended-practice-radar-based-flood-mapping)).

**What needs improvement.**

- **Resolution mismatch.** MERIT Hydro is a 3 arc-second grid, ≈90 m at the
  equator ([Earth Engine — MERIT Hydro](https://developers.google.com/earth-engine/datasets/catalog/MERIT_Hydro_v1_0_1);
  [Yamazaki et al., *WRR* 55, 2019](https://www.researchgate.net/publication/333438388_MERIT_Hydro_A_High-Resolution_Global_Hydrography_Map_Based_on_Latest_Topography_Dataset)).
  It is combined pixel-for-pixel with a 10 m radar band and reduced at 60 m, so
  the binary HAND edge is resampled (default nearest-neighbour) from a grid 1.5×
  coarser than the analysis scale and 9× coarser than the radar.
- **Accuracy against a 10 m cut.** MERIT Hydro derives from MERIT DEM, for
  which the headline result is that land mapped at **2 m or better vertical
  accuracy rose from 39% to 58%** — i.e. ~42% of land is worse than 2 m
  ([Yamazaki et al., *GRL* 44, 2017](http://dx.doi.org/10.1002/2017GL072874);
  [MERIT DEM](https://developers.google.com/earth-engine/datasets/catalog/MERIT_DEM_v1_0_3)).
  A hard 10 m threshold on a derived height with that error budget is a soft
  boundary presented as a crisp one.
- **The drainage definition is a global default the authors warn about.** HAND
  was computed with a globally uniform 0.5 km² upstream-area threshold, and
  Yamazaki et al. state: "Users are recommended to recalculate HAND using their
  own region-specific threshold if their application is sensitive to the
  thresholding value." On an anabranching floodplain tens of kilometres wide
  with thousands of secondary channels, the product is certainly sensitive to
  it. No recalculation was done.
- **HAND sees only the nearest flowpath**, which is a documented limitation on
  floodplains fed by several channels, and HAND-based mapping is reported to
  **under**predict inundated area
  ([Northeastern University evaluation of HAND](https://repository.library.northeastern.edu/files/neu:bz60w948d/fulltext.pdf)).
- **The cut-off was never swept in committed code.** `floodwater.py:102-106`
  asserts 10 m beat 15 m and 25 m; Stage 1 established no committed script
  sweeps HAND at all.

**What cannot be verified here.** Whether HAND ≤ 10 m clips real flood water on
the high floodplain terraces and levees below Corrientes. The project's own
reasoning implies HAND is nearly inert on the flat reaches and active only on
the valley walls — plausible, and it would explain why 10, 15 and 25 m gave
similar scores — but it is untested, and if true it means the parameter that
rescued the gorge does nothing for the reaches that score worst.

---

## S9 — **Medium.** Speckle filtering degrades the mask below its stated resolution, and nothing says so

`focal_median(50, "circle", "meters")` on a 10 m band is a circular kernel of
radius 5 pixels, ≈81 pixels, ≈110 m across. The effective resolution of the
water mask is therefore coarser than the 60 m grid the areas are reduced on, so
the 60 m scale is nominal rather than real, and shorelines move by more than
the "pixel or two" claimed at `flooded.py:70-71`. On a 20 km-wide corridor with
hundreds of kilometres of braided shoreline, a systematic boundary shift of
~50 m integrates into a substantial area term that is nowhere estimated.

A median is a defensible, robust choice and is rank-preserving in dB (S7), but
it is not a standard SAR speckle filter; Refined Lee and Lee-sigma are
adaptive, preserve edges better and are what the flood-mapping literature uses.
The relevant evidence that filtering matters is that on Sen1Floods11 a fixed dB
threshold scores 0.592 IoU with speckle filtering and 0.533 without — a 6-point
swing from the filter alone.

**Needs improvement.** State the effective resolution; estimate the shoreline
bias; consider an edge-preserving filter.

---

## S10 — **Medium.** Each cycle is a patchwork of dates, not a snapshot, and stage moves inside the window

`.mosaic()` composites by taking the last image in collection order for each
pixel. A cycle's mask is therefore a spatial patchwork of up to several
acquisition dates — and, per S6, of different orbits and look directions — not a
temporal aggregate. During the October rise the published series moves 6,982 →
9,728 km² in one cycle (+39%), so stage changes materially inside a window:
different parts of the same published figure correspond to different river
levels.

`method.html:373` discloses the right thing ("A composite is a window, not an
instant, and the date shown is the day that window closes"), which is good
practice. What is not disclosed is that it is a *mosaic* rather than an
aggregate, which is the part that makes the number spatially inhomogeneous in
time. A minimum or median composite over the window, or a per-date series with
coverage weighting, would at least be a defined statistic.

**Low-severity, related.** The window is 11 days, not 12 — Stage 1 finding C11,
`filterDate` being end-exclusive — so the cycle excludes the date it is
labelled with.

---

## S11 — **Medium.** Urban and smooth dry surfaces are a known false-positive class with no mitigation in the live pipeline

Smooth artificial surfaces return weakly and are a documented source of false
alarms: UN-SPIDER's recommended practice warns that "smooth objects like roads
and sand can also trigger false alarms"
([UN-SPIDER](https://un-spider.org/advisory-support/recommended-practices/recommended-practice-radar-based-flood-mapping)),
and operational global products build explicit exclusion masks for urban areas,
bare ground, arid surfaces and slopes above 10°
([Nature Communications, 2025](https://www.nature.com/articles/s41467-025-60973-1)).
In cities the opposite error also occurs: double-bounce off façades makes
genuinely flooded streets anomalously bright.

The project has measured this failure on its own data. `exposure.py:17-21`
records that on one pass "the urban surface fell below the water threshold and
a city of some half a million read as a lake" — 23,482 + 23,433 people across
the two reaches meeting at Posadas and Encarnación — and states explicitly:
"The old VV mask did this too, at Posadas, at 97,498 people. A different band,
the same city, the same failure."

So the live absolute-VH mask is **known** to misclassify Posadas, and the live
pipeline contains no settlement mask, no urban exclusion and no test for it.
Stage 1 finding C4 recorded the documentation side of this: `export.py:676-678`
credits the fix to change detection, a method that was rejected, so the only
documented safeguard applies to nothing that runs. Posadas sits inside
`libertador_posadas` (IoU 0.90) and `posadas_ita_ibate` (IoU 0.87), both of
which publish; the validation date did not happen to catch the failure.

**Needs improvement.** A settlement mask (GHSL built-up, or ESA WorldCover
class 50) is cheap and the exposure module already loads a population grid.

---

## S12 — **Medium.** Wet soil and agricultural land after rain

Moist and smooth bare soil attenuates the return and produces low backscatter
that is hard to separate from water; smooth asphalt, runways, mountain shadow
and certain moist bare soils are all documented look-alikes, and arid or bare
surfaces are the classic case
([*Remote Sensing* 18(17):2966](https://www.mdpi.com/2072-4292/18/17/2966);
[UN-SPIDER](https://un-spider.org/advisory-support/recommended-practices/recommended-practice-radar-based-flood-mapping)).
The Paraná corridors are intensively cropped and the floods coincide with the
wet season, so saturated fields are the expected false-positive population and
they are spatially correlated with genuine flooding, which is the worst case for
a single global threshold.

HAND ≤ 10 m does not screen this at all — wet fields on a floodplain are low
above drainage by definition. A −25 dB cut is restrictive enough that saturated
soil is more likely to be excluded than included, which is a point in its
favour, but no evidence either way exists in the repository, and the 2026-10-22
validation date is a single wet-season date, so the scoring cannot separate
flood water from saturated ground that happened to be dark.

**Cannot be verified here.** Needs the backscatter distribution of
agricultural land in these corridors across a dry/wet pair.

---

## S13 — **Medium.** Seasonal vegetation change is unaccounted for in a year-long series

C-band backscatter over cropland and herbaceous wetland varies seasonally with
biomass, structure and canopy moisture. With a fixed absolute threshold and no
change detection, any seasonal drift in land backscatter translates directly
into apparent area change. The published series spans a full annual cycle
(2025-11-02 to 2026-10-28), and the shape of its dry season — a slow decline
from November to a March–April minimum, then a rise — tracks the austral
growing season as closely as it tracks the hydrograph. Nothing in the project
separates the two.

This is the error that change detection is designed to remove, and change
detection was rejected on evidence that Stage 1 established is no longer
reproducible (`scripts/tune_mask.py:109` calls a function removed in
`73a539c`). The rejection may well have been correct — the recorded reason,
that flood-only areas exceeded the old method's total water, is a sound
falsification — but it was a rejection of one implementation, not of the idea,
and the seasonal-drift problem it would have addressed is left unmitigated and
unmeasured.

**Mitigating factor, in the project's favour.** S1 shows in-channel detected
water is stable to 1.5% across the whole year, which bounds seasonal drift
*inside the permanent channel* tightly. That is a real and reassuring result
the project could publish. It says nothing about drift over the floodplain,
which is where the flood signal lives.

---

## S14 — **Low/Medium.** "One Sentinel-1 repeat cycle" is no longer true of the constellation that produced this series

`floodwater.WINDOW_DAYS = 12` is documented as "One Sentinel-1 repeat cycle"
(`floodwater.py:109-110`), and `method.html:373` and `index.html:291` tell
readers that twelve days is how long the satellite takes to see the whole river.

Twelve days was the single-satellite repeat after Sentinel-1B failed
(23 December 2021, mission ended August 2022). It is not the repeat for most of
the published series: Sentinel-1C was fully operational from early May 2025,
Sentinel-1D data opened 17 April 2026, and the nominal **six-day** repeat was
re-established on 24 June 2026
([Copernicus Data Space, 24 June 2026](https://dataspace.copernicus.eu/news/2026-6-24-s1cd-final-orbital-configuration-achieved);
[Sentinel Online — Sentinel-1D user data opening](https://sentinels.copernicus.eu/-/sentinel-1d-user-data-opening-from-17-april-2026);
[Wikipedia — Sentinel-1](https://en.wikipedia.org/wiki/Sentinel-1), for the S1B
timeline). ESA also notes the new pair's ground-track pattern is **shifted by
one day** relative to the old A/B configuration.

Three consequences:

1. The stated justification for a 12-day window is out of date; the series
   could be sampled twice as often for most of its span.
2. The constellation **changed inside the series**, with no `platform_number`
   filter anywhere, so acquisition geometry over these corridors is not
   homogeneous across the 31 cycles. Compounds S6.
3. Cycles are not comparable in observation density: early cycles are one
   satellite, later ones two. With no coverage check (S1) that difference is
   invisible in the data.

---

## S15 — **Low.** The "0.7–0.9" benchmark the site measures itself against is not sourced, and flatters the comparison

`index.html:293` and `method.html:370` both state that published flood-mapping
work reaches 0.7–0.9 IoU, and conclude that the upper river sits inside that
band and the lower river does not. No citation is given anywhere in the
repository.

Benchmark values for comparable methods are lower. On Sen1Floods11, the
canonical Otsu-on-VH baseline scores around 0.55 IoU on the hand-labelled test
split, with reported values varying by evaluation setup; deep-learning models on
the same split reach about 0.73
([Bonafilia et al., CVPRW 2020](https://openaccess.thecvf.com/content_CVPRW_2020/papers/w11/Bonafilia_Sen1Floods11_A_Georeferenced_Dataset_to_Train_and_Test_Deep_Learning_CVPRW_2020_paper.pdf);
[arXiv 2302.08180](https://arxiv.org/pdf/2302.08180)).

Against that, a **0.69 mean IoU from a single global threshold is a good result**
— better than standard thresholding baselines and close to trained networks.
The project is being harder on itself than the literature warrants in one
respect, while quoting an unsourced band that makes its weakest reaches look
like failures and, more importantly, implies a like-for-like comparison with
numbers that come from a benchmark with hand-labelled truth rather than from a
single in-sample date. Neither the band nor the comparison should be published
without a citation and a statement of what was compared.

---

## S16 — **Supported.** Permanent-water separation is the strongest part of the design

`floodwater.permanent_water()` unions JRC GSW `occurrence > 90` with
`YearlyHistory` `waterClass == 3` for 2021 (`floodwater.py:148-175`). Verified
against the catalog: `waterClass` values are 0 no data, 1 not water, 2 seasonal,
3 permanent, at 30 m, with yearly coverage 1984–2021 — so `year == 2021` is the
last available year and it exists
([Earth Engine — JRC GSW1_4 YearlyHistory](https://developers.google.com/earth-engine/datasets/catalog/JRC_GSW1_4_YearlyHistory)).

The reasoning is correct and the diagnosis behind it is a genuine piece of
scientific work: `occurrence` averages 1984–2021, so a reservoir impounded
part-way through can never reach 90% however permanent it now is, and Yacyretá
(filled 2011) was being published as 353 km² of standing flood water in every
frame including the driest week of the year (`floodwater.py:150-160`). Adding
the 2021 bajante year as an alternative definition fixes it with a defensible
physical argument — water present in the lowest Paraná in 77 years is channel.

Two residual points, neither a defect in the logic:

- GSW `YearlyHistory` ends at 2021, so the union cannot represent any
  impoundment or channel change after that. It is a frozen reference against a
  moving river, and the series it is subtracted from runs to 2026.
- `flood_km2` inherits every error in `water_km2`; subtracting a fixed mask
  does not make the remainder more reliable. S1 is the clearest demonstration:
  on 2026-06-30 `flood_km2` *rose* while `water_km2` hit a record low, because
  the missing 750 km² came out of the channel term.

---

## S17 — **Low.** Georeferencing and raster alignment

Three grids are combined: S1 GRD orthorectified against SRTM 30 m, MERIT Hydro
HAND at ≈90 m, JRC GSW at 30 m, reduced at 60 m with `ee.Image.pixelArea()`.
Earth Engine reprojects on demand, so the alignment is handled and the area
computation is correct — `pixelArea()` gives true area per pixel, and
`reduceRegion(scale=60, maxPixels=1e10, bestEffort=True)` over a ≈9,500 km²
corridor needs ≈2.6 × 10⁶ pixels, three orders of magnitude inside `maxPixels`,
so `bestEffort` will not silently coarsen the scale. **This is fine and I
checked it specifically because it is a common way for Earth Engine area
figures to be wrong.**

What remains is the resampling of the coarse HAND edge (S8) and the
co-registration of S1 against the optical reference in validation. The latter is
not negligible: misalignment between radar and optical reference data is a
recognised error term in SAR–optical flood comparisons, and at 60 m scoring
cells a sub-cell offset moves the boundary pixels that dominate IoU over a long
braided shoreline. Nothing in the repository estimates it.

---

## What is scientifically supported, what needs improvement, what cannot be verified

**Supported.**
- Not re-applying Earth Engine's calibration, denoising or orthorectification (S7).
- Radar over optical for a cloudy flood season; the physical account of why water is dark.
- Permanent-water separation by the union of GSW occurrence and the 2021 bajante year (S16).
- Validating against optical water rather than against the channel at low flow (S4) — the right correction, imperfectly executed.
- VH being less wind-sensitive than VV (S5).
- Area computation, projection handling and `maxPixels`/`bestEffort` (S17).
- Median (not mean) filtering in dB space (S7).
- Publishing per-stretch scores rather than one river-wide figure, and withholding unscored reaches.

**Needs improvement.**
- S1: remove or flag the 2026-06-30 cycle; re-anchor the headline comparatives; add a coverage check and a channel-plausibility check; store an image count per cycle.
- S2: raise the threshold toward the published optimum, add a lower bound, treat the swath inhomogeneously.
- S3: use VV as well as VH; account for the flooded-vegetation cost of the polarisation choice; read the regional literature's L-band/HH/steep-angle conclusion.
- S4: state that the optical reference shares the radar's blind spot; stop describing a 13-day median composite as a day; attach an error budget to MNDWI > 0.
- S6: filter by pass direction and relative orbit, or normalise with the `angle` band.
- S8: recalculate HAND with a basin-specific drainage threshold, or state that it is inert on the floodplain.
- S9: state the mask's effective resolution; estimate the shoreline bias.
- S10: use a defined composite statistic rather than `mosaic()`; fix the 11-day window.
- S11: add a settlement mask; the Posadas failure is measured, not hypothetical.
- S14: correct the repeat-cycle statements; filter or at least record the platform.
- S15: cite the 0.7–0.9 band or drop it.

**Cannot be verified without an Earth Engine credential or new data.**
- Whether the 2026-06-30 anomaly is missing acquisitions or mask failure (S1) — one query settles it.
- Whether VH over these corridors approaches NESZ (S2).
- The magnitude of the under-canopy miss (S3, S4) — needs L-band, SWOT or coherence, not a better threshold.
- How much published variance is acquisition geometry (S6) — testable on existing cycles.
- Whether HAND ≤ 10 m clips real water on the floodplain (S8).
- The agricultural false-positive rate (S12) and the seasonal drift over the floodplain (S13).
- Radar–optical co-registration error in the validation (S17).

**A plausible-looking mask is not a validated flood measurement, and this one
is not yet validated.** It has been scored once, in-sample, on a single date,
against a reference blind to its dominant error, over a cloud-selected fraction
of each corridor (16% for `corrientes_goya`, per Stage 1 C2) — and the number
the site leads with is compared against a pass the data shows to have failed.
The measurement is real and the instrument is a reasonable one. The
*measurement of the measurement* is not yet strong enough to carry what is
published on it.

---

## Sources

- [Earth Engine — Sentinel-1 Algorithms](https://developers.google.com/earth-engine/guides/sentinel1)
- [Earth Engine Data Catalog — COPERNICUS/S1_GRD](https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S1_GRD)
- [Earth Engine Data Catalog — JRC/GSW1_4/YearlyHistory](https://developers.google.com/earth-engine/datasets/catalog/JRC_GSW1_4_YearlyHistory)
- [Earth Engine Data Catalog — MERIT Hydro v1.0.1](https://developers.google.com/earth-engine/datasets/catalog/MERIT_Hydro_v1_0_1)
- [Earth Engine Data Catalog — MERIT DEM v1.0.3](https://developers.google.com/earth-engine/datasets/catalog/MERIT_DEM_v1_0_3)
- [ESA Charter Mapper — Sentinel-1 mission characteristics](https://docs.disasterscharter.org/missions/sar/sentinel-1/)
- [eoPortal — Copernicus Sentinel-1](https://www.eoportal.org/satellite-missions/copernicus-sentinel-1)
- [S-1 Mission Performance Centre — Thermal Denoising of Products Generated by the Sentinel-1 IPF](https://sentinels.copernicus.eu/documents/247904/2142675/Thermal-Denoising-of-Products-Generated-by-Sentinel-1-IPF.pdf)
- [Copernicus Data Space — Sentinel-1 orbital reconfiguration dates (28 May 2026)](https://dataspace.copernicus.eu/news/2026-5-28-sentinel-1-orbital-reconfiguration-dates)
- [Copernicus Data Space — S1C/D final orbital configuration achieved (24 June 2026)](https://dataspace.copernicus.eu/news/2026-6-24-s1cd-final-orbital-configuration-achieved)
- [Sentinel Online — Sentinel-1D user data opening from 17 April 2026](https://sentinels.copernicus.eu/-/sentinel-1d-user-data-opening-from-17-april-2026)
- [Wikipedia — Sentinel-1 (S1B anomaly and mission end dates)](https://en.wikipedia.org/wiki/Sentinel-1)
- [Wagner et al. — The normalised Sentinel-1 Global Backscatter Model, *Scientific Data* 8:277 (2021)](https://www.nature.com/articles/s41597-021-01059-7)
- [Yamazaki et al. — MERIT DEM, *Geophysical Research Letters* 44 (2017), doi:10.1002/2017GL072874](http://dx.doi.org/10.1002/2017GL072874)
- [Yamazaki et al. — MERIT Hydro, *Water Resources Research* 55 (2019)](https://www.researchgate.net/publication/333438388_MERIT_Hydro_A_High-Resolution_Global_Hydrography_Map_Based_on_Latest_Topography_Dataset)
- [An evaluation of the Height Above Nearest Drainage (HAND) method — Northeastern University](https://repository.library.northeastern.edu/files/neu:bz60w948d/fulltext.pdf)
- [Bonafilia et al. — Sen1Floods11, CVPRW 2020](https://openaccess.thecvf.com/content_CVPRW_2020/papers/w11/Bonafilia_Sen1Floods11_A_Georeferenced_Dataset_to_Train_and_Test_Deep_Learning_CVPRW_2020_paper.pdf)
- [Cross Modal Distillation for Flood Extent Mapping, arXiv:2302.08180](https://arxiv.org/pdf/2302.08180)
- [UN-SPIDER — Recommended Practice: Radar-based Flood Mapping](https://un-spider.org/advisory-support/recommended-practices/recommended-practice-radar-based-flood-mapping)
- [Mapping global floods with 10 years of satellite radar data, *Nature Communications* (2025)](https://www.nature.com/articles/s41467-025-60973-1)
- [Integrating C- and L-Band SAR Imagery for Detailed Flood Monitoring of Remote Vegetated Areas, *Water* 12(10):2745 (2020)](https://www.mdpi.com/2073-4441/12/10/2745)
- [Flood Mapping in Vegetated Areas Using an Unsupervised Clustering Approach on Sentinel-1 and -2 Imagery (2020)](https://www.researchgate.net/publication/345944723_Flood_Mapping_in_Vegetated_Areas_Using_an_Unsupervised_Clustering_Approach_on_Sentinel-1_and_-2_Imagery)
- [Backscatter Analysis Using Multi-Temporal and Multi-Frequency SAR Data in the Context of Flood Mapping at River Saale, Germany](https://www.researchgate.net/publication/279283735_Backscatter_Analysis_Using_Multi-Temporal_and_Multi-Frequency_SAR_Data_in_the_Context_of_Flood_Mapping_at_River_Saale_Germany)
- [Evaluation of SAR systems for hydrological monitoring of wetlands in the Paraná River delta, *Revista de Teledetección*](https://polipapers.upv.es/index.php/raet/article/view/25725)
- [Mapping Plant Functional Types in Floodplain Wetlands: C-Band Polarimetric SAR from RADARSAT-2, *Remote Sensing* 8(3):174 (2016)](https://www.mdpi.com/2072-4292/8/3/174)
- [Assessing SWOT capabilities for monitoring floodplain water dynamics in the Paraná Basin, *Scientific Reports* (2026)](https://www.nature.com/articles/s41598-026-53807-7)
- [Normalizing the Local Incidence Angle in Sentinel-1 Imagery, *Land* 10(7):680 (2021)](https://doi.org/10.3390/land10070680)
- [Sentinel-1-Imagery-Based High-Resolution Water Cover Detection on Wetlands, *Remote Sensing* 12(10):1614 (2020)](https://www.mdpi.com/2072-4292/12/10/1614)
- [Flood mapping using Sentinel-1 imagery with topographical and hydrological contextualization: Ribe, Denmark (2025)](https://www.sciencedirect.com/science/article/pii/S1569843225004637)
- [Operational Flood Detection Using Sentinel-1 SAR Data over Large Areas](https://www.researchgate.net/publication/332441802_Operational_Flood_Detection_Using_Sentinel-1_SAR_Data_over_Large_Areas)
- [Application of Sentinel-1 SAR data for flood monitoring in the lower Ganges Basin (2025)](https://link.springer.com/article/10.1007/s44397-025-00012-2)
- [Improving Backscatter-Based Surface Water Classification in Arid Environments Through Interferometric Coherence, *Remote Sensing* 18(17):2966](https://www.mdpi.com/2072-4292/18/17/2966)
- [Surface Water Mapping and Flood Monitoring in the Mekong Delta Using Sentinel-1 SAR Time Series and Otsu Threshold, *Remote Sensing* 14(22):5721](https://www.mdpi.com/2072-4292/14/22/5721)
- [A Global Multi-Sensor Dataset of Surface Water Indices from Landsat-8 and Sentinel-2, *Scientific Data* (2025)](https://www.nature.com/articles/s41597-025-05562-z)
