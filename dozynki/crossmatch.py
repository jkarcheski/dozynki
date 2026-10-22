"""Independent crossmatching, so labels don't inherit a broker's host association.

All catalogs are queried in parallel; any that fail or time out are reported
in `errors` rather than blocking the page.
"""

import io
import math
from concurrent.futures import ThreadPoolExecutor, wait

import numpy as np
import pandas as pd
import requests
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

from .config import DATALAB_ANON_TOKEN, DATALAB_QUERY, HTTP_TIMEOUT, XMATCH_RADIUS_ARCSEC

QUERY_TIMEOUT = 45
RUBIN_OBS_CODE = "X05"


def _clean(v):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return None
    if isinstance(v, np.generic):
        v = v.item()
        return None if isinstance(v, float) and not math.isfinite(v) else v
    if np.ma.is_masked(v):
        return None
    return v


def _records(df):
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict("records")]


def _sep_and_offset(ra0, dec0, ra, dec):
    """Separation (arcsec) and east/north offsets of (ra, dec) relative to (ra0, dec0)."""
    d_east = (ra - ra0) * math.cos(math.radians(dec0)) * 3600
    d_north = (dec - dec0) * 3600
    return math.hypot(d_east, d_north), d_east, d_north


def _with_offsets(rows, ra0, dec0, ra_key, dec_key):
    for r in rows:
        if r.get(ra_key) is None:
            continue
        r["sep"], r["d_east"], r["d_north"] = _sep_and_offset(ra0, dec0, r[ra_key], r[dec_key])
    return sorted([r for r in rows if "sep" in r], key=lambda r: r["sep"])


def legacy_survey(ra, dec, radius):
    sql = (
        "SELECT t.ls_id, t.ra, t.dec, t.type, t.mag_g, t.mag_r, t.mag_z, t.shape_r, t.shape_e1, t.shape_e2, "
        "p.z_phot_median, p.z_phot_l68, p.z_phot_u68, p.z_spec "
        "FROM ls_dr10.tractor AS t LEFT JOIN ls_dr10.photo_z AS p ON t.ls_id = p.ls_id "
        f"WHERE q3c_radial_query(t.ra, t.dec, {ra}, {dec}, {radius}/3600.)"
    )
    r = requests.get(
        DATALAB_QUERY,
        params={"sql": sql, "ofmt": "csv", "async": "false"},
        headers={"X-DL-AuthToken": DATALAB_ANON_TOKEN},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    rows = _with_offsets(_records(df), ra, dec, "ra", "dec")
    for row in rows:
        row["z_spec"] = row["z_spec"] if row.get("z_spec") is not None and row["z_spec"] > -1 else None
        row.update(_ellipse(row))
        if row.get("type") != "PSF" and row.get("dlr"):
            row["d_dlr"] = row["sep"] / row["dlr"]
    return rows


def _ellipse(row):
    """Tractor shape -> semi-axes and PA, plus the directional light radius toward the transient.

    DLR follows Gupta et al. (2016): the galaxy's elliptical radius along the
    direction to the transient. Tractor e1/e2 -> PA convention is approximate
    here, so treat d_dlr as a ranking aid, not a precise measurement.
    """
    r_e = row.get("shape_r") or 0
    if r_e <= 0:
        return {"a": None, "b": None, "pa_deg": None, "dlr": None}
    e1, e2 = row.get("shape_e1") or 0, row.get("shape_e2") or 0
    e = min(math.hypot(e1, e2), 0.99)
    ab = (1 - e) / (1 + e)
    theta = 0.5 * math.atan2(e2, e1)  # PA, east of north
    a, b = r_e, r_e * ab
    # direction from galaxy to transient, measured east of north
    psi = math.atan2(-row["d_east"], -row["d_north"])
    dphi = psi - theta
    dlr = a * b / math.hypot(a * math.sin(dphi), b * math.cos(dphi))
    return {"a": a, "b": b, "pa_deg": math.degrees(theta), "dlr": dlr}


VIZIER_CATALOGS = {
    "gaia": ("I/355/gaiadr3", ["Source", "RA_ICRS", "DE_ICRS", "Gmag", "BP-RP", "Plx", "e_Plx", "pmRA", "e_pmRA", "pmDE", "e_pmDE", "RUWE"]),
    "ps1": ("II/349/ps1", ["objID", "RAJ2000", "DEJ2000", "gmag", "rmag", "imag", "zmag"]),
    "milliquas": ("VII/294", ["Name", "RAJ2000", "DEJ2000", "Type", "z", "Qpct"]),
}


def vizier(key, ra, dec, radius):
    from astroquery.vizier import Vizier

    catalog, cols = VIZIER_CATALOGS[key]
    v = Vizier(columns=cols, row_limit=200, timeout=QUERY_TIMEOUT)
    res = v.query_region(SkyCoord(ra, dec, unit="deg"), radius=radius * u.arcsec, catalog=catalog)
    if not res:
        return []
    df = res[0].to_pandas()
    ra_key, dec_key = cols[1], cols[2]
    rows = _with_offsets(_records(df), ra, dec, ra_key, dec_key)
    if key == "gaia":
        for r in rows:
            plx_snr = (r["Plx"] / r["e_Plx"]) if r.get("Plx") is not None and r.get("e_Plx") else None
            pm_snr = None
            if r.get("pmRA") is not None and r.get("e_pmRA") and r.get("pmDE") is not None and r.get("e_pmDE"):
                pm_snr = math.hypot(r["pmRA"] / r["e_pmRA"], r["pmDE"] / r["e_pmDE"])
            r["plx_snr"], r["pm_snr"] = plx_snr, pm_snr
            r["likely_star"] = bool((plx_snr or 0) > 5 or (pm_snr or 0) > 5)
    return rows


def simbad(ra, dec, radius):
    from astroquery.simbad import Simbad

    s = Simbad()
    s.TIMEOUT = QUERY_TIMEOUT
    s.add_votable_fields("otype", "rvz_redshift")
    t = s.query_region(SkyCoord(ra, dec, unit="deg"), radius=radius * u.arcsec)
    if t is None or len(t) == 0:
        return []
    df = t.to_pandas()[["main_id", "ra", "dec", "otype", "rvz_redshift"]]
    return _with_offsets(_records(df), ra, dec, "ra", "dec")


def ned(ra, dec, radius):
    from astroquery.ipac.ned import Ned

    t = Ned.query_region(SkyCoord(ra, dec, unit="deg"), radius=radius * u.arcsec)
    if t is None or len(t) == 0:
        return []
    df = t.to_pandas()[["Object Name", "RA", "DEC", "Type", "Redshift"]]
    df.columns = ["name", "ra", "dec", "type", "z"]
    return _with_offsets(_records(df), ra, dec, "ra", "dec")


def skybot(ra, dec, mjd, radius):
    """Known solar-system objects near the position at the epoch of a detection."""
    from astroquery.imcce import Skybot

    try:
        t = Skybot.cone_search(
            SkyCoord(ra, dec, unit="deg"), radius * u.arcsec, Time(mjd, format="mjd"), location=RUBIN_OBS_CODE
        )
    except RuntimeError as e:  # raised when nothing is found
        if "No solar system object" in str(e):
            return []
        raise
    df = t.to_pandas()
    keep = [c for c in ["Number", "Name", "RA", "DEC", "V", "centerdist", "RA_rate", "DEC_rate"] if c in df]
    rows = _records(df[keep])
    for r in rows:
        r["sep"] = float(r.pop("centerdist", None) or 0)
    return sorted(rows, key=lambda r: r["sep"])


def context(ra, dec):
    c = SkyCoord(ra, dec, unit="deg")
    return {
        "gal_l": float(c.galactic.l.deg),
        "gal_b": float(c.galactic.b.deg),
        "ecl_lat": float(c.barycentrictrueecliptic.lat.deg),
    }


def _run_jobs(jobs, out):
    pool = ThreadPoolExecutor(max_workers=len(jobs))
    futures = {name: pool.submit(fn, *args) for name, (fn, args) in jobs.items()}
    wait(futures.values(), timeout=QUERY_TIMEOUT)
    for name, f in futures.items():
        if not f.done():
            out["errors"][name] = "timed out"
            out[name] = None
        elif f.exception():
            out["errors"][name] = f"{type(f.exception()).__name__}: {f.exception()}"
            out[name] = None
        else:
            out[name] = f.result()
    pool.shutdown(wait=False, cancel_futures=True)
    return out


def run(ra, dec, radius=XMATCH_RADIUS_ARCSEC):
    jobs = {
        "legacy_survey": (legacy_survey, (ra, dec, radius)),
        "gaia": (vizier, ("gaia", ra, dec, radius)),
        "ps1": (vizier, ("ps1", ra, dec, radius)),
        "milliquas": (vizier, ("milliquas", ra, dec, radius)),
        "simbad": (simbad, (ra, dec, radius)),
        "ned": (ned, (ra, dec, radius)),
    }
    out = {"ra": ra, "dec": dec, "radius_arcsec": radius, "context": context(ra, dec), "errors": {}}
    _run_jobs(jobs, out)
    galaxies = [g for g in out.get("legacy_survey") or [] if g.get("d_dlr") is not None]
    out["host_candidates"] = sorted(galaxies, key=lambda g: g["d_dlr"])[:5]
    return out


def run_skybot(ra, dec, detection_mjds):
    """SkyBoT at the first and latest detections: a mover shows up at one, rarely both.

    Kept separate from run() because the IMCCE service can be very slow.
    """
    jobs = {}
    for label, mjd in {"first": min(detection_mjds, default=None), "last": max(detection_mjds, default=None)}.items():
        if mjd is not None:
            jobs[label] = (skybot, (ra, dec, mjd, 60))
    out = {"epochs": {"first": min(detection_mjds, default=None), "last": max(detection_mjds, default=None)}, "errors": {}}
    return _run_jobs(jobs, out) if jobs else out
