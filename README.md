# DOZYNKI

A local web app for vetting LSST alerts, combining ANTARES, ALeRCE and Babamul,
to build a labeled training set for real/bogus filtering and anomaly detection.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # add Babamul token (optional) and your labeler name
.venv/bin/python -m dozynki   # http://127.0.0.1:8765
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

**Aladin Lite.** The field card has an Aladin tab (DESI Legacy DR10, Pan-STARRS, DSS2,
unWISE, 2MASS, GALEX) with the LS DR10 and Gaia crossmatches overlaid; clicking an LS
source there also sets the host. Needs a WebGL2-capable browser.

**Watchlist.** The "Watch for more data" flag parks an object (queue status `watch`).
"Recheck watchlist" re-polls ANTARES; objects with new alerts return to the queue, and
the next label is stored with phase `followup`. Un-toggle the flag to finish with it.

Label taxonomy, flags and hotkeys live in `dozynki/labels.py`.

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
  assemble.py       fetch + cache per object, evidence / opinion split
  sources.py        queue sources (selection functions)
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
