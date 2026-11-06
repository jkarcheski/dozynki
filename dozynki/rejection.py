"""Rejection filters: rules that throw objects away, run as an ordered pipeline.

Unlike filters.py (which picks what gets sampled and served for labeling), these
are the cuts being *evaluated*: evaluate.py scores them against the blind labels.

Each rule sees a feature dict built from the cached payloads only (no network),
and returns True (reject), False (keep), or None (can't tell: missing data, so
the object is kept). Rules that read broker output (reliability, drb, ALeRCE)
are marked broker=True, since revealed labels may have been nudged by the same numbers.

A pipeline is a list of {"rule": name, **params}; its version is a hash of
that list, so results for different thresholds are stored side by side.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Callable

from . import assemble, crossmatch, db, details


def _rms_offset(dets):
    """rms of LSST detection positions about their mean, arcsec."""
    pos = [(p["ra"], p["dec"]) for p in dets if p.get("ra") is not None]
    if len(pos) < 2:
        return None
    ra0 = sum(r for r, _ in pos) / len(pos)
    dec0 = sum(d for _, d in pos) / len(pos)
    cosd = math.cos(math.radians(dec0))
    sq = [((r - ra0) * cosd * 3600) ** 2 + ((d - dec0) * 3600) ** 2 for r, d in pos]
    return math.sqrt(sum(sq) / len(sq))


def _median(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2


def _frac(values):
    vals = [bool(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _nearest(rows, keep=lambda r: True):
    seps = [r["sep"] for r in rows or [] if r.get("sep") is not None and keep(r)]
    return min(seps, default=None)


def _snr(value, err):
    return abs(value) / err if value is not None and err else None


def _likely_star(g):
    """A Gaia DR3 match with significant parallax or proper motion (the same cut crossmatch.py makes)."""
    plx = _snr(g.get("parallax"), g.get("parallax_error"))
    pm = (math.hypot(g["pmra"] / g["pmra_error"], g["pmdec"] / g["pmdec_error"])
          if None not in (g.get("pmra"), g.get("pmdec")) and g.get("pmra_error") and g.get("pmdec_error") else None)
    return (plx or 0) > 5 or (pm or 0) > 5


GALAXY_CATALOGS = ("sdss_gals", "2mass_xsc", "RC3")  # ANTARES catalogs whose entries are galaxies
COLOR_PAIR_DAYS = 1.0  # detections in two bands this close in time make a color
LSST_WAVELENGTH = {"u": 3671, "g": 4827, "r": 6223, "i": 7546, "z": 8691, "y": 9712}  # effective, Angstrom
GR_GAP = LSST_WAVELENGTH["r"] - LSST_WAVELENGTH["g"]


def _color(lsst):
    """Color of the latest pair of LSST difference-image detections in two bands within COLOR_PAIR_DAYS,
    as the g - r it would be: (blue mag - red mag) scaled by g-r's wavelength gap over the pair's, so
    i - z or r - z read on one scale with g - r. Returns (color, "i-z"), or (None, None)."""
    pos = [p for p in lsst if p.get("mag") is not None and p.get("band") in LSST_WAVELENGTH]
    pairs = []
    for a in pos:
        for b in pos:
            gap = LSST_WAVELENGTH[b["band"]] - LSST_WAVELENGTH[a["band"]]
            if gap > 0 and abs(a["mjd"] - b["mjd"]) <= COLOR_PAIR_DAYS:
                pairs.append((max(a["mjd"], b["mjd"]), (a["mag"] - b["mag"]) * GR_GAP / gap, f"{a['band']}-{b['band']}"))
    if not pairs:
        return None, None
    _, color, bands = max(pairs)
    return color, bands


def _template_ratio(lsst, det):
    """Median Rubin template flux at the source over the brightest difference flux: above 1, the
    change is smaller than what was already there (a variable source in the template)."""
    peak = max((p["diff_flux"] for p in lsst if (p.get("diff_flux") or 0) > 0), default=None)
    tmpl = _median(det[p["alert_id"]].get("template_flux") for p in lsst if p.get("alert_id") in det)
    return tmpl / peak if peak and tmpl is not None else None


def _rise(dets):
    """(days from the first positive detection to the brightest, fastest brightening in mag/day).

    The brightest is by difference flux on the common nJy scale, so the bands mix; the rate
    compares detections in one band of one survey at least half a day apart with mag_err < 0.2.
    """
    pos = sorted((p for p in dets if p.get("mag") is not None and p.get("diff_flux")), key=lambda p: p["mjd"])
    if not pos:
        return None, None
    peak = max(pos, key=lambda p: p["diff_flux"])
    rates = []
    for key in {(p["survey"], p.get("band")) for p in pos}:
        pts = [p for p in pos if (p["survey"], p.get("band")) == key and (p.get("mag_err") or 1) < 0.2]
        rates += [(a["mag"] - b["mag"]) / (b["mjd"] - a["mjd"]) for a, b in zip(pts, pts[1:]) if b["mjd"] - a["mjd"] >= 0.5]
    return peak["mjd"] - pos[0]["mjd"], max(rates, default=None)


def _dayobs(mjd):
    """Rubin's dayObs (the night's date, UTC-12h) as an MJD, so one night never splits at UTC midnight."""
    return math.floor(mjd - 0.5)


def _nightly_mags(dets):
    """{"survey band": [[dayObs MJD, median mag], ...] oldest first}, from detections with mag_err < 0.2
    (as _rise), so a single noisy point in a night doesn't decide whether it brightened."""
    nights = {}
    for p in dets:
        if p.get("mag") is not None and p.get("diff_flux") and (p.get("mag_err") or 1) < 0.2:
            nights.setdefault(f"{p['survey']} {p.get('band')}", {}).setdefault(_dayobs(p["mjd"]), []).append(p["mag"])
    return {k: [[n, _median(m)] for n, m in sorted(v.items())] for k, v in nights.items()}


def _not_brighter(f, brightening, min_delta):
    """Reject unless a band detected on the newest night brightened by more than min_delta mag, where
    brightening(nights) gives that band's change (None: can't tell). None if no band can tell."""
    bands = f["nightly_mags"].values()
    newest = max((nights[-1][0] for nights in bands), default=None)
    changes = [brightening(nights) for nights in bands if nights[-1][0] == newest]
    changes = [c for c in changes if c is not None]
    return None if not changes else all(c <= min_delta for c in changes)


def _since(nights, mjd):
    """Magnitudes gained from the last night on or before dayObs mjd to the newest night (None without both)."""
    before = [m for n, m in nights if n <= mjd]
    return before[-1] - nights[-1][1] if before and nights[-1][0] > mjd else None


def _rising_over(nights, n):
    """The smallest night-to-night brightening over the last n nights (None with fewer than n + 1 nights)."""
    last = nights[-(n + 1):]
    return min(a[1] - b[1] for a, b in zip(last, last[1:])) if n >= 1 and len(last) == n + 1 else None


ARCHIVAL_MIN_POINTS = 10  # ZTF data-release points in one band for its scatter to mean anything
PS1_MIN_POINTS = 3  # Pan-STARRS1 detections in a band for its median to stand against a stray one
HOST_MAG_LIMIT = 25.0  # fainter Legacy Survey magnitudes are noise (DR10 reaches about 24.5 in r)
HOST_ZPHOT_MAX_WIDTH = 1.0  # a photo-z whose 68% interval is wider says nothing


def _archival(raw):
    """(points by survey, whether each survey answered) from the cached archival search; None if not fetched."""
    hit = (raw.get("archival") or {}).get("data")
    if hit is None:
        return None, {}
    by = {}
    for p in hit["points"]:
        by.setdefault(p["survey"], []).append(p)
    return by, {s: s not in hit["errors"] for s in ("ps1", "ztf_dr")}


def _ps1_mags(ps1, band):
    """Pan-STARRS1 magnitudes in one band, or [] if fewer than PS1_MIN_POINTS (one stray detection isn't a source)."""
    mags = [q["mag"] for q in ps1 if q["band"] == band]
    return mags if len(mags) >= PS1_MIN_POINTS else []


def _ps1_brightening(lsst, ps1):
    """Pan-STARRS1's median magnitude minus LSST's science (total) magnitude now, in the same band: how much
    brighter it is than in 2009-2014. "Now" is the median over the latest night with an LSST detection in a
    band Pan-STARRS1 has enough of, so neither side rests on one point. None without such a band."""
    for p in sorted(lsst, key=lambda p: p["mjd"], reverse=True):
        old = _ps1_mags(ps1, p.get("band"))
        if p.get("sci_mag") is None or not old:
            continue
        night = [q["sci_mag"] for q in lsst if q.get("band") == p["band"] and q.get("sci_mag") is not None
                 and math.floor(q["mjd"]) == math.floor(p["mjd"])]
        return _median(old) - _median(night)
    return None


def _excess_rms(points):
    """Largest scatter beyond the errors, in any band with ARCHIVAL_MIN_POINTS points (mag). The scatter is
    1.4826 x the median absolute deviation, so a few bad epochs don't read as variability."""
    out = []
    for band in {p["band"] for p in points}:
        mags = [p["mag"] for p in points if p["band"] == band]
        if len(mags) >= ARCHIVAL_MIN_POINTS:
            med = _median(mags)
            scatter = 1.4826 * _median(abs(m - med) for m in mags)
            err = _median(p["mag_err"] for p in points if p["band"] == band) or 0
            out.append(math.sqrt(max(0.0, scatter ** 2 - err ** 2)))
    return max(out, default=None)


def _host_props(host):
    """The Legacy Survey host's photo-z, r magnitude and half-light radius (arcsec), each None where it's
    a placeholder (-99 photo-z), unconstrained (photo-z interval wider than HOST_ZPHOT_MAX_WIDTH) or noise
    (fainter than HOST_MAG_LIMIT)."""
    if not host:
        return None, None, None
    z, lo, hi = host.get("z_phot_median"), host.get("z_phot_l68"), host.get("z_phot_u68")
    z = z if z is not None and z >= 0 and lo is not None and hi is not None and lo >= 0 and hi - lo <= HOST_ZPHOT_MAX_WIDTH else None
    mag = host.get("mag_r")
    mag = mag if mag is not None and math.isfinite(mag) and mag <= HOST_MAG_LIMIT else None
    return z, mag, host.get("shape_r")


def _tns(object_id, ant, cats):
    """(ANTARES lists a TNS match, it has a classification): the class from the cached TNS matches or
    ANTARES's catalog matches; None while neither has it."""
    if "tns_public_objects" not in (ant.get("catalogs") or []):
        return False, False
    matches = (db.cache_get(object_id, "tns") or {}).get("data") or (cats or {}).get("tns_public_objects") or []
    if any("type" in m for m in matches):
        return True, any(m.get("type") for m in matches)
    return True, None


def features(object_id):
    """Flat feature dict for one object from its cached payloads, or None if not fetched."""
    raw = assemble._gather(object_id)
    if raw["antares"] is None:
        return None
    ev = assemble.evidence(object_id, raw)
    op = assemble.opinions(raw)
    dets = [p for p in ev["points"] if p["kind"] == "detection"]
    lsst = [p for p in dets if p["survey"] == "lsst"]
    xm = ev["xmatch"] or {}
    rel = op["reliability"]
    stamp = ((op["alerce"] or {}).get("lsst") or {})
    # newest stamp classifier version (names sort by date suffix)
    stamp = stamp[max(stamp)] if stamp else {}
    top_host = (xm.get("host_candidates") or [None])[0] or {}
    ant = raw["antares"]["data"]
    # ANTARES's own catalog matches (about 1 arcsec), not our slower crossmatch; None until fetched
    cats = details.catalogs(object_id)
    sky = crossmatch.context(ev["ra"], ev["dec"])
    hosts = details.hosts(object_id)
    arch, answered = _archival(raw)
    host_z, host_mag, host_size = _host_props(hosts[0] if hosts else None)
    tns_listed, tns_classified = _tns(object_id, ant, cats)
    rise_days, rise_rate = _rise(dets)
    det = details.get(object_id, ant)
    lsst_details = [det[p["alert_id"]] for p in lsst if p.get("alert_id") in det] if det is not None else []
    color, color_bands = _color(lsst)
    ztf_ps1 = [p for p in dets if p["survey"] == "ztf" and p.get("distpsnr1") is not None and p["distpsnr1"] >= 0]
    nearest_ps1 = min(ztf_ps1, key=lambda p: p["distpsnr1"]) if ztf_ps1 else {}
    return {
        "n_det": len(dets),
        "n_lsst_det": len(lsst),
        "n_nights": len({math.floor(p["mjd"]) for p in dets if p.get("mjd") is not None}),
        "span_days": (max(p["mjd"] for p in dets) - min(p["mjd"] for p in dets)) if dets else None,
        "frac_negative": sum(p.get("is_negative", False) for p in lsst) / len(lsst) if lsst else None,
        "frac_pixel_flagged": sum(bool(p.get("pixel_flags")) for p in lsst) / len(lsst) if lsst else None,
        "pos_rms_arcsec": _rms_offset(lsst),
        "sso_associated": bool(ev["sso_associations"]["lsst_ssObjectId"] or ev["sso_associations"]["ztf_ssnamenr"]),
        "gaia_star_sep": None if cats is None else _nearest(cats.get("gaia_dr3_gaia_source"), _likely_star),
        "gaia_sep": None if cats is None else _nearest(cats.get("gaia_dr3_gaia_source")),
        "milliquas_sep": None if cats is None else _nearest(cats.get("milliquas")),
        "host_d_dlr": top_host.get("d_dlr"),
        # Legacy Survey host (lowest DLR) offset; None also when there is no galaxy nearby
        "ls_hosts_fetched": hosts is not None,
        "ls_host_sep": hosts[0]["sep"] if hosts else None,
        # ANTARES's galaxy-catalog matches (about 1 arcsec)
        "antares_galaxy_sep": None if cats is None else min((r["sep"] for c in GALAXY_CATALOGS for r in cats.get(c) or []
                                                              if r.get("sep") is not None), default=None),
        "dec": ev["dec"],
        # difference magnitudes of positive LSST and ZTF detections
        "mag_peak": min((p["mag"] for p in dets if p.get("mag") is not None), default=None),
        "mag_latest": max(((p["mjd"], p["mag"]) for p in dets if p.get("mag") is not None), default=(None, None))[1],
        # archival total brightness at the position (archival.py): None until searched or if the survey failed
        "ps1_n": len(arch.get("ps1", [])) if arch is not None and answered["ps1"] else None,
        "ps1_brightening": _ps1_brightening(lsst, arch.get("ps1", [])) if arch is not None and answered["ps1"] else None,
        "ps1_median_mag_r": _median(_ps1_mags(arch.get("ps1", []), "r")) if arch is not None and answered["ps1"] else None,
        "host_z_phot": host_z,
        "host_mag_r": host_mag,
        "host_size_arcsec": host_size,
        "ztf_dr_excess_rms": _excess_rms(arch.get("ztf_dr", [])) if arch is not None and answered["ztf_dr"] else None,
        "tns_listed": tns_listed,
        "tns_classified": tns_classified,
        "color_gr_equiv": color,
        "color_bands": color_bands,
        "template_ratio": _template_ratio(lsst, det or {}),
        "rise_days": rise_days,
        "rise_rate_mag_per_day": rise_rate,
        "nightly_mags": _nightly_mags(dets),
        "abs_gal_b": abs(sky["gal_b"]),
        "abs_ecl_lat": abs(sky["ecl_lat"]),
        # Rubin pipeline measurements per LSST detection (details.py); None before they're fetched
        "extendedness_median": _median(d["extendedness"] for d in lsst_details),
        "frac_dipole": _frac(d["is_dipole"] for d in lsst_details),
        "any_glint": any(d["glint_trail"] for d in lsst_details) if lsst_details else None,
        "psf_chi2_red_median": _median(d["psf_chi2_red"] for d in lsst_details),
        "size_ratio_median": _median(d["size_ratio"] for d in lsst_details),
        "frac_measurement_flagged": _frac(d["measurement_flagged"] for d in lsst_details),
        "snr_max": max((p["snr"] for p in lsst if p.get("snr") is not None), default=None),
        "trail_max_arcsec": max((p["trail_length"] for p in lsst if p.get("trail_length") is not None), default=None),
        # ZTF's nearest Pan-STARRS1 source and its star/galaxy score (1 star, 0 galaxy)
        "ztf_ps1_dist": nearest_ps1.get("distpsnr1"),
        "ztf_ps1_sgscore": nearest_ps1.get("sgscore1"),
        # ANTARES's own catalog matches and filter tags on the locus
        "antares_bright_star": "bright_guide_star_cat" in (ant.get("catalogs") or []),
        "antares_gaia_variable": "gaia_dr3_variability" in (ant.get("catalogs") or []),
        "scimma_quality": "lsst_scimma_quality_transient" in (ant.get("tags") or []),
        "lsst_reliability_max": max(rel["lsst_per_alert"], default=None),
        "lsst_reliability_mean": rel["lsst_mean"],
        "ztf_drb_mean": rel["ztf_drb_mean"],
        "alerce_stamp_top": stamp.get("top"),
        "alerce_stamp_probs": stamp.get("probs") or {},
    }


def _antares_nuclear(f, max_sep):
    ps1_galaxy = f["ztf_ps1_sgscore"] is not None and f["ztf_ps1_sgscore"] < 0.5 and _within(f["ztf_ps1_dist"], max_sep)
    return _within(f["antares_galaxy_sep"], max_sep) or ps1_galaxy


@dataclass
class Rule:
    name: str
    description: str
    defaults: dict
    test: Callable  # (features, **params) -> True reject / False keep / None unknown
    broker: bool = False


def _below(value, threshold):
    return None if value is None else value < threshold


def _within(sep, max_sep):
    return sep is not None and sep <= max_sep


RULES = {r.name: r for r in [
    Rule("sso_association", "Rubin ssObjectId or ZTF ssnamenr on any detection", {},
         lambda f: f["sso_associated"]),
    Rule("gaia_star", "Gaia DR3 source with significant parallax or proper motion within max_sep arcsec "
         "(ANTARES's match, which reaches about 1 arcsec)",
         {"max_sep": 1.0}, lambda f, max_sep: _within(f["gaia_star_sep"], max_sep)),
    Rule("milliquas_agn", "Milliquas QSO/AGN within max_sep arcsec (ANTARES's match, which reaches about 1 arcsec)",
         {"max_sep": 1.0},
         lambda f, max_sep: _within(f["milliquas_sep"], max_sep)),
    Rule("min_nights", "detected on fewer than n distinct nights", {"n": 2},
         lambda f, n: f["n_nights"] < n),
    Rule("min_detections", "fewer than n detections (LSST + ZTF, no upper limits)", {"n": 2},
         lambda f, n: f["n_det"] < n),
    Rule("min_lsst_detections", "fewer than n LSST detections", {"n": 2},
         lambda f, n: f["n_lsst_det"] < n),
    Rule("min_span", "first to last detection spans less than days", {"days": 1.0},
         lambda f, days: None if f["span_days"] is None else f["span_days"] < days),
    Rule("galactic_plane", "within min_b degrees of the Galactic plane", {"min_b": 10.0},
         lambda f, min_b: _below(f["abs_gal_b"], min_b)),
    Rule("ecliptic", "within min_beta degrees of the ecliptic, where most asteroids are", {"min_beta": 10.0},
         lambda f, min_beta: _below(f["abs_ecl_lat"], min_beta)),
    Rule("negative_flux", "more than max_frac of LSST detections are negative difference flux",
         {"max_frac": 0.5}, lambda f, max_frac: None if f["frac_negative"] is None else f["frac_negative"] > max_frac),
    Rule("position_scatter", "LSST detection positions scatter by more than max_rms arcsec",
         {"max_rms": 0.5}, lambda f, max_rms: None if f["pos_rms_arcsec"] is None else f["pos_rms_arcsec"] > max_rms),
    Rule("extended_diff", "median extendedness of the LSST difference-image detections above max_ext",
         {"max_ext": 0.9}, lambda f, max_ext: None if f["extendedness_median"] is None else f["extendedness_median"] > max_ext),
    Rule("dipole", "more than max_frac of LSST detections flagged as dipoles", {"max_frac": 0.5},
         lambda f, max_frac: None if f["frac_dipole"] is None else f["frac_dipole"] > max_frac),
    Rule("glint", "an LSST detection flagged as a satellite glint trail", {},
         lambda f: f["any_glint"]),
    Rule("bad_psf_fit", "median reduced chi2 of the LSST PSF fits above max_chi2", {"max_chi2": 5.0},
         lambda f, max_chi2: None if f["psf_chi2_red_median"] is None else f["psf_chi2_red_median"] > max_chi2),
    Rule("sharp", "LSST detections smaller than the PSF (median second-moment size ratio below min_ratio): cosmic rays, hot pixels",
         {"min_ratio": 0.25}, lambda f, min_ratio: _below(f["size_ratio_median"], min_ratio)),
    Rule("oversized", "LSST detections larger than the PSF (median second-moment size ratio above max_ratio)",
         {"max_ratio": 2.0}, lambda f, max_ratio: None if f["size_ratio_median"] is None else f["size_ratio_median"] > max_ratio),
    Rule("measurement_flags", "more than max_frac of LSST detections have a failed PSF, centroid, shape or aperture measurement",
         {"max_frac": 0.5}, lambda f, max_frac: None if f["frac_measurement_flagged"] is None else f["frac_measurement_flagged"] > max_frac),
    Rule("low_snr", "brightest LSST detection has SNR below min_snr", {"min_snr": 7.0},
         lambda f, min_snr: _below(f["snr_max"], min_snr)),
    Rule("trailed", "an LSST detection trailed longer than max_len arcsec (a fast mover)", {"max_len": 2.0},
         lambda f, max_len: None if f["trail_max_arcsec"] is None else f["trail_max_arcsec"] > max_len),
    Rule("ztf_ps1_star", "ZTF's nearest PS1 source is within max_dist arcsec with star/galaxy score at least min_sg",
         {"max_dist": 1.0, "min_sg": 0.5},
         lambda f, max_dist, min_sg: None if f["ztf_ps1_dist"] is None or f["ztf_ps1_sgscore"] is None
         else f["ztf_ps1_dist"] <= max_dist and f["ztf_ps1_sgscore"] >= min_sg),
    Rule("declination", "outside min_dec to max_dec degrees (what you can follow up)", {"min_dec": -35.0, "max_dec": 90.0},
         lambda f, min_dec, max_dec: not min_dec <= f["dec"] <= max_dec),
    Rule("faint", "brightest detection fainter than max_mag (difference magnitude)", {"max_mag": 22.0},
         lambda f, max_mag: None if f["mag_peak"] is None else f["mag_peak"] > max_mag),
    Rule("faint_now", "latest detection fainter than max_mag (difference magnitude): too faint to follow up now",
         {"max_mag": 22.0}, lambda f, max_mag: None if f["mag_latest"] is None else f["mag_latest"] > max_mag),
    Rule("bright", "brightest detection brighter than min_mag (difference magnitude)", {"min_mag": 15.0},
         lambda f, min_mag: _below(f["mag_peak"], min_mag)),
    Rule("red", "color redder than max_gr, as g - r: the latest LSST detections in any two bands within a day, "
         "scaled to g - r's wavelength gap (difference mags)",
         {"max_gr": 1.0}, lambda f, max_gr: None if f["color_gr_equiv"] is None else f["color_gr_equiv"] > max_gr),
    Rule("blue", "color bluer than min_gr, as g - r: the latest LSST detections in any two bands within a day, "
         "scaled to g - r's wavelength gap (difference mags)",
         {"min_gr": -0.5}, lambda f, min_gr: _below(f["color_gr_equiv"], min_gr)),
    Rule("in_template", "Rubin's template flux at the source is more than max_ratio times the brightest difference "
         "flux: a source already there that varies (AGN, variable stars; also transients on a bright galaxy core)",
         {"max_ratio": 3.0}, lambda f, max_ratio: None if f["template_ratio"] is None else f["template_ratio"] > max_ratio),
    Rule("long_history", "first to last detection spans more than max_days (AGN and variables keep alerting)",
         {"max_days": 100.0}, lambda f, max_days: None if f["span_days"] is None else f["span_days"] > max_days),
    Rule("long_rise", "more than max_days from the first detection to the brightest (slow risers: AGN, variables)",
         {"max_days": 50.0}, lambda f, max_days: None if f["rise_days"] is None else f["rise_days"] > max_days),
    Rule("fast_rise", "brightens faster than max_rate mag/day between detections in one band (flares)",
         {"max_rate": 1.0}, lambda f, max_rate: None if f["rise_rate_mag_per_day"] is None else f["rise_rate_mag_per_day"] > max_rate),
    Rule("not_fast_rise", "brightens no faster than min_rate mag/day, or has no rate (fewer than two good detections "
         "in one band half a day apart): keeps only fast risers (young SNe, fast transients)",
         {"min_rate": 0.2}, lambda f, min_rate: f["rise_rate_mag_per_day"] is None or f["rise_rate_mag_per_day"] < min_rate),
    Rule("not_brighter_since", "the newest night is not more than min_delta mag brighter than the last night on or before "
         "dayObs mjd, in the same band (nightly medians, difference mags; kept if no band has both)",
         {"mjd": 61350.0, "min_delta": 0.0},
         lambda f, mjd, min_delta: _not_brighter(f, lambda nights: _since(nights, mjd), min_delta)),
    Rule("not_rising", "did not brighten by more than min_delta mag on each of the last n nights it was detected, in "
         "one band (nightly medians, difference mags; kept with fewer than n + 1 nights in every band)",
         {"n": 1, "min_delta": 0.0},
         lambda f, n, min_delta: _not_brighter(f, lambda nights: _rising_over(nights, n), min_delta)),
    Rule("nuclear", "within max_sep arcsec of its Legacy Survey host galaxy's center (AGN, nuclear variability)",
         {"max_sep": 0.5}, lambda f, max_sep: _within(f["ls_host_sep"], max_sep) if f["ls_hosts_fetched"] else None),
    Rule("not_nuclear", "not within max_sep arcsec of a Legacy Survey host galaxy's center, or no host (keeps only nuclear: TDEs, AGN)",
         {"max_sep": 0.5}, lambda f, max_sep: not _within(f["ls_host_sep"], max_sep) if f["ls_hosts_fetched"] else None),
    Rule("antares_nuclear", "on a galaxy: ANTARES matched SDSS galaxies, 2MASS extended sources or RC3 within max_sep arcsec "
         "(it matches within about 1), or ZTF's nearest PS1 source is a galaxy (score < 0.5) that close",
         {"max_sep": 1.0}, lambda f, max_sep: _antares_nuclear(f, max_sep)),
    Rule("ps1_persistent", "Pan-STARRS1 (2009-2014) detected a source at the position (1.5 arcsec) in at least min_n "
         "single exposures: something was already there (stars, AGN, galaxy cores)",
         {"min_n": 3}, lambda f, min_n: None if f["ps1_n"] is None else f["ps1_n"] >= min_n),
    Rule("no_brighter_than_ps1", "the latest LSST total magnitude is less than min_delta brighter than Pan-STARRS1's "
         "median in the same band: it looks as it did in 2009-2014", {"min_delta": 0.5},
         lambda f, min_delta: None if f["ps1_brightening"] is None else f["ps1_brightening"] < min_delta),
    Rule("ztf_archival_variable", "ZTF data-release light curve (2018 on) scatters by more than max_rms mag beyond its "
         "errors in some band: a long-lived variable", {"max_rms": 0.1},
         lambda f, max_rms: None if f["ztf_dr_excess_rms"] is None else f["ztf_dr_excess_rms"] > max_rms),
    Rule("tns_known", "ANTARES matched a TNS object: already reported", {}, lambda f: f["tns_listed"]),
    Rule("tns_classified", "its TNS match has a classification: already typed", {}, lambda f: f["tns_classified"]),
    Rule("not_tns_known", "ANTARES matched no TNS object (keeps only reported ones)", {}, lambda f: not f["tns_listed"]),
    Rule("antares_bright_star", "ANTARES matched the locus to its bright guide star catalog", {},
         lambda f: f["antares_bright_star"]),
    Rule("antares_gaia_variable", "ANTARES matched the locus to the Gaia DR3 variability catalog", {},
         lambda f: f["antares_gaia_variable"]),
    Rule("scimma_quality", "ANTARES's SCIMMA quality-transient filter did not tag the locus", {},
         lambda f: not f["scimma_quality"], broker=True),
    Rule("lsst_reliability", "highest per-alert LSST reliability below min_rel", {"min_rel": 0.5},
         lambda f, min_rel: _below(f["lsst_reliability_max"], min_rel), broker=True),
    Rule("alerce_stamp_agn", "ALeRCE LSST stamp classifier P(AGN) above max_p", {"max_p": 0.5},
         lambda f, max_p: None if "AGN" not in f["alerce_stamp_probs"] else f["alerce_stamp_probs"]["AGN"] > max_p,
         broker=True),
    Rule("alerce_stamp_bogus", "ALeRCE LSST stamp classifier P(bogus) above max_p", {"max_p": 0.5},
         lambda f, max_p: None if "bogus" not in f["alerce_stamp_probs"] else f["alerce_stamp_probs"]["bogus"] > max_p,
         broker=True),
]}

DEFAULT_PIPELINE = [
    {"rule": "sso_association"},
    {"rule": "gaia_star"},
    {"rule": "milliquas_agn"},
    {"rule": "negative_flux"},
    {"rule": "position_scatter"},
    {"rule": "min_nights"},
    {"rule": "lsst_reliability"},
]


# Rules ANTARES's locus listing can decide, so a whole night can be filtered without
# fetching every object (nightrun.py). Some become ES clauses that keep what the rule
# keeps; the rest run here on listing_features(). Every other rule waits for the full fetch.
LISTING_ES = {
    "min_detections": lambda n: [{"range": {"properties.num_mag_values": {"gte": n}}}],
    # LSST only: ZTF fills ssnamenr with the string "null" on nearly every locus, so ZTF's
    # associations wait for the full fetch, which reads them per alert
    "sso_association": lambda: [{"bool": {"must_not": {"exists": {"field": "properties.survey.lsst.ss_object_id"}}}}],
    "antares_bright_star": lambda: [{"bool": {"must_not": {"term": {"catalogs": "bright_guide_star_cat"}}}}],
    "antares_gaia_variable": lambda: [{"bool": {"must_not": {"term": {"catalogs": "gaia_dr3_variability"}}}}],
    "scimma_quality": lambda: [{"term": {"tags": "lsst_scimma_quality_transient"}}],
    # ANTARES lists the catalogs it matched within about 1 arcsec, so a cut at least that wide is
    # the same as its match; a narrower one needs the separation, from the full fetch (None: run later)
    "milliquas_agn": lambda max_sep: [{"bool": {"must_not": {"term": {"catalogs": "milliquas"}}}}] if max_sep >= 1 else None,
    "tns_known": lambda: [{"bool": {"must_not": {"term": {"catalogs": "tns_public_objects"}}}}],
    "not_tns_known": lambda: [{"term": {"catalogs": "tns_public_objects"}}],
    "declination": lambda min_dec, max_dec: [{"range": {"dec": {"gte": min_dec, "lte": max_dec}}}],
    # the catalog half only: ZTF's PS1 galaxies wait for the full fetch, which applies the whole rule
    "antares_nuclear": lambda max_sep: [{"bool": {"must_not": {"terms": {"catalogs": list(GALAXY_CATALOGS)}}}}] if max_sep >= 1 else None,
}
# Not the magnitude cuts: ANTARES's brightest/newest alert magnitudes mix ZTF and LSST total
# magnitudes (18.25 where the LSST difference magnitude is 23.05), so those wait for the full fetch.
LISTING_LOCAL = {"min_span", "long_history", "galactic_plane", "ecliptic"}


def listing_split(pipeline):
    """(ES clauses, steps to run on listing_features, steps that need the full fetch)."""
    es, local, later = [], [], []
    for step in resolve(pipeline):
        params = {k: v for k, v in step.items() if k != "rule"}
        clauses = LISTING_ES[step["rule"]](**params) if step["rule"] in LISTING_ES else None
        if clauses is not None:
            es += clauses
        elif step["rule"] in LISTING_LOCAL:
            local.append(step)
        else:
            later.append(step)
    return es, local, later


def listing_features(summaries):
    """The features LISTING_LOCAL rules read, for many ANTARES listing summaries at once."""
    from astropy.coordinates import SkyCoord

    if not summaries:
        return []
    c = SkyCoord([s["ra"] for s in summaries], [s["dec"] for s in summaries], unit="deg")
    gal_b, ecl_lat = c.galactic.b.deg, c.barycentrictrueecliptic.lat.deg
    return [{"n_det": s["n_det"],
             "span_days": s["last_mjd"] - s["first_mjd"] if s["first_mjd"] is not None and s["last_mjd"] is not None else None,
             "abs_gal_b": abs(float(b)), "abs_ecl_lat": abs(float(e))}
            for s, b, e in zip(summaries, gal_b, ecl_lat)]


def resolve(pipeline):
    """Fill in default params; checks every rule and param name."""
    out = []
    for step in pipeline:
        rule = RULES[step["rule"]]
        params = {k: v for k, v in step.items() if k != "rule"}
        unknown = set(params) - set(rule.defaults)
        if unknown:
            raise ValueError(f"{rule.name}: unknown params {sorted(unknown)}")
        out.append({"rule": rule.name, **rule.defaults, **params})
    return out


def version(pipeline):
    return hashlib.sha1(json.dumps(resolve(pipeline), sort_keys=True).encode()).hexdigest()[:10]


def step_key(step):
    """Unique name for a step, so the same rule at two thresholds stays distinct."""
    params = {k: v for k, v in step.items() if k != "rule"}
    return step["rule"] + "".join(f"[{k}={v}]" for k, v in sorted(params.items()))


def apply(feats, pipeline):
    """{step_key: decision} for every step (not just up to the first rejection),
    so dropping or reordering a rule can be evaluated without re-running."""
    out = {}
    for step in resolve(pipeline):
        params = {k: v for k, v in step.items() if k != "rule"}
        out[step_key(step)] = RULES[step["rule"]].test(feats, **params)
    return out


def first_rejection(decisions, keys=None):
    """The step that rejects the object in pipeline order, or None if it survives."""
    for k in keys or decisions:
        if decisions[k]:
            return k
    return None


_features_memo = {}  # object_id -> (newest cache fetched_at, features)


def features_for(object_ids):
    """features() for many objects, recomputed only when an object's cache has changed."""
    stamps = {r["object_id"]: r["t"] for r in db.query("SELECT object_id, MAX(fetched_at) t FROM cache GROUP BY object_id")}
    out = {}
    for oid in object_ids:
        hit = _features_memo.get(oid)
        if hit is None or hit[0] != stamps.get(oid):
            hit = _features_memo[oid] = (stamps.get(oid), features(oid))
        out[oid] = hit[1]
    return out


def run(object_ids, pipeline=DEFAULT_PIPELINE, save=True):
    """Apply a pipeline; with save, store every decision under its version."""
    pipeline = resolve(pipeline)
    ver = version(pipeline)
    if save:
        db.save_pipeline(ver, pipeline)
    results = {}
    for oid, feats in features_for(object_ids).items():
        if feats is None:
            continue
        decisions = apply(feats, pipeline)
        if save:
            db.save_rejections(oid, ver, decisions)
        results[oid] = {"features": feats, "decisions": decisions}
    return ver, results
