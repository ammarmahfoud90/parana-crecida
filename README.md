# parana-crecida

Flood-wave tracker for the Argentine Paraná.

A flood wave takes days to travel from the Paraguay/Paraná confluence to the Delta,
so a crest observed at Corrientes is advance warning for Goya, Paraná, Rosario and
the Delta towns. This repo turns the observed daily stage into that warning: it
detects rises and crests at each gauge, estimates travel time between gauges from
the historical record, and projects where a wave now in progress will arrive.

Data comes from INA's a5 API (`alerta.ina.gob.ar/a5`).

## Two packages

| Package | What it is |
|---|---|
| `packages/ina-a5` | A client for the INA a5 API. Generic — knows nothing about the Paraná. |
| `src/parana_crecida` | The tracker. |

`ina-a5` exists because the ingestion code in
[rios-argentina](https://github.com/ammarmahfoud90/rios-argentina) had been
copy-pasted into six scripts, each with slightly different bugs. It is a separate
distribution so that rios-argentina can depend on it rather than keep its copies:

```bash
pip install "ina-a5 @ git+https://github.com/ammarmahfoud90/parana-crecida#subdirectory=packages/ina-a5"
```

The three things it fixes, which are the whole reason to extract it:

1. **Failure raises.** The original `get_json` returned `None` for a 429, a 500, a
   timeout and an empty result alike, so "the server throttled us" was
   indistinguishable from "this gauge has no data".
2. **Retries are status-aware.** 429 and 5xx back off exponentially and honour
   `Retry-After`; other 4xx fail at once, because retrying cannot help.
3. **One session per client.** `requests.Session` is not thread-safe, and the
   original climatology build shared one across a thread pool.

It also converts timestamps to Buenos Aires time instead of truncating the string,
and drops today's partial day by default — a day still filling up averages to a
level the river never had, which at a wave front reads as a spurious rise.

The on-disk cache format is unchanged from rios-argentina (`cache/{series_id}.json`
holding `[["YYYY-MM-DD", value], ...]`), so its existing `cache/` and
`cache_recent/` are readable here with no migration. There are tests that read the
real files to prove it rather than assert it.

## Install

```bash
python -m venv .venv --system-site-packages
.venv/Scripts/python -m pip install -e packages/ina-a5 -e .
```

On Linux or macOS use `.venv/bin/python`. Install `ina-a5` first or in the same
command — the tracker depends on it and it is not on PyPI.

Python 3.12 or newer. An `A5_TOKEN` environment variable is supported but has never
been required for the public endpoints.

## Use

```bash
crecida gauges            # the gauges in scope and how far each is trusted
crecida chain             # the downstream chain and river-km (--rederive to rebuild)
crecida lags              # measure travel time and freeze the table
crecida climatology       # day-of-year reference curves for every gauge
crecida oni               # NOAA's Oceanic Nino Index
crecida rainfall          # basin rainfall from Earth Engine (needs a credential)
crecida flooded           # flooded area from Sentinel-1 (needs a credential)
crecida animation         # render the flood as a loop (needs a credential)
crecida mapframes         # georeferenced overlays for the map (needs a credential)
crecida thresholds        # parse INA's alert/evacuation levels into data/
crecida fetch             # top up the cached series from the a5 API
crecida load              # load the JSON cache into DuckDB
crecida validate          # run the data tests
crecida export            # validate, then write site/data
crecida daily             # all of the above, in order: what CI runs
crecida freshness         # fail if what is published has gone stale
crecida guards            # check for stale, stuck or re-zeroed gauges
crecida catalogue         # page the whole a5 catalogue to data/
```

## Two layers, and the gate between them

The JSON cache under `cache/` is the **raw layer** - one file per series, the
format rios-argentina already writes, cheap to top up and safe to delete. DuckDB
is the **clean layer**: typed, keyed on `(series_id, day)` so duplicate days are
structurally impossible, and the only thing the exporter reads. A bad load can
always be rebuilt from the raw files without going back to INA's server.

Between the store and the site is the gate. `export` takes a validation result and
refuses to write unless it passed, so "export without validating" is not a thing
you can accidentally write. If a test fails, nothing on disk changes, CI skips its
commit and deploy steps, and the already-published page stays up - with its own
last-update date visible, so a reader can see the data has not moved.

The data tests, in `src/parana_crecida/validate.py`:

| Test | Blocks? |
|---|---|
| There are rows at all | yes - an empty store must never pass vacuously |
| No null fields, no NaN or infinity | yes |
| No duplicate `(gauge, day)` | yes |
| No readings dated after today | yes - that means a timezone bug, not a fast river |
| No series systematically outside its unit's range | yes |
| Thresholds in the same unit as their gauge | yes |
| Every tier-1 gauge has data | warns; blocks only if *none* do |
| Isolated out-of-range readings | warns |
| Data is fresh | warns - if INA is down, keep showing the last good reading |
| Latest reading within the gauge's own record | warns |

The range check splits two faults that look alike. A whole series out of range
means the series is not what we think it is - discharge filed as stage - and blocks.
One bad row in twenty thousand is a glitch in INA's archive and only warns. Both
cases are real and both turned up on the first run against live data.

## Alert and evacuation levels

The a5 API does not publish thresholds; they are only in INA's daily report PDF.
`crecida thresholds` parses that into `data/ina_thresholds.csv`, which is then the
one source for them.

Three rules it follows, because a wrong threshold would mislabel a real flood:

- **Nothing is invented.** A gauge INA does not list gets an empty row saying so,
  and a missing threshold reads as *unknown* everywhere downstream, never as 0 m.
- **Nothing is dropped.** Report rows that match no gauge in scope are written out
  too, marked `unmatched_report_row`, so the CSV can be checked against the PDF
  line by line.
- **Matching is on name and unit.** INA lists a *stage* for Puerto Pilcomayo, but
  the series held under that name is *discharge*; attaching a 5.35 m alert to an
  1,800 m3/s reading would mark every day of the record as catastrophic, so the
  match is refused and reported.

As of the 2026-10-28 report, **14 of the 45 gauges in scope have official levels**.
The other 31 are shown as having none.

Reuse rios-argentina's existing history instead of re-downloading a century:

```bash
crecida guards --cache-dir "../Rios de Argentina/cache" --cache-dir cache_recent
```

`--cache-dir` is repeatable and searched in order. Writes go to the first root.

Every data command takes `--report PATH` and writes a plain-text run summary, the
convention rios-argentina uses. Read it before committing.

## Scope: Puerto Iguazú to San Fernando

Gauges are tiered by how much they can carry, because the lower river is genuinely
harder and pretending otherwise would put confident arrival times on gauges that
cannot support them. `crecida gauges` prints the whole roster.

- **Tier 1 — Corrientes to Villa Constitución.** Thirteen main-stem gauges,
  correctly snapped, records of 120–143 years at Corrientes, Goya, La Paz, Paraná,
  Diamante and Rosario. Celerity is fitted here and arrival times are published as
  forecasts.
- **Tier 2 — below Villa Constitución, to San Fernando.** Tracked and charted, with
  wide uncertainty and no celerity fit until validated. Zárate, Campana and Escobar
  are on the Paraná de las Palmas branch, not the main stem; Campana, Escobar,
  Baradero, Tigre, San Fernando and Olivos are unrouted (no reach in radius, or
  inside the Río de la Plata box); the braided delta is a single centerline in
  HydroRIVERS; and stage there is tide- and backwater-affected, so a "crest" is
  partly not a river signal. The estuary gauges are the downstream boundary, not
  routing nodes.
- **Tier 3 — context.** Puerto Iguazú down to Santa Ana, between Itaipú and
  Yacyretá; Posadas to Paso de la Patria, below Yacyretá — all dam-operated. Plus
  the Paraguay, Bermejo and Iguazú inflows. Lead indicators, never routing nodes.

Six gauges are flagged `bad_snap`: `snap_v2.py` put Barranqueras, Santa Fe, San
Nicolás, Ramallo, San Pedro and Zárate on small tributary reaches. They need a
manual reach override before they can contribute river-km.

## View 1: river today

`site/index.html` is a plain page with no build step: it fetches the committed
`site/data/river_today.json` and renders every gauge from Puerto Iguazú to San
Fernando in river order, with the latest level, the change since the previous
reading, and the official alert and evacuation lines where INA publishes them.

Spanish and English, switchable, remembered per reader. Nothing in the pipeline is
translated — the JSON carries proper nouns and stable keys, and the page turns
those into text — so adding a third language touches one file. Gauge names carry
their real spelling on the page (*Paraná*, *Iguazú*, *Zárate*) while the CLI prints
ASCII, because a Windows console is cp1252 and an accent there is a crash.

The change is measured against the *previous reading*, not "yesterday": gauges have
gaps, and calling a two-day gap a one-day rise would overstate how fast the river
moved. Where there is a gap, the page says how many days it spans.

## View 2: the wave

`site/wave.html` lays the gauges out by river-km against time, coloured by how
high the river is for the season, with detected crests marked. A flood wave reads
as a band running diagonally down and to the right; its slope is the crest speed.

Three pieces sit behind it.

**The chain** (`data/parana_chain.json`). Travel time is meaningless without
distance, and distance along a river is not distance on a map. A walk down the
HydroRIVERS topology from Puerto Iguazú gives 19 gauges over 1,712 km. It checks
out against the real river — Corrientes to Rosario at 731 km against a published
730, Corrientes to Paraná at 569 against 570 — which is accurate enough for crest
speed and not accurate enough to quote to the kilometre.

The chain is derived once and committed. One that silently reordered itself
between runs would quietly change every travel time on the site, so `--rederive`
is explicit and its output is something you diff. Only gauges whose snap we trust
may take a position: a bad snap can land on the walk and look entirely reasonable,
and Confluencia — snapped to Puerto Iguazú's reach, 660 km above the junction it
is named after — would otherwise have entered the chain at km 0. The validator
caught it on the first run.

**Crest detection** (`events.py`). Smoothing is centred. A trailing filter shifts
peaks later by about half its window, and since the same shift lands on every
gauge it would look harmless while quietly biasing every travel time it feeds.
Gaps are not silently bridged: a short one is interpolated, a long one splits the
record, so no crest is invented inside a month of missing data. Run over the
record at Corrientes it returns 1983, 1992, 1905, 1998 and 1966 as the largest
crests, which are the floods that reach is known for.

**Travel time** (`lag.py`). Measured, not modelled, by two estimators reported
side by side: crest matching, which is interpretable and yields a distribution,
and cross-correlation, which is steadier but has no story attached. On the Paraná
they agree on all twelve routing legs.

| | |
|---|---|
| Corrientes → Paraná | 13 days (569 km) |
| Corrientes → Villa Constitución | 21 days (781 km) |
| Average crest speed | 37 km/day |

Both estimators are held inside a plausible celerity band. Without it, matching
pairs a crest with the next flood two months later, and correlation locks onto the
annual cycle — every gauge on this river is high in summer, so a 365-day lag
correlates beautifully and means nothing.

Short legs publish no km/day. San Lorenzo to Rosario is 21 km, which a crest
covers in hours, but daily readings can only record 0, 1 or 2 days; the resulting
speed describes the sampling interval rather than the river. Crest speed is fitted
over the whole chain instead, where the roundings cancel rather than compound.

Crests and travel times come from the whole record while the plot is windowed to
recent months. That split matters: a wave still rising has not crested, so there
may be no crest in the window at all, and the travel times must still be there.

**None of it is a forecast.** These are the travel times the river has shown.

## View 3: upstream drivers

`site/drivers.html` shows what is pushing the river: the Iguazú's discharge at
Andresito, Yacyretá's inflow and release, the Paraná at Confluencia, and the state
of El Niño. These lead the middle river by days.

Three kinds of thing sit on that page, and the page says which is which, because
they are not equally solid.

**Series with history**, from a5: Andresito, Yacyretá afluente and efluente, and
Confluencia. Each is placed against its own day-of-year distribution, so "32,500
m3/s" also reads as "p96 for late October".

**Values INA prints but does not serve.** Guaíra Porto and Represa Itaipú appear
in the daily report's flow table and nowhere in the a5 catalogue. There is today's
number and no history at all, so they are shown as a dated snapshot and explicitly
not compared to anything.

**ENSO**, from NOAA CPC's Oceanic Niño Index. Monthly, slow, and basin-scale: it
explains why the whole basin is wet this season, not the timing of any one crest.
The page labels the *reading*, not the episode — NOAA's episode definition needs
five consecutive overlapping seasons, which a single number cannot establish.

Adding the drivers also resolved two gauges Stage 1 could not map. INA's report
lists a *stage* for Puerto Pilcomayo while the series held under that name is
discharge, and lists Andresito and Yacyretá afluente with no obvious a5 id. The
catalogue gives Andresito as 26605 and Yacyretá afluente as 26684, both daily-mean
discharge, so **16 of the 47 gauges in scope now carry official levels**.

### Reference curves

`crecida climatology` trims rios-argentina's day-of-year percentile curves where
they exist and builds them from the cached record where they do not. The dam and
inflow series were never in that file, and without a curve the view cannot say
whether 12,300 m3/s is unusual — which is the only interesting question. A gauge
whose record is too short is left without one rather than given a thin curve: no
reference reads as unknown everywhere downstream, which is the honest answer.

### Basin rainfall

`crecida rainfall` pulls GPM IMERG from Google Earth Engine and averages it over
the two basins that meet at Puerto Iguazú:

| Basin | Area | Sub-basins |
|---|---|---|
| Alto Paraná, above the confluence | 834,192 km² | 115 |
| Iguazú | 67,536 km² | 9 |

Neither is hand-drawn. Both are built by walking HydroSHEDS' basin topology
upstream from the sub-basin at their outlet, and the walk is checked against
HydroSHEDS' own published upstream area — 834,184 km² and 67,537 km², reproduced to
within a rounding error. A test asserts it. They are disjoint siblings draining to
the same junction, so they do not double-count.

The scale difference is the point. Over seven days in late October 2026 the Iguazú
took 162 mm against the Alto Paraná's 65, and peaked at 89 mm in a day against 23.
A basin a twelfth the size responds to a storm that a continental average barely
registers, which is why Andresito rose 7,353 m³/s in a week.

Rainfall is a basin **average**: 12 mm over an area the size of France is a very
different thing from 12 mm on one town, and the page says so.

**This needs an Earth Engine credential.** Locally that is the OAuth token from
`earthengine authenticate`. A scheduled run cannot have one, so it looks for a
service-account key in `EE_SERVICE_ACCOUNT_KEY`; without that secret the rainfall
step is skipped and the committed series stands, going stale until someone runs
`crecida rainfall` by hand. Setting that up is written out in
[docs/earth-engine-service-account.md](docs/earth-engine-service-account.md). The page shows when it was last refreshed and marks it
once it falls behind.

## Knowing when it stops working

The export gate is deliberately lenient about staleness: if INA is down, the
honest thing is to keep showing the last good reading *with its date*, not to take
the site down. The cost is that a stalled feed looks exactly like a working one to
anyone not reading dates.

So `crecida freshness` runs as the **last** step of the daily job, after the
deploy. The site is already updated with whatever it has, and a stall turns the
run red — which is what actually reaches a person, since GitHub emails on a failed
scheduled run. It fails if the river data is more than 3 days old, or the rainfall
more than 10.

## View 4: flooded area

`site/flood.html` shows how much ground the river covers between Corrientes and
Goya, measured by Sentinel-1 radar, with the stage at Corrientes on the same
dates. A gauge says how high the river is at one point; it says nothing about how
wide, and on a floodplain tens of kilometres across that is the question.

Radar rather than optical imagery because the Litoral is cloudy exactly when it
floods. In VV polarisation calm open water is dark: it reflects the pulse away
from the satellite instead of scattering it back.

### Why the threshold is fixed, not Otsu

The textbook recipe is Otsu's method. It does not work here, and it fails in a way
that looks like success. Over this corridor:

| | median VV |
|---|---|
| water JRC maps as permanent | −15.9 dB |
| dry land | −11.2 dB |

Four decibels apart with overlapping tails, and water is only about 5% of the
corridor's area. There is no second mode to find, so Otsu splits *land* in two and
returns about −12 dB — which labelled **4,445 km² as water during the worst drought
in 77 years**, more than the flood it was compared against.

The threshold is fixed at **−16 dB**, chosen because it reproduces an independent
measurement: over the 2021 bajante it gives 536 km² where JRC Global Surface Water
maps 521 km² of permanent water in the same corridor. A test asserts that
agreement, so if the method drifts the suite says so rather than the site quietly
publishing a wrong number.

### Only full passes count

A Sentinel-1 pass crosses part of the corridor; the rest has no data, and
unobserved ground reduces to zero water — which reads as the river shrinking.
Coverage per date here is either about 99.7% or about 23–40%, with nothing in
between, so partial passes are dropped. Full coverage comes round every 12 days,
and that is the real sampling rate.

### What it shows

Through May to mid-October the area sits between 490 and 824 km² while the stage
wanders between 2.3 and 5.3 m. Then on 25 October, with Corrientes at 7.36 m —
above its evacuation level — it reaches **1,651 km²**, three times the low-water
baseline.

The correlation between area and stage across all passes is only r = 0.57, and
that is the finding rather than a problem: below about 5 m the river stays in its
channel and the floodplain does not respond at all. Area and stage are not two
measurements of the same thing.

### The loop

`crecida animation` renders one frame per satellite pass into
`site/media/flood.gif` -- twenty real observations of the same 212 km of river,
played in order. Nothing is modelled or interpolated.

Two choices make it readable. The backdrop is **static**, the median backscatter
over the whole period rather than each pass's own image: speckle and soil moisture
change between passes, and a live backdrop flickers so badly the water is hard to
follow. And water is **split in two** -- the permanent channel from JRC in blue,
drawn every frame because it is a constant, and this pass's detected water beyond
it in orange. Without that split a flood just looks like a slightly fatter blue
line.

Every frame carries its date, because the satellite comes round every twelve days
and an unlabelled loop reads as a smooth progression when it is nothing of the
kind.

### What it misses

Radar does not see water under vegetation. A flooded forest scatters the pulse
back from trunks and canopy and reads as dry land, so over the wooded islands and
wetlands this figure is too low. It is a floor, not a total, and the page leads
with that.

## View 5: the map

`site/map.html` puts the measurement on a real map: OpenStreetMap underneath, the
gauges as markers carrying their current level, and departamento boundaries so you
can see which ones are affected. It covers the whole Argentine Parana above the
Delta - **1,440 km from the Brazil frontier at Puerto Iguazu to Villa
Constitucion** - and carries two layers over it.

### Two layers, because they answer different questions

**The river, day by day** (the default). Every segment of the river line is
coloured by how high the water is *for the time of year*, every day. This is the
layer that shows movement: a wave travelling down the river reads as a band of
colour moving south over the weeks.

**Water by radar.** The twelve-day Sentinel-1 composites, as transparent overlays.
This is the layer that shows extent: how far the water has actually spread off the
channel and over the land.

The daily layer is the default because it is the one that updates every day and
covers the river evenly. The radar layer needs an Earth Engine credential and a
satellite pass; the daily layer is built from the store on every scheduled run,
which is why `crecida export` writes it and `crecida freshness` checks it.

They are different lengths - 179 daily values against 15 composites - so the
slider position cannot carry across a switch. What carries is the **date**:
switching layer lands on the nearest date the other layer has.

### Colouring a river from gauges along it

Stage is measured at 22 gauges on this run, daily. Between them the value is
interpolated, and two decisions make that defensible:

**Percentiles, not metres.** Every gauge has its own zero, so 5.7 m is a flood at
Goya and an ordinary day at Puerto Iguazu. The day-of-year percentile makes them
comparable: "above the 95th for late October" means the same thing everywhere.

**Interpolated along the river, not across the map.** Two points 20 km apart on a
meander are far closer along the water than two points 20 km apart across a bend.

**The line breaks at Yacyreta.** Above the dam is a reservoir pool, below it a
regulated river. They are hydraulically separate, and a smooth gradient across the
wall would be plainly wrong while looking perfectly smooth.

A gauge may not colour the river if its level is not a river signal: `flow` is a
different quantity, `bad_snap` and `unrouted` have no trustworthy position, and
`tide_affected` Delta gauges rise with the Rio de la Plata - painting the Parana
with their percentile would show a tide as a flood wave.

River-km come from the frozen chain. Walking a stretch's centreline vertices gives
1 to 7% more than the chain's gauge-to-gauge spacing, so each walk is scaled onto
its chain spacing; every gauge then lands exactly on its own point of the line,
and the line is monotonic by construction.

### Eight stretches, eight thresholds - and two that publish no number

The run is cut into eight corridors at gauge pairs. **The threshold does not
transfer between them**, so each is calibrated separately against JRC permanent
water over the 2021 bajante:

| stretch | km | threshold | bajante vs JRC | residual |
|---|---|---|---|---|
| Puerto Iguazu - Libertador | 189 | -17.0 dB | 192 / 149 | **29%** |
| Libertador - Posadas | 150 | -17.0 dB | 318 / 215 | **48%** |
| Posadas - Ita Ibate | 163 | -14.5 dB | 784 / 816 | 3.9% |
| Ita Ibate - Corrientes | 170 | -15.5 dB | 587 / 548 | 7.1% |
| Corrientes - Goya | 212 | -16.0 dB | 536 / 521 | 3.0% |
| Goya - La Paz | 218 | -16.5 dB | 496 / 536 | 7.5% |
| La Paz - Parana | 154 | -16.0 dB | 562 / 556 | 1.2% |
| Parana - Villa Constitucion | 221 | -16.5 dB | 738 / 715 | 3.3% |

Whole decibels were not enough - stretches sat 13-19% off at -16 dB. At half
decibels the six southern ones come within 8%, and they land in a tight band
rather than scattering, which is what says the method is measuring water.

**The two gorge stretches never got there, so they claim no area.** Above Posadas
the Parana runs in a basalt gorge. Widening the search did find thresholds that
matched JRC - around -20 and -21 dB - but with a 6.5 dB spread between
neighbouring reaches, which is a threshold compensating for radar shadow on the
valley walls rather than measuring water. Slope masking helped (455 to 296 km2)
and still not enough. They are marked `calibrated: false`: they render a picture,
they are kept out of every published total, and a test asserts both.

That is the one thing extending north cost. The measured result south of it is
unambiguous: at the flood's peak, water covers 3.1x the bajante baseline at
Posadas-Ita Ibate and Corrientes-Goya, falling to 1.5x by Parana-Villa
Constitucion - the wave spreading hardest where the valley is widest.

### Composites for the map, single passes for the charts

Sentinel-1's swath is narrower than this run of river and the corridors sit across
swath edges. Goya to La Paz gets full coverage **five** times in six months while
Esquina to La Paz gets it twenty, and two of the northern stretches get it **not
once**; splitting a corridor does not fix it, because one half still straddles the
edge. A 12-day composite covers every stretch at 99.6%, every window, and gives
all eight 14 or 15 cycles over the same six months.

So the map animates composites and the charts use single passes, and each says
which it is. A composite is a window, not an instant, and labelling it that way
costs nothing.

### Two filters, both earning their place

A pass is dropped if it covers less than 95% of the corridor: unobserved ground
reduces to zero water and reads as the river shrinking. And a pass is dropped if
it finds less than 70% of the permanent river, which happens when wind roughens
the surface enough to lift its backscatter above the threshold. That second one
was found by a test, not by inspection: one pass reported 218 km2 where the
permanent channel alone is 556.

### Layers and licences

- Basemap: OpenStreetMap, (c) its contributors
- Departamentos: [geoBoundaries](https://www.geoboundaries.org/) ADM2, CC BY 4.0.
  Argentina's own IGN service was timing out, and GADM - the obvious alternative -
  forbids redistribution, which committing it to a public repository would be.
- Gauges: INA, positioned from rios-argentina's snapped station file
- River line: HydroRIVERS centrelines, scaled onto the frozen chain's river-km

## The method page

`site/method.html` gathers, in one place, the assumptions and limits that were
spread across the five views: why percentiles instead of metres, what the frozen
chain is and how approximate its kilometres are, what a one-day lag resolution
does and does not permit you to say, how the radar threshold is calibrated and
where it fails, which gauges may not colour the river and why, and what the site
flatly does not do.

**Its two tables are built from the published measurement, not typed into the
page.** The calibration table reads `data/flooded.json` and the travel-time table
reads `data/wave.json`, so a threshold that changes changes here too. A test
asserts that the calibrated figures do not appear in the page source, because a
method page stating numbers from its own copy is exactly the claim that goes
quietly stale.

The flooded-area export now carries `threshold_db` and `calibrated` through to
the page for that reason.

### Tests for the pages

Until now nothing checked the published pages at all, which is where every
user-facing claim lives. `tests/test_site.py` adds structural checks across all
six: the nav links to every view and every target exists, each page marks itself
current, Spanish and English carry the same keys, every `data-t` slot has a
translation in both, every page carries the "not an alert system" notice and
links INA's alert, and every file a page fetches is actually published.

That last one is the check that would have caught the map going live without its
gauge and departamento layers.

## Arrival projections, and what they are worth

`track.py` is the only thing here that points forward, so it is built to say
as little as possible and to publish its own accuracy next to every claim.

It takes the **flood front** - the furthest downstream gauge that has already
passed its peak, with the river still rising below it - and adds the measured
travel times. Projecting from the front rather than from further upstream
matters: every leg adds its own error, so projecting Rosario from Corrientes
when Goya has already crested throws away the better measurement. If any leg
between the front and a gauge has no confident travel time, that gauge gets no
projection at all, rather than a total that quietly skipped a stretch.

**It says when, not how high.** A crest's height at the next gauge depends on
the channel it travels through and on what the tributaries contribute in
between, and there is no routing model here. A level projected without one
would be a hunch with a number on it.

**The range is not a confidence interval.** It is every leg at its historical
fast end against every leg at its slow end. Quantiles do not add, so summing
per-leg p25s does not give the 25th percentile of the journey - it gives
something wider and more pessimistic. Wider in the honest direction, and
labelled as what it is.

**A crest is provisional until it has actually fallen.** When this was written
Corrientes sat 0.07 m below a peak it had climbed 2.50 m to reach. That is not
a crest, it is a river that has stopped rising, and the page says which it is.

### How well it actually does

`crecida backtest` runs the tracker over the whole record, as it would have run
on each day, and scores what it would have published against the crest the
river then reached. The result is frozen to `data/track_skill.json` and
published beside the projection, so the page states its own accuracy instead of
leaving the reader to assume one.

Over 933 projections on 200 occasions since 1998:

| | |
|---|---|
| actual crest inside the published window | **23%** |
| median absolute error | **9 days** |
| same, with no travel times at all | 14 days |
| within 7 days | 46% |

So the travel times carry real signal - they beat the no-lag baseline by five
days - but a nine-day median error on journeys of a few days to a fortnight is
too large to treat as a date, and the page says so in those words.

There is also a floor under any method here. Measured over 995 annual flood
crests, a Parana crest sits within 10 cm of its peak for a **median of six
days**, so "the day of the crest" is itself only defined to about three days
either way.

Two things in the backtest are easy to misread, and are written down where
they are computed:

- **The scoring is noisiest next to the anchor.** Error is worst one leg out
  (median 16 days) and best at four to seven (4 days). That is not the river
  becoming more predictable with distance; it is that the first local maximum
  after a date a day or two away is as likely to be a ripple as the crest.
- **The baseline matters more than the headline.** A method that cannot beat
  "the crest arrives everywhere the day the anchor peaked" has added nothing,
  and without computing that there is no way to know.

Two bugs in the projection were found by the backtest and by nothing else: the
anchor was the furthest gauge that had turned over rather than the wave front
(over any past window every gauge has turned, so it picked the last gauge on
the chain, with nothing below it), and the record was not truncated at the day
being asked about, so a question about a past date saw the whole record after
it and anchored on a flood that had not happened yet. Both have tests.

## The Delta, and why almost everything stops there

Below Villa Constitucion the Parana stops being a river and becomes a delta:
branches across a belt of islands tens of kilometres wide, with the Rio de la
Plata taking over. Everything here was extended south to see what survived.
Almost nothing did, and each refusal is a measurement rather than a worry.

### Where it stops being the Parana

Correlating every Delta gauge against Villa Constitucion over 2000-2026:

| gauge | r2 with Villa Constitucion | day-to-day r |
|---|---|---|
| San Nicolas, Ramallo | 0.96 - 0.98 | 0.69 - 0.74 |
| San Pedro, Baradero | 0.83 - 0.89 | 0.37 - 0.39 |
| Ibicuy | 0.73 | 0.23 |
| Zarate, Campana, Escobar | 0.08 - 0.27 | about 0 |
| Tigre, San Fernando, Olivos | 0.01 - 0.02 | **-0.15 to -0.28** |

The estuary gauges move *against* the river day to day. Wind and tide drive
them, not the Parana, and no amount of routing will make them say otherwise.

### Why the river line goes no further

HydroRIVERS is a tree: every reach has exactly one reach below it. A delta
does the opposite - one branch splits into two - so a distributary network
cannot be expressed in it. The western branch past San Nicolas, Ramallo and
San Pedro is simply absent: at San Nicolas's latitude the main line runs
**46 km to the east**.

That is the whole explanation for `bad_snap`, and it is worth stating
precisely. San Nicolas's nearest reach drains **2,030 km2**; the Parana there
carries **2,535,000 km2**. Nearest-reach snapping chose a creek over a
continental river, because the river was 46 km away. Re-snapping constrained
to the main stem does not fix it either - it only moves the gauge onto a
channel it is not on.

So the coloured river line stops at Villa Constitucion, the last gauge that can
anchor a percentile, and `riverstage` enforces that rather than leaving it to
the data: it stops at the first stretch whose downstream gauge cannot colour
the river.

### Why the Delta publishes no area

The Delta corridor - Villa Constitucion to Ibicuy, down the eastern channel,
the only Delta branch the topology carries - **calibrates**. At -15.5 dB the
2021 bajante gives 277 km2 against JRC's 302, an 8.2% residual, inside the 10%
bar every other stretch had to clear, and 1.0 dB from its upstream neighbour
rather than an outlier.

Then it falls apart on the flood it was built to measure:

| | |
|---|---|
| series over six months of record flood | 430 - 1,326 km2, **no trend** |
| correlation with stage at Ibicuy | **-0.39** |
| the six calibrated stretches, same test | +0.43 to +0.72 |

The water area falls as the river rises. A negative correlation is not a weak
measurement, it is a measurement of something else: tide state and wind on open
water, averaged into a 12-day composite. So the Delta is marked
`calibrated: false` alongside the two gorge reaches - it renders a picture and
claims no number.

**A calibration that passes is necessary and not sufficient.** The gorge failed
the bajante check and was caught. The Delta passed it and would have shipped a
number that falls as the river rises. The test that caught it - does the
measured area actually track the gauge - now runs over every stretch.

## Four improvements, and what each was worth

### The Brazilian gauges: tested, and closed

ANA's service was the one dependency the plan flagged as untested, because
Brazilian gauges above Itaipu are the only available source of *lead time* -
the one thing that could lift the arrival projections above 23%.

It is closed. The catalogue works: `ListaEstacoesTelemetricas` returns 5,415
active telemetric stations with coordinates, 123 of them in the upper Parana
box above Itaipu, including PORTO MEIRA at Foz do Iguacu, which is
effectively co-located with our own Puerto Iguazu gauge. But every data
endpoint - `DadosHidrometeorologicos`, `DadosHidrometeorologicosGerais`,
`HidroSerieHistorica` - answers *"Sem dados para esta estacao no periodo
solicitado"* for every station and every period tried, across 2020, 2023,
2024 and 2026. `HidroInventario` returns `<Vazio>` for every query. The
modern REST gateway needs registered credentials.

So the data exists and is not served on the open path. Getting it means
registering for ANA's HidroWebService, which is a decision rather than a
task. Recorded here so nobody tests it blind again.

### Crest speed by wave size: measured, and it does not help

Celerity rises with stage, so a single median per leg should be fitting two
populations at once. The travel-time table now splits each leg at its own
median wave magnitude - the day-of-year anomaly, which does not saturate the
way a percentile does when half the crests in a flood read 100.

The split barely separates anything. Most legs return the *same* median for
large and small waves:

| leg | all | small | large |
|---|---|---|---|
| Corrientes - Empedrado | 1.0 | 1.0 | 1.0 |
| Goya - Esquina | 3.0 | 3.0 | 3.0 |
| Parana - Diamante | 2.0 | 2.0 | 1.0 |
| La Paz - Santa Elena | 1.0 | 1.0 | 2.0 |

The two that differ disagree about the sign. The reason is the resolution
the method page already documents: lags are whole days, most legs are 1 to 3
days, and a 20% celerity difference on a two-day leg is 0.4 days.

Wired into the projection and backtested over the same 933 projections, it
changes nothing: inside-window 23% to 25%, median error 9 days either way,
within-three-days 24% to 23%. About one standard error, in both directions.

**So the code path was reverted and the measurement kept.** The bands are
published in `travel_times.json` because the negative result is worth having
written down; the projection still uses the pooled figure, because this
project does not ship a branch it cannot show works.

### CSV, because JSON is not really publishing it

The pages read JSON because a browser does. Anyone checking the work, or
using it in their own basin, wants columns. `crecida export` now writes
three files next to the JSON, and the method page links them:

- `river_daily.csv` - every gauge, every day, level and percentile (8,454 rows)
- `flooded_area.csv` - every stretch at every pass, uncalibrated ones absent
- `travel_times.csv` - every leg, with spread and how many floods it was
  measured over

The stage file covers the published window, not the full record: 1884 to now
is 950,000 rows and belongs in the DuckDB store, not behind a link on a page.

### The Paraguay, and a correction

A page about where the water comes from was showing only the Parana's own
side of the confluence. The Parana at Corrientes is the sum of two rivers,
and which one is high is the first thing a reader should be able to see.

Three gauges were already in the store and on no page: Puerto Pilcomayo
(Paraguay discharge), Puerto Formosa (Paraguay stage) and Bermejo. They are
now on view 3, in the order the water arrives.

They also correct something stated earlier in this project's own notes. The
Paraguay is **not** driving this flood:

| | percentile for the day of year |
|---|---|
| Andresito (Iguazu inflow) | 100 |
| Yacyreta efluente | 100 |
| Corrientes, main stem | 100 |
| Bermejo | 88 |
| Puerto Formosa (Paraguay) | 86 |
| Puerto Pilcomayo (Paraguay) | **56** |

This is a Parana flood. Until these gauges were on the page there was no way
for a reader to tell, which is the whole argument for putting them there.

## Status

Built:

- `ina-a5`: client, catalogue paging, observation download, cache, quality guards.
- `parana_crecida`: gauge roster, INA threshold parsing, the DuckDB store, the data
  tests, the export gate, view 1, and the daily GitHub Actions run.
- The river chain, normalisation against day-of-year climatology, crest detection,
  travel time between gauges, and view 2.
- Dam and inflow drivers, ENSO ingestion, reference-curve building, basin rainfall
  from Earth Engine, and view 3.
- Flooded area from Sentinel-1 for eight stretches, Puerto Iguazu to Villa
  Constitucion, each separately calibrated - six of them successfully; views 4
  and 5, and the loop.
- The daily river line: stage as a day-of-year percentile along all 1,440 km,
  written by `crecida export` on every scheduled run and checked by
  `crecida freshness`.
- Rainfall refreshing itself in CI from an Earth Engine service account.
- The method-and-limits page, and the first tests over the published pages.
- Arrival projections from the flood front, with a backtest that scores them
  and publishes the score beside them.

Not built yet — see `.claude/plans` for the full plan:

- An area for the Delta, which would need a method that separates tide and
  wind from flood, not a better threshold.
- An area for the two gorge stretches above Posadas, which would need a method
  that handles radar shadow in a valley rather than a tuned threshold.
- A projection of how *high* the crest will be, which needs a routing model
  rather than a travel time.
- Lead time from Brazil, which needs credentials for ANA's HidroWebService
  rather than code.

Events are defined relative to each gauge's own seasonal normal (the day-of-year
p90/p95 curves from rios-argentina's `climatology_final.json`) as well as against
the official levels. Both are worth having: the official levels are what people act
on, the percentiles are what make gauges comparable.

**This is not an alert system.** Travel times describe what has already happened,
not a forecast. Every page links to INA's official alert.

## Tests

```bash
.venv/Scripts/python -m pytest
```

Two tests read the real rios-argentina cache from `~/rios-argentina/cache_recent`
and skip if it is not there.

## Data provenance

Observations and metadata are from the Instituto Nacional del Agua (INA),
Argentina, via the public a5 API. River topology is derived from HydroRIVERS v10.
Historical series, percentile curves and the snapped station network come from
rios-argentina.
