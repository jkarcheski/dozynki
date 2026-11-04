"""Archival light curves at the object's position, for events that never reached TNS (or YSE PZ).

Two position searches, neither needing a login:
  - ZTF from IRSA's light-curve service (data releases, 2018 on): PSF magnitudes measured on the science
    images, not difference images, so a transient on a galaxy shows as a brightening of the galaxy's light.
  - Pan-STARRS1 DR2 single-epoch detections from MAST (2009-2014): catalog detections on single exposures,
    so mostly bright or long-lived activity.
Both are total brightness, so the vetter draws them in the sci mag view only. Detections only: neither
service gives upper limits.

A failed survey is reported in `errors` and retried when the object is next loaded (assemble.py); after a
timeout or server error it is skipped for DOWN_SECONDS, so an outage doesn't stall every object.
"""

import io
import math
import threading
import time

import pandas as pd
import requests

RADIUS_ARCSEC = 1.5
TIMEOUT = 30
DOWN_SECONDS = 300
IRSA_ZTF = "https://irsa.ipac.caltech.edu/cgi-bin/ZTF/nph_light_curves"
MAST_PS1 = "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/detection.csv"
PS1_BANDS = {1: "g", 2: "r", 3: "i", 4: "z", 5: "y"}
PS1_MIN_QF = 0.9  # psfQfPerfect: fraction of the PSF on unmasked pixels

_down = {}  # survey -> time.time() until which it is skipped
_down_lock = threading.Lock()


def _point(survey, mjd, band, mag, mag_err):
    return {"survey": survey, "mjd": float(mjd), "band": band, "mag": float(mag),
            "mag_err": float(mag_err) if mag_err is not None and math.isfinite(mag_err) else None}


def _csv(r):
    """A CSV response as a DataFrame; MAST answers "nothing here" with an empty body, not just a header."""
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text)) if r.text.strip() else pd.DataFrame()


def ztf(ra, dec):
    """ZTF data-release detections with clean flags (catflags 0)."""
    pos = f"CIRCLE {ra} {dec} {RADIUS_ARCSEC / 3600}"
    df = _csv(requests.get(IRSA_ZTF, params={"POS": pos, "FORMAT": "csv"}, timeout=TIMEOUT))
    if df.empty:
        return []
    df = df[(df["catflags"] == 0) & df["mag"].notna()]
    return [_point("ztf_dr", m.mjd, m.filtercode[-1:], m.mag, m.magerr) for m in df.itertuples()]


def ps1(ra, dec):
    """Pan-STARRS1 DR2 single-epoch PSF detections, mostly unmasked (PS1_MIN_QF)."""
    params = {"ra": ra, "dec": dec, "radius": RADIUS_ARCSEC / 3600, "pagesize": 5000,
              "columns": "[obsTime,filterID,psfFlux,psfFluxErr,psfQfPerfect]"}
    df = _csv(requests.get(MAST_PS1, params=params, timeout=TIMEOUT))
    if df.empty:
        return []
    df = df[(df["psfFlux"] > 0) & (df["psfQfPerfect"] >= PS1_MIN_QF)]
    mag = -2.5 * (df["psfFlux"] / 3631).apply(math.log10)  # psfFlux is in Jy
    err = 1.0857 * df["psfFluxErr"] / df["psfFlux"]
    return [_point("ps1", t, PS1_BANDS.get(f, "?"), m, e) for t, f, m, e in zip(df["obsTime"], df["filterID"], mag, err)]


SURVEYS = {"ztf_dr": ztf, "ps1": ps1}


def run(ra, dec):
    out = {"points": [], "errors": {}}
    for name, fetch in SURVEYS.items():
        with _down_lock:
            if _down.get(name, 0) > time.time():
                out["errors"][name] = "skipped: it failed a moment ago"
                continue
        try:
            out["points"] += fetch(ra, dec)
        except Exception as e:
            out["errors"][name] = f"{type(e).__name__}: {e}"
            if isinstance(e, requests.Timeout) or (isinstance(e, requests.HTTPError) and e.response.status_code >= 500):
                with _down_lock:
                    _down[name] = time.time() + DOWN_SECONDS
    return out
