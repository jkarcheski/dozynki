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

**Random and Filtered modes.** The vetter's mode switch picks what you see. *Random* is everything sampled,
as drawn. *Filtered* runs a whole night through a saved filter from the filter lab ("Filter whole night"),
like ANTARES's per-night filter counts, so on a live night you vet only what passes and never sample more.
A night is every LSST object whose first or latest ANTARES alert came during it (the locus listing has no
other alert times, so for a past night this misses objects that alerted on it and again later). The run
(`nightrun.py`, table `night_runs`) has two stages:

1. The rules the locus listing answers run over the whole night at once: ANTARES applies detections,
   solar-system association, its catalog matches and the SCIMMA tag in its query, and Galactic latitude,
   ecliptic latitude and span run here on each listing (`rejection.LISTING_ES` / `LISTING_LOCAL`). This
   takes about 40 s for a 50,000-object night and gives "N of M pass".
2. Everything that passes is queued as `screening` in a random order, and a background thread (`screen.py`)
   fetches each object (the fetch vetting needs anyway), applies the whole filter, and moves it to `pending`
   or `filtered`. It also drops objects with no detection on the night. The rejecting step goes in
   `queue.screen_result` and every decision in `rejections`; an object that can't be fetched is kept
   (`screen_result` "error"). This runs at about one object a second, so a big night is checked over hours,
   ahead of you as you vet.

The filter as it was is stored with the run and each object (`source_params.screen`). The two modes have
separate queues, so neither takes the other's objects; a random-mode sample that draws an object a filter
threw away takes it back into random mode, so Random stays random. The Nights card lists filtered nights
with their pass counts, and finishing a night counts toward achievements in random mode only.

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

Rules draw on the light curve, the independent crossmatch, broker scores, and:
- **Rubin pipeline measurements** of each LSST detection: difference-image extendedness, dipole flag,
  glint-trail flag, PSF-fit χ²/N, size relative to the PSF (too sharp or too large), failed
  measurement flags, SNR and trail length. New fetches keep them (`antares.get_locus` "lsst_details");
  objects cached earlier get them from "Fetch them" in the filter lab (`details.py`, a separate
  `lsst_details` cache kind, so labeled evidence is untouched). Until then those rules see no data.
- **ZTF's PS1 star/galaxy score** of the nearest Pan-STARRS1 source (`sgscore1`, `distpsnr1`).
- **ANTARES locus metadata**: its bright guide star and Gaia DR3 variability catalog matches (yes/no,
  ANTARES gives no separation) and the SCIMMA quality-transient filter tag (broker output).

```bash
.venv/bin/python -m dozynki.evaluate --list-rules
.venv/bin/python -m dozynki.evaluate                       # default pipeline
.venv/bin/python -m dozynki.evaluate --pipeline cuts.json --target real
.venv/bin/python -m dozynki.evaluate --target transient:nuclear,agn --include-alt
```

**Filter lab.** `http://127.0.0.1:8765/rejection` (or the "Filter lab" tab)
does the same thing interactively for one pipeline, scored against no filter.
- Pick the target from chips: any class or transient subclass, optionally counting
  "could also be" classes.
- Toggle rules and edit thresholds; the metrics, the decision tree, the kept-by-class chart and
  the per-step table update as you type. Object IDs in the tree's lists open in the vetter
  (`/vet?object=<id>`).
- Sweep any threshold to plot completeness, purity and false-positive rate against
  it, then click a point to set it.

- **Build a filter with questions** walks through the rules one plain-language question at a time
  ("Drop objects sitting on a Gaia star?"), starting with what you want to keep. Each choice shows how many
  target and other objects it would throw away after your earlier answers, and the decision tree beside it
  grows as you answer. Questions, choices and their order live in `QUESTIONS` in `static/rejection.js`;
  a rule without an entry gets a generic yes/no question.
- **Decision tree** draws the pipeline as a cascade: each object falls to the first question it answers yes to.
  Bars show target vs other; click a box to list its objects.
- **Saved filters** are named pipelines stored in the `saved_pipelines` table, so every browser sees them.
  Save one from the pipeline card or at the end of the questionnaire, and load one back. The pipeline
  card shows which saved filter it came from and "(edited)" once it differs. The vetter's Filtered mode
  runs whole nights through one. On the command line:
  `.venv/bin/python -m dozynki.evaluate --saved "<name>"`.

Evaluating stores nothing in the database. Your other settings are remembered in the browser.

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

## Profiles (shared server)

On a laptop everyone is `DOZYNKI_LABELER` and nothing changes. A shared server sets `DOZYNKI_ACCOUNTS=1`
(and `DOZYNKI_SECURE_COOKIES=1` behind https), and then:

- **Guests.** Anyone can vet without signing in. Each browser gets a random guest id (a cookie), and its labels
  count toward the totals as one "guests" row on the leaderboard, never by name.
- **Profiles.** "guest · sign in" in the tab bar opens `/account`: a handle, a password and an email. The handle is
  the labeler name on that person's labels; making a profile moves the browser's guest labels to it (a checkbox).
- **Email** is used only for password resets and is stored as a salted scrypt hash, never as the address. A reset
  asks for the handle and the same email, and the one-hour link goes to the address just typed. Mail goes through
  `DOZYNKI_SMTP_*`; without `DOZYNKI_SMTP_HOST` the link is printed in the server log instead.
- **Each labeler has their own queue.** Labeled, skipped and watched are per labeler (`queue_status`), so several
  people can label the same object independently, which is what the Compare page needs for agreement. Only a
  person's own labels show in the vetter, and blind mode and the reveal are per person. `queue.status` now says only
  whether an object is ready (`pending`), waiting for (`screening`) or thrown away by (`filtered`) a filter.
- Passwords are scrypt hashes; sessions are random tokens kept as SHA-256 hashes, 30 days, ended everywhere by a
  password change. Sign-ins and reset requests are rate-limited (in memory). Code in `dozynki/accounts.py`.

Admin commands (with the server's environment):

```bash
.venv/bin/python -m dozynki.accounts add <handle> --labels-from anonymous   # profile that takes over a laptop's labels
.venv/bin/python -m dozynki.accounts password <handle>                     # set a forgotten password by hand
.venv/bin/python -m dozynki.accounts list
```

## Achievements

The landing page shows tiered achievements for the current labeler (`DOZYNKI_LABELER`), and the vetter
announces a new tier when a label earns one. Definitions and tiers live in `dozynki/achievements.py`; nothing
is stored, every count comes from your latest label of each object (primary class only).
- Counts: objects vetted, labeled star, labeled bogus, hosts associated.
- Confirmed finds: your class agrees with a catalog within 1″ — transients on TNS or with a SIMBAD/NED supernova
  type or TNS-style name, rare transients typed TDE or nova, AGN in Milliquas/SIMBAD/NED, asteroids with a
  pipeline SSO association or a SkyBoT match.
- TNS: "I Knew You Were Trouble" counts transients you labeled before TNS received their discovery report, and
  "Told You So" ones TNS has since classified. TNS comes from ANTARES's copy (`tns_public_objects` catalog
  matches, so no TNS account is needed), cached per object as kind `tns`. A transient label fetches it for that
  object; "Check TNS" on the landing page re-checks every object labeled transient, since TNS changes after you
  label. ANTARES refreshes its copy on its own schedule, so a new report can take a while to show up.
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
