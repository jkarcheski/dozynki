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

Keys: `1`–`6`, `0` class · `Enter` commit · `n` next · `s` skip · `h` hostless · `f` cycle light-curve mode.
Click a galaxy in the field image or host table to record the host.

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
