# DOZYNKI

A local web app for vetting LSST alerts, combining ANTARES, ALeRCE and Babamul,
to build a labeled training set for real/bogus filtering and anomaly detection.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # add Babamul token (optional) and your labeler name
.venv/bin/python -m dozynki   # http://127.0.0.1:8765 (landing page; vetter at /vet)
```

## How labeling works

1. **Sample** – "Sample random LSST" draws a uniform random set of ANTARES loci
   that have an LSST diaObject (Elasticsearch `random_score`). The seed and
   query are saved with every queued object, so the selection function of the
   training set is known. The unit is the *object*, not the alert.
2. **Blind pass** – you see only evidence: the combined ZTF+LSST light curve
   (difference flux on a common nJy scale, plus Babamul forced photometry when
   configured), ANTARES cutouts, a Legacy Survey DR10 field image, and an
   *independent* crossmatch (LS DR10 with photo-z and DLR host ranking, Gaia DR3,
   PS1, Milliquas, SIMBAD, NED, optional SkyBoT). Pipeline SSO associations
   (Rubin `ssObjectId`, ZTF `ssnamenr`) are shown because they are positional
   facts, not classifier output.
3. **Reveal** – committing a blind label reveals broker opinions: ALeRCE
   classifier probabilities (each classifier version separately), LSST
   reliability / ZTF drb, ANTARES tags and catalog matches, Babamul flags and its
   ZTF association (flagged when it disagrees with ANTARES).
4. **Revise (optional)** – committing again stores a separate `revealed` label.
   Both are kept, so you can measure how much brokers shift your labels.

**Filters.** The bar under the header limits work to objects with at least N
detections (LSST + ZTF, upper limits not counted) and/or whose first alert, latest
alert, or alerting period falls in a date range. The same filters apply to "Sample
random LSST" (as an ANTARES query, recorded in each object's selection parameters) and
to which already-queued object comes up next. Note that ANTARES's "latest alert" includes
ZTF alerts, and its LSST objects currently have first alerts only through mid-July 2026.

**Sampling a night.** "Sample night" queues the next N random objects (the count box) whose first ANTARES
alert came on one Rubin night (dayObs: noon to noon UTC), with at least the filter bar's detections; the bar's
date range is ignored. The first sample of a night at a detection minimum fixes a random order (seeded on
`locus_id`, which new alerts don't change); later samples continue it, so they never repeat and, given time,
cover the whole night. ANTARES counts and pages only 10,000 matches, so a busier night can't be finished
at that minimum; raise it. Batches are recorded in the `night_pulls` table, and the landing page's Nights
card shows each night's progress: sampled and vetted of the night's size, and what is left. ANTARES's first alert includes ZTF, so objects
with earlier ZTF alerts are not part of a later night. Night samples are left out of the filter lab and
compare pages' default population, which is the uniform random sample.

**Multiple classes.** Click (or press the digit) for the primary class; Shift+click
(or Shift+digit) adds "could also be" classes, e.g. primary *bogus* that could be a
*star*, or *AGN* that could be a nuclear *transient*. `is_real`/`is_transient` follow the
primary class; the export adds `alt_labels`, `possibly_real` and `possibly_transient`.

Keys: `1`–`6`, `0` class · Shift+digit alternative class · `w` watch · `Enter` commit · `n` next · `s` skip · `h` hostless · `f` cycle light-curve mode · `+`/`-` zoom.
Click anywhere near a source in the field image (the nearest one is picked) or a row of the host table to record the host.

**ZTF.** ZTF detections and upper limits come from the ANTARES locus when ANTARES
has associated a ZTF object. The crossmatch panel also searches for ZTF objects by
position through ALeRCE and warns when that disagrees with ANTARES. In the science-mag
view, ZTF points are approximate totals (reference flux + signed difference flux), shown only
when the nearest reference source is within 1.5″.

**Detection offsets.** A polar plot (north up, east left) of each detection's position
relative to the mean LSST position, colored by time, with the rms scatter and a same-night
motion estimate. A stationary source clusters at the center; a mover makes a time-ordered
line. Objects cached before positions were stored get them backfilled into a separate cache.

**Source classes.** Catalog matches are colored by what the catalogs report (not a classifier):
star, galaxy, AGN/QSO, known SN/transient, unclassified. LS DR10 `type` gives star (PSF) or
galaxy (EXP/DEV/SER); a confident Gaia (significant parallax/proper motion), Milliquas, SIMBAD or
NED class within 1″ confirms or overrides it. Dashed outlines and a "?" mark uncertain classes
(PSF without a Gaia star, REX, Gaia without significant motion, SIMBAD candidates, disagreeing
catalogs); hover a marker or chip for the reasoning. The crossmatch panel lists everything within
1″ of the alert first. Colors and SIMBAD/NED type mappings live in `static/app.js`.

**Aladin Lite.** An Aladin panel sits beside the LS DR10 cutout (default Pan-STARRS; also DESI Legacy DR10, DSS2,
unWISE, 2MASS, GALEX) with the LS DR10 and Gaia crossmatches overlaid; clicking an LS
source there also sets the host. Needs a WebGL2-capable browser.

**Watchlist.** The "Watch for more data" flag parks an object (queue status `watch`).
"Recheck watchlist" re-polls ANTARES; objects with new alerts return to the queue, and
the next label is stored with phase `followup`. Un-toggle the flag to finish with it.

Label taxonomy, flags and hotkeys live in `dozynki/labels.py`.

## Rejection filters

The `filtering` branch adds cuts that *throw objects away* and scores them against
your labels. Rules live in `dozynki/rejection.py`. Each one reads only cached
payloads and returns reject, keep, or "no data" (kept). A pipeline is an ordered JSON list;
`cuts.example.json` is the default pipeline with every parameter spelled out, so copy it
(`cp cuts.example.json cuts.json`) and edit:

```json
[{"rule": "gaia_star", "max_sep": 1.5}, {"rule": "min_nights", "n": 2}, {"rule": "lsst_reliability", "min_rel": 0.3}]
```

```bash
.venv/bin/python -m dozynki.evaluate --list-rules
.venv/bin/python -m dozynki.evaluate                       # default pipeline
.venv/bin/python -m dozynki.evaluate --pipeline cuts.json --target real
.venv/bin/python -m dozynki.evaluate --target transient:nuclear,agn --include-alt
```

**Filter lab.** `http://127.0.0.1:8765/rejection` (or the "Filter lab" tab)
does the same thing interactively. It compares two pipelines, A and B, side by side.
- Pick the target from chips: any class or transient subclass, optionally counting
  "could also be" classes.
- Toggle rules and edit thresholds; the metrics, the kept-by-class chart, the per-step
  table and the list of objects A and B disagree on update as you type. Object IDs in that
  list open in the vetter (`/vet?object=<id>`).
- Sweep any threshold of A to plot completeness, purity and false-positive rate against
  it, then click a point to set it.

The page stores nothing in the database. Your settings are remembered in the browser.

- **Truth** is each object's first blind label (`--truth final` for the latest label of any phase).
  Rules marked `broker` read reliability or ALeRCE output, which revealed labels may have seen.
- **Population** is the uniform random sample only (`--all-sources` to include everything),
  so the rates estimate the LSST stream within the recorded sampling filters. `unsure` is excluded.
- **Metrics** (Wilson 95% intervals): completeness (target kept / target), purity (target
  kept / kept), false-positive rate (non-target kept / non-target), bogus leak (bogus kept /
  bogus), against a no-filter baseline. The target is `transient` by default, or `real`.
- **Per step**: rejections by class with the step alone, rejections no other step makes,
  and the pipeline's metrics with that step left out.

Every step's decision is stored in the `rejections` table under the pipeline's version
hash, and each run writes `exports/rejection/<timestamp>/` (`report.txt`, `metrics.json`,
`decisions.csv` with the features behind each decision).

## Comparing labels

`http://127.0.0.1:8765/compare` (or the "Compare" tab) draws a confusion matrix between
any two label sources: each labeler's first blind or latest labels, and every ALeRCE
classifier found in the cache (top class). Nothing is stored. Code lives in `dozynki/compare.py`.
- Each source keeps its own class names (ALeRCE "SN", "VS", …), so the matrix need not be
  square. Agreement maps both sides onto the DOZYNKI classes with `ALERCE_CLASSES`; outlined
  cells count as agreement. "unsure" and unmapped classes (e.g. ALeRCE "Stochastic") are left out and counted.
- View all classes, or collapse to real vs not real, or transient vs not transient.
- Accuracy (Wilson 95%), Cohen's κ, and per-class recall / precision / F1. The reference is the rows.
- "ALeRCE top p ≥" drops low-confidence broker calls; "incl. could-also-be" adds an accuracy
  that also accepts a match with an alternative class.
- A table scores every source against the reference. Click a matrix cell to list its objects,
  most confident broker calls first, with links into the vetter.
- Blind vs latest for the same labeler shows how much revealing broker opinions shifted your labels.
- Like the filter lab, only the uniform random sample is used unless "non-random objects too" is on.

## Achievements

The landing page shows tiered achievements for the current labeler (`DOZYNKI_LABELER`), and the vetter
announces a new tier when a label earns one. Definitions and tiers live in `dozynki/achievements.py`; nothing
is stored, every count comes from your latest label of each object (primary class only).
- Counts: objects vetted, labeled star, labeled bogus, hosts associated.
- Confirmed finds: your class agrees with a catalog within 1″ — transients with a SIMBAD/NED supernova type or
  TNS-style name (SN/AT 20xxabc; there is no direct TNS query yet), rare transients typed TDE or nova, AGN in
  Milliquas/SIMBAD/NED, asteroids with a pipeline SSO association or a SkyBoT match.
- Nights: nights sampled to the end with every object vetted by you. A night sampled at several detection
  minimums counts by the lowest one.
- Filtering: a filter lab pipeline changed from its default. The filter lab keeps pipelines in the browser,
  so this one is per browser.

## Export

The "Export" button (or `python -m dozynki.export`) writes `exports/<timestamp>/`:

- `labels.parquet/csv` – every label row, blind and revealed
- `objects.parquet/csv` – one row per object: final label, blind label,
  `changed_after_reveal`, selection metadata, crossmatch summary, and
  `broker_*` columns kept separate from the label
- `lightcurves.parquet` – normalized photometry per object

## Layout

```
dozynki/
  app.py            FastAPI endpoints
  compare.py        confusion matrices between label sources (labelers, ALeRCE)
  assemble.py       fetch + cache per object, evidence / opinion split
  sources.py        queue sources (selection functions): random sample, night samples
  achievements.py   achievement definitions and counts
  crossmatch.py     independent catalog crossmatch + DLR
  brokers/          antares.py, alerce.py, babamul_client.py
  db.py             SQLite (data/dozynki.sqlite): queue, cache, labels
  export.py         training-set export
  labels.py         taxonomy
  static/           UI (vanilla JS + Plotly)
```

## Planned

- Babamul Kafka topics and CSV object lists as queue sources
- ALeRCE LSST forced photometry in the light curve
- Anomaly scoring stage for objects labeled real transients
