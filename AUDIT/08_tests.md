# Stage 8 — Batch (c): regression tests and validation gates

Dated 2026-11-05. Implements batch (c) of
[`AUDIT/05_defect_register.md`](05_defect_register.md), after the pipeline
work in [`06_changes_pipeline.md`](06_changes_pipeline.md) and the website
work in [`07_changes_website.md`](07_changes_website.md).

**The suite grew from 617 collected to 714, and from 614 passing to 706.**
No test was weakened or deleted to make it pass. Five tests found defects
that were then fixed; one test of my own was wrong and was corrected
rather than the code.

---

## 1. Commands run, and what they actually returned

```
$ python -m pytest -q
706 passed, 8 skipped in 12.86s

$ python -m pytest -q -rs
SKIPPED [1] tests/test_areastage.py:82: not exported yet
SKIPPED [4] tests/test_external.py: external; run with --run-external
SKIPPED [1] tests/test_published_outputs.py:230: too few whole-river cycles to judge
SKIPPED [1] tests/test_published_outputs.py:253: no whole-river series published
SKIPPED [1] tests/test_site.py:321: no error box on this page
706 passed, 8 skipped in 13.02s

$ python -m pytest --collect-only -q
714 tests collected in 2.38s

$ python -m pytest -q -rs -m needs_ee --run-external
SKIPPED [4] tests/test_external.py: --run-external given but no EE credential in the environment
4 skipped, 710 deselected in 2.30s
```

Supporting commands run during the work, with their real outcomes:

```
$ crecida fetch --cache-dir cache
47 updated, 0 unchanged, 0 failed

$ crecida load --cache-dir cache
47 gauges, 47 series, 956638 observation rows
newest reading: 2026-11-04

$ crecida export
WARNING  no whole-river total: ... Run `crecida floodwater` to measure it.
WARNING  left out of river_daily.csv: 26254 2026-08-22 = 78.91, outside the range its unit allows
WARNING  1 reading(s) left out as out of range
8 stretches of water surface -> site/data/floodwater.json
8450 rows -> site/data/csv/river_daily.csv
warnings: no_isolated_outliers, river_measured_over_the_union
```

## 2. The eight skips, each with its reason

| skip | why | blocked by |
|---|---|---|
| `test_external.py` × 4 | the only tests that need Earth Engine | a credential; see §4 |
| `test_published_outputs.py:230` (`permanent channel present`) | fewer than three whole-river cycles to take a median over | the union measurement, which needs Earth Engine |
| `test_published_outputs.py:253` (`total is not a sum`) | no whole-river series published at all | the same |
| `test_areastage.py:82` | the area-against-stage record is deliberately withdrawn | re-measurement on the live mask |
| `test_site.py:321` | that page has no error box, by design | nothing |

Nothing is skipped to avoid a failure. The two `test_published_outputs`
skips are the correct response to a payload that currently withholds its
total — they start running the moment the union measurement lands, which
is exactly when their subject exists.

## 3. What was added

### Credential-dependent tests, kept out of the automated suite

`tests/conftest.py` registers `needs_ee` and `needs_network`, skips both
unless `--run-external` is given, and skips `needs_ee` even then if no
credential is in the environment. A skipped marked test still appears in
the summary, so the command is discoverable rather than folklore.

`tests/test_external.py` holds the four measurements this audit could not
make, written out rather than described: how much water the overlapping
corridors actually double-count (U3), whether every published cycle was
observed in full (U2), whether the 2026-06-30 failure was missing
acquisitions or the mask (U1), and what the published 0.69 looks like
split into channel and beyond-channel (A1.1, A1.2). Two of them report
rather than assert, because there is no prior figure to assert against.

### Calibration and evaluation kept apart

`data/tuning_manifest.json` records what the mask's parameters were fitted
on — one date, three of nine reaches — that the published score is
therefore in-sample, what it does not cover, and that the chosen values
are not reproducible from committed code as it stood when they were set.

`tests/test_tuning_manifest.py` (12 tests) holds it true rather than
letting it rot. The manifest has to describe the parameters actually in
`floodwater.py`; its dates and reaches are read back out of
`tune_mask.py` and `validate_mask.py`, so a changed default fails until
the record is brought into line; and no held-out date may equal a tuning
date, fall within twelve days of one — near enough to share radar passes —
or name a reach the sweep used. The held-out lists are empty today, so
those last checks pass trivially; they start doing work the moment a real
evaluation is set up.

### The artefacts, not just the functions

`tests/test_published_outputs.py` (19 tests) reads the files the site
serves. Schemas and provenance metadata; every payload dated, with a
timezone where it is a timestamp; no NaN or Infinity anywhere; no
future-dated observation; cycle dates ordered, unique and on the grid;
parallel arrays aligned; no area that is a zero standing in for a gap; a
gap present with a reason and no number; flood never exceeding total
water; no reach reporting more water than its corridor holds; the
permanent channel present in every cycle; the total not being a sum; no
unvalidated reach in a total; a withheld reach carrying its reason and no
numbers; the superseded view not served as current; the CSVs judged per
row against their own unit; and no arrival date published.

### The gate, the paperwork, and the pages

`tests/test_gates_and_consistency.py` (21 tests) runs every export against
a failing validation result and asserts the published directory is
byte-identical afterwards, parametrised over the whole surface so a new
export that forgets to check is caught. It also asserts every `export_*`
takes a `result` at all, and that writes are atomic.

Then it reads the four places every number lives — the constant, the
exported metadata, `docs/METHOD.md`, the method page — side by side.

`tests/test_site.py` gained nine checks: titles follow the language,
viewport and description present, every named local asset exists, the
window and age are shown beside a satellite figure, no page calls a cycle
end "the last pass", a withheld total is not announced as an alert, the
chart breaks at a gap, no page promises an arrival, and every page links
INA's alert system.

## 4. What the tests found

Five real defects, each fixed in the same commit as the test that found
it. This is the part worth reading.

**The daily CSV named its column `level_m` and carried m³/s.** Five of the
gauges are discharge, so 8,800 sat under a header asserting metres, in the
file the method page offers for download. It now has `value` and `unit`
columns, and the test judges each row against its own unit.

**`export_flooded` wrote to the published directory before consulting the
gate.** The stamp added in stage 6 marks the superseded file, and it ran
ahead of the `result.ok` check — so a failing data test could still change
what is served. The gate moved ahead of the stamp.

**`wave.html`'s anchor line still introduced a list that was removed.**
"The crest passed X on Y. Going by the record, it would arrive downstream
like this:" — ending on a colon and nothing, because the arrival table went
with the projections.

**The method page's "what this site does not do" list described the
withdrawn arrival dates as a current feature**, and still claimed no area
is published for the two gorge reaches. That is the third place that claim
survived, after the README summary and the map. It is replaced by the
limit that is genuinely unbounded: how much water sits under vegetation,
which neither the radar nor the optical reference can see.

**`test_every_published_area_moves_with_the_river` started running, after
months of skipping, and failed.** `posadas_ita_ibate` correlates −0.28
with Itá Ibaté in the superseded VV table — the Yacyretá reach, where that
mask read 353 km² of standing reservoir as flood water.

That one deserved more than a fix. The bar belongs to whatever is
published, and the VV product no longer is, while the live mask had **no
published stage correlation at all**: `_stage_correlation` was only ever
called from `export_flooded`. So `export_floodwater` now computes it for
the live mask and publishes it with a note saying it is context and not
validation, and the test covers the product that ships. The old test is
retargeted at what the frozen VV record is still for — the evidence behind
the reaches that publish no figure — rather than deleted.

**Newly measured, and the one genuinely new scientific result in this
stage:** every published stretch of the live VH mask correlates
**positively** with the stage at the gauge below it, +0.12 to +0.78,
including **+0.56** on the Yacyretá reach the old mask had at −0.28.

| stretch | IoU | r with downstream gauge |
|---|---|---|
| puerto_iguazu_libertador | 0.83 | +0.30 |
| libertador_posadas | 0.90 | +0.12 |
| posadas_ita_ibate | 0.87 | +0.56 |
| ita_ibate_corrientes | 0.64 | +0.66 |
| corrientes_goya | 0.48 | +0.66 |
| goya_la_paz | 0.53 | +0.78 |
| la_paz_parana | 0.67 | +0.51 |
| parana_villa_constitucion | 0.61 | +0.66 |

This is **context, not validation**, and is published as such. Extent at a
given stage depends on topography, channel–floodplain connectivity,
vegetation and whether the wave is rising or falling, so a weak
correlation is an expected result on some reaches. What it does establish
is that no published reach is measuring something that moves against the
river — which the superseded mask could not say.

## 5. One test of mine was wrong

`test_a_failed_data_test_writes_nothing_at_all` called three-argument
exports with the arguments in the wrong order and reported a false
failure for `export_river_today`. The test was fixed to bind `result` by
keyword and fill the positional arguments from the signature; the code was
not touched. Recorded because the opposite mistake — adjusting the code to
satisfy a wrong test — is the one that matters.

## 6. What remains blocked

- **The four `needs_ee` tests**, and with them U1, U2, U3 and the
  channel/beyond-channel decomposition of the published IoU. They need
  `EE_SERVICE_ACCOUNT_KEY` in the environment and `--run-external`. One
  exists in CI and is demonstrably live; this session had none.
- **Two `test_published_outputs` checks** cannot exercise their subject
  until the union measurement exists, for the same reason.
- **The held-out evaluation** of `AUDIT/03` §A2 has not been designed into
  tests beyond the leakage guard, because there is no held-out data to
  guard yet. `data/tuning_manifest.json` is the hook: adding a date to
  `held_out` turns the existing tests on.
- **No test asserts the water mask is correct**, and none can. The
  automated suite checks structure, provenance, consistency and the
  refusals; whether −25 dB finds water is a measurement against imagery,
  not an assertion.
