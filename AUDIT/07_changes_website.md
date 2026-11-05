# Stage 7 — Batch (b): website and user experience

Dated 2026-11-05. Implements batch (b) of
[`AUDIT/05_defect_register.md`](05_defect_register.md), after the pipeline
work recorded in [`AUDIT/06_changes_pipeline.md`](06_changes_pipeline.md).

All 614 tests pass. No new dependency. The live site was inspected first,
in a fresh browser session, and every fix was re-verified against three
data states (§4).

---

## 1. Commits

| commit | what changed |
|---|---|
| `b7689f5` | store migration: the `unit` column on a database that predates it |
| `7f660c6` | label every measurement with its window, age and status; break the chart at gaps; separate "withheld" from "failed to load"; titles follow the language |
| `5a68517` | correct the map and the stretch page; raise the disclaimers |
| `1cb46f2` | make the method page describe the method |
| `343fb37` | chart description matches the chart; label the empty column header |

## 2. Files changed

| file | what changed |
|---|---|
| `site/index.html` | headline window + age + validation status; unit renamed; gap breaks and gap markers in the chart; `riverUnavailable` in its own box; `sumWarning`; anchor wording; skill caveats; benchmark citation; "missing quarter" → measured recall; urban bullet added; `limitGorge` → `limitDelta`; loop caption says composites; accessible chart description; disclaimer raised; title follows the language |
| `site/gauges.html` | the satellite evidence line: past tense, window, age, the anchor's span, and the cadence caveat; title suffix |
| `site/flood.html` | window + age + "Window measured"; unit renamed; score caveat; disclaimer raised; title follows the language |
| `site/map.html` | the gorge/Delta claims corrected in both the radar note and the provenance block; frame total declares the overlap; title follows the language |
| `site/method.html` | "three times a day" scoped; benchmark cited; two paragraphs on what the score does not cover; the filters paragraph rewritten now that both filters exist; Delta correlation; empty column header labelled |
| `site/wave.html`, `site/drivers.html` | title follows the language; `drivers` already had the strong disclaimer |
| `src/parana_crecida/store.py`, `tests/test_store.py` | the migration in `b7689f5` |

## 3. What was fixed, against the register

**D11 — the satellite figure was undated and in the present tense.** The
worst reader-facing defect, and confirmed live before touching anything.
`gauges.html` read:

> Radar measures 9,728 km² of water over 1,477 km of river, 1.9 times the
> driest pass

— on a page that labels its gauge readings three ways in the same block
("Measured to 3 Nov", "Data to 3 November 2026", "Generated 4 Nov, 23:26"),
for a figure that was eight days old and can reach a fortnight. It now
reads:

> Radar measured 9,728 km² of open water over 1,477 km of river in the
> composite from 17 Oct to 28 Oct, 8 days ago. 1.9 times the lowest passes
> in the one-year record (26 Mar – 19 Apr). The satellite measurement is
> updated when a valid observation becomes available, not daily.

**Every measurement now carries its window, unit, reach, status and age.**
"Last pass" was never a pass — the date is a grid boundary — so it is
"Window measured" with both ends, on `index.html` and `flood.html` alike.
The units become "open water detected by radar" and "open water beyond the
permanent channel", which is what the pipeline measures; "km² of the Paraná
valley under water" implied flood extent. The flood figure states that it
has not been validated against optical water and the total water has.

**Missing data no longer looks like zero water.** A cycle that could not be
measured is absent from the series, and the chart drew a straight line
across it — a measurement nobody made, rendered as though they had. The
line now breaks and the break is labelled "cycle not measured". The
accessible description carries the count, because a break is invisible to
someone reading the description rather than the picture.

**Withheld is no longer indistinguishable from broken.** The "no
whole-river figure" notice was going into the box with `role="alert"` —
which a screen reader announces as urgent, and which is also where a failed
fetch lands. It has its own box now.

**Misleading totals are declared.** While the published data is still
summed from overlapping corridors, `index.html` says so and quantifies it
(~7% too much), and the map's frame total says the same. Both disappear
once the union measurement lands, keyed off the shape of the data rather
than a hand-edited flag.

**No published figure rests on one cycle.** The chart annotated the raw
minimum while the headline quoted the three-cycle median anchor, putting
two different "lowest" figures on one page. Both use the anchor now, and
the wording adapts to whether the anchor is one cycle or several.

**Inconsistent descriptions of the algorithm.** `map.html` claimed the
radar layer "reaches the Delta ... but for the Delta and the gorge above
Posadas only the picture is shown: no area is published there". Both halves
were wrong: the gorge has published since `73a539c` at IoU 0.83 and 0.90,
and the layer draws only the reaches that publish, so it does not reach the
Delta at all. `method.html`'s "It updates itself, three times a day" is
true of the river heights and false of the satellite product. Its claim
that the coverage and plausibility filters are "not needed now" is replaced
by a description of the two filters, which exist again.

**Measured, calculated, interpolated and unavailable.** The provenance
blocks were already a strength and are kept; what changed is that the
claims inside them are now true (the Delta's reason, the gorge's status).
`map.html` already distinguished interpolated values explicitly ("Between
gauges the value is interpolated") and still does.

**The uncited benchmark.** "Published flood-mapping work reaches 0.7–0.9"
appeared on two pages with no source. Replaced on both by the Sen1Floods11
figures — around 0.55 IoU for threshold methods, around 0.73 for trained
networks — against which 0.69 from one global threshold is a reasonable
result rather than a failure.

**What the score does not cover** is now stated where the score is shown,
on `index.html`, `flood.html` and `method.html`: the threshold was tuned
against the same date (and three of the same reaches) it is scored on; what
is scored is total water, of which the permanent channel is 56–93%; the
water beyond the channel was never scored; and the optical reference is a
six-day-either-side median covering 16–98% of a corridor, blind under
vegetation exactly where the radar is.

**"The missing quarter of the water"** is replaced by the measured 80%
recall and the honest statement that the canopy share is unmeasured. A
bullet for urban and smooth dry surfaces is added, because that failure is
measured, not hypothetical.

**Not an official alert system.** `index.html` and `flood.html` carried the
weaker "This is not an official forecast". Every page now uses the wording
`gauges.html`, `map.html` and `drivers.html` already had: not an alert
system, does not warn, does not forecast, not an official risk map, with
INA named as the source of alerts, evacuations and official forecasts, and
linked. `wave.html`'s own notice was already explicit and is untouched.

**Language and accessibility.** Five of the seven pages never updated
`document.title` on a language switch, so an English page served a Spanish
tab title under `<html lang="en">`. All seven do now, with a consistent
suffix. An empty `<th>` on the travel-time table left a column unlabelled.
Spanish and English were kept in step throughout; a sweep found no element
left untranslated in either language on any page.

## 4. What was verified in the browser

Inspected first on the live site, `https://ammarmahfoud90.github.io/parana-crecida/`,
then re-verified locally against **three data states**, because the fixes
have to hold for the data published today *and* for what the stage-6
pipeline will produce next:

- **`oldsite`** — exactly what is published now (schema 2, summed river
  series, single-cycle anchor). Confirms no regression for the live site.
- **`newsite`** — the real output of `crecida export` after stage 6: no
  whole-river total, `river_unavailable` set.
- **`unionsite`** — a union-measured series with three injected gaps and a
  three-cycle anchor, to exercise what the next Earth Engine run produces.

Verified:

| check | result |
|---|---|
| Console errors, all seven pages, both languages | none |
| Local links and assets — every `href`/`src` on all seven pages | all 200 |
| `document.title` and `<html lang>` after a language switch | match, all seven pages |
| Elements left blank after a language switch | none, either language |
| Chart gap rendering | both paths break into 3 subpaths, 2 gap markers, description names the count |
| Chart "lowest" annotation vs the headline | now agree (5,165 km²) |
| `oldsite` headline | "the lowest pass ... (5,204 km², 30 June 2026)", with the overlap warning shown |
| `unionsite` headline | "the lowest passes ... (median 5,165 km², over 26 Mar – 19 Apr)", overlap warning hidden |
| `newsite` headline | withheld, with the reason, in its own box; chart says there is no series |
| Map layer switch | slider resets to 15 frames; `aria-valuetext` becomes the window "17 Oct – 28 Oct" |
| Map controls | `aria-label="Date"` on the slider, `aria-pressed` on every toggle, `aria-label` on play |
| Mobile at 375×812, `index` and `gauges` | no horizontal overflow (`scrollWidth == innerWidth`) |
| `gauges.html` satellite line | past tense, window, age, anchor span, cadence caveat |

## 5. What remains

**Needs data that does not exist yet.**

- **D22, the per-reach observed fraction beside each score.** The `seen`
  share — 16% for `corrientes_goya`, 98% for `la_paz_parana` — is the
  single most useful number missing from the validation table, and it is in
  `data/mask_skill.json` already. But the matching *coverage per cycle*
  arrives only with the next Earth Engine run (stage 6 added
  `observation[].coverage`), so the table would show one and not the other.
  Better done in one pass once the series is rebuilt.
- **D35, area bias and an interval beside every area.** Neither exists.
  They need the held-out validation of `AUDIT/03`, not a page change.

**Deliberately left.**

- The low-water comparison still rests on a single cycle *in the data
  published today*, because that is the data. The page now says which it
  is, and the pipeline rejects the bad cycle as soon as it runs.
- `site/data/flooded.json` is still served. Stage 6 stamps it as superseded
  on the next export rather than deleting it, because its per-stretch stage
  correlations are the evidence behind the reaches that publish no figure.
  No page fetches it.

**Carried over from stage 6, unchanged by this stage.** The headline
figures will not be correct until `crecida floodwater` runs with an Earth
Engine credential and rebuilds the series over the dissolved corridor
union. Until then the site publishes the summed total *with a warning
saying it is about 7% too high* — which is the best available answer, but
it is not the right one.
