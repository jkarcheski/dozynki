"""ANTARES REST client: random LSST sampling, locus + alert history, thumbnails."""

import json
import math

import requests

from ..config import ANTARES_API, HTTP_TIMEOUT

LSST_ZP_NJY = 31.4  # AB zero point for fluxes in nJy
ZTF_FID_BAND = {1: "g", 2: "r", 3: "i"}
# ant_survey codes seen in the API
SURVEY_ZTF_CAND, SURVEY_ZTF_ULIM, SURVEY_LSST = 1, 2, 4

_session = requests.Session()


def _get(url, params=None):
    r = _session.get(url, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _search(es_query, limit):
    return _get(
        f"{ANTARES_API}/loci",
        {"elasticsearch_query[locus_listing]": json.dumps(es_query), "page[limit]": limit},
    )


def random_lsst_loci(n, seed, extra_filters=()):
    """Uniform random sample over ANTARES loci that have an LSST diaObject.

    Note the unit is the *object* (locus), not the alert: objects with many
    alerts are not up-weighted. extra_filters are ES clauses (see filters.py).
    """
    filters = [{"exists": {"field": "properties.survey.lsst.dia_object_id"}}, *extra_filters]
    es_query = {
        "query": {
            "function_score": {
                "query": {"bool": {"filter": filters}},
                "random_score": {"seed": seed, "field": "_seq_no"},
                "boost_mode": "replace",
            }
        }
    }
    resp = _search(es_query, n)
    return es_query, [_listing_summary(x) for x in resp.get("data", [])]


def night_lsst_loci(mjd_lo, mjd_hi, seed, offset, n, extra_filters=()):
    """A batch of loci with an LSST diaObject whose first alert falls in [mjd_lo, mjd_hi).

    The order is random but fixed by seed: the score hashes locus_id, which (unlike
    _seq_no) does not change when a locus gets new alerts, so batches taken at
    increasing offsets with one seed never repeat. Returns (es_query, n_matching, loci);
    ANTARES counts and pages only the first 10,000 matches.
    """
    filters = [{"exists": {"field": "properties.survey.lsst.dia_object_id"}},
               {"range": {"properties.oldest_alert_observation_time": {"gte": mjd_lo, "lt": mjd_hi}}}, *extra_filters]
    es_query = {
        "query": {
            "function_score": {
                "query": {"bool": {"filter": filters}},
                "random_score": {"seed": seed, "field": "locus_id"},
                "boost_mode": "replace",
            }
        }
    }
    resp = _get(f"{ANTARES_API}/loci", {"elasticsearch_query[locus_listing]": json.dumps(es_query),
                                         "page[limit]": n, "page[offset]": offset})
    return es_query, resp.get("meta", {}).get("count", 0), [_listing_summary(x) for x in resp.get("data", [])]


def count_loci(filters):
    """How many loci match a bool filter; ANTARES stops counting at 10,000."""
    es_query = {"query": {"bool": {"filter": filters}}}
    return _get(f"{ANTARES_API}/loci", {"elasticsearch_query[locus_listing]": json.dumps(es_query),
                                         "page[limit]": 1}).get("meta", {}).get("count", 0)


def list_loci(filters, offset, n):
    """One page of loci matching a bool filter, as listing summaries (offset + n within 10,000)."""
    es_query = {"query": {"bool": {"filter": filters}}}
    resp = _get(f"{ANTARES_API}/loci", {"elasticsearch_query[locus_listing]": json.dumps(es_query),
                                         "page[limit]": n, "page[offset]": offset})
    return [_listing_summary(x) for x in resp.get("data", [])]


def locus_summary(locus_id):
    return _listing_summary(_get(f"{ANTARES_API}/loci/{locus_id}")["data"])


def locus_for_dia_object(dia_object_id):
    es_query = {"query": {"term": {"properties.survey.lsst.dia_object_id": str(dia_object_id)}}}
    data = _search(es_query, 1).get("data", [])
    return _listing_summary(data[0]) if data else None


def _listing_summary(x):
    attr = x["attributes"]
    survey = attr["properties"].get("survey", {})
    dia_ids = survey.get("lsst", {}).get("dia_object_id", [])
    return {
        "locus_id": x["id"],
        "ra": attr["ra"],
        "dec": attr["dec"],
        "dia_object_id": dia_ids[0] if dia_ids else None,
        "num_alerts": attr["properties"].get("num_alerts"),
        "n_det": attr["properties"].get("num_mag_values"),
        "first_mjd": attr["properties"].get("oldest_alert_observation_time"),
        "last_mjd": attr["properties"].get("newest_alert_observation_time"),
        "tags": attr.get("tags", []),
        "catalogs": attr.get("catalogs", []),
        "ss_ids": survey.get("lsst", {}).get("ss_object_id", []) + survey.get("ztf", {}).get("ssnamenr", []),
    }


def get_locus(locus_id):
    """Locus attributes plus the full, normalized alert history."""
    locus = _get(f"{ANTARES_API}/loci/{locus_id}")["data"]
    attr = locus["attributes"]
    props = attr.get("properties", {})
    alerts = _all_alerts(locus_id)
    return {
        "locus_id": locus_id,
        "ra": attr["ra"],
        "dec": attr["dec"],
        "survey": props.get("survey", {}),
        "ztf_object_id": props.get("ztf_object_id"),
        "tags": attr.get("tags", []),
        "catalogs": attr.get("catalogs", []),
        "catalog_matches": attr.get("catalog_matches", []),
        "num_alerts": props.get("num_alerts"),
        "points": [p for p in (_normalize(a) for a in alerts) if p],
        "lsst_details": _details(alerts),
    }


def tns_matches(locus_id):
    """TNS entries ANTARES has matched to the locus (its copy of TNS public objects).

    reported is when TNS received the discovery report (UTC); type is the TNS
    classification, empty until someone classifies the object.
    """
    data = _get(f"{ANTARES_API}/loci/{locus_id}/catalog-matches").get("data", [])
    out = []
    for m in data:
        if m.get("meta", {}).get("catalog_name") != "tns_public_objects":
            continue
        p = m["attributes"].get("properties", {})
        out.append({
            "name": f"{p.get('name_prefix') or 'AT'} {p.get('name') or m['attributes'].get('object_name')}",
            "type": p.get("type") or None,
            "discovered": p.get("discoverydate"),
            "reported": p.get("time_received") or p.get("creationdate"),
            "reporting_group": p.get("reporting_group"),
            "sep": m["meta"].get("separation"),
        })
    return out


def _all_alerts(locus_id):
    url = f"{ANTARES_API}/loci/{locus_id}/alerts"
    params = {"page[limit]": 100}
    out = []
    while url:
        resp = _get(url, params)
        out.extend(resp.get("data", []))
        url = (resp.get("links") or {}).get("next")
        params = None  # the next link already carries paging params
    return out


def _flux_to_mag(flux):
    return -2.5 * math.log10(flux) + LSST_ZP_NJY if flux and flux > 0 else None


def _mag_to_flux(mag):
    return 10 ** (-0.4 * (mag - LSST_ZP_NJY))


def _pos_err(ra_err_deg, dec_err_deg):
    if ra_err_deg is None or dec_err_deg is None:
        return None
    return math.hypot(ra_err_deg, dec_err_deg) * 3600


def alert_positions(locus_id):
    """{alert_id: {ra, dec, pos_err_arcsec}} for detections; backfills objects cached before positions were kept."""
    out = {}
    for p in (_normalize(a) for a in _all_alerts(locus_id)):
        if p and p.get("ra") is not None:
            out[p["alert_id"]] = {k: p.get(k) for k in ("ra", "dec", "pos_err_arcsec")}
    return out


def alert_details(locus_id):
    """{alert_id: detail} for LSST detections; backfills objects cached before details were kept."""
    return _details(_all_alerts(locus_id))


def _details(alerts):
    """Rubin pipeline measurements per LSST detection, used by the rejection rules (not shown while vetting)."""
    out = {}
    for a in alerts:
        p = a["attributes"]["properties"]
        if p.get("ant_survey") != SURVEY_LSST:
            continue
        size, psf_size = (p.get("lsst_diaSource_ixx") or 0) + (p.get("lsst_diaSource_iyy") or 0), \
            (p.get("lsst_diaSource_ixxPSF") or 0) + (p.get("lsst_diaSource_iyyPSF") or 0)
        chi2, ndata = p.get("lsst_diaSource_psfChi2"), p.get("lsst_diaSource_psfNdata")
        out[a["id"]] = {
            "extendedness": p.get("lsst_diaSource_extendedness"),
            "is_dipole": p.get("lsst_diaSource_isDipole"),
            "glint_trail": p.get("lsst_diaSource_glint_trail"),
            # reduced chi2 of the PSF fit, and second moments relative to the PSF's
            "psf_chi2_red": chi2 / ndata if chi2 is not None and ndata else None,
            "size_ratio": size / psf_size if size > 0 and psf_size > 0 else None,
            "measurement_flagged": any(p.get(f"lsst_diaSource_{k}") for k in
                                       ("psfFlux_flag", "centroid_flag", "shape_flag", "apFlux_flag")),
        }
    return out


def _normalize(alert):
    """One light-curve point. Difference fluxes are put on a common nJy scale."""
    p = alert["attributes"]["properties"]
    code = p.get("ant_survey")
    base = {"alert_id": alert["id"], "mjd": p.get("ant_mjd")}

    if code == SURVEY_LSST:
        flux, err = p.get("lsst_diaSource_psfFlux"), p.get("lsst_diaSource_psfFluxErr")
        sci, sci_err = p.get("lsst_diaSource_scienceFlux"), p.get("lsst_diaSource_scienceFluxErr")
        flags = sorted(
            k.replace("lsst_diaSource_pixelFlags_", "")
            for k, v in p.items()
            if k.startswith("lsst_diaSource_pixelFlags_") and v is True
        )
        return {
            **base,
            "survey": "lsst",
            "kind": "detection",
            "band": p.get("lsst_diaSource_band") or p.get("ant_passband"),
            "diff_flux": flux,
            "diff_flux_err": err,
            "mag": _flux_to_mag(flux),
            "mag_err": (1.0857 * err / flux) if flux and err and flux > 0 else None,
            "lim_mag": _flux_to_mag(5 * err) if err else None,
            "sci_mag": _flux_to_mag(sci),
            "sci_mag_err": (1.0857 * sci_err / sci) if sci and sci_err and sci > 0 else None,
            "snr": p.get("lsst_diaSource_snr"),
            "ra": p.get("lsst_diaSource_ra", p.get("ant_ra")),
            "dec": p.get("lsst_diaSource_dec", p.get("ant_dec")),
            "pos_err_arcsec": _pos_err(p.get("lsst_diaSource_raErr"), p.get("lsst_diaSource_decErr")),
            "is_negative": bool(p.get("lsst_diaSource_isNegative")) or (flux is not None and flux < 0),
            "pixel_flags": flags,
            "ss_object_id": p.get("lsst_diaSource_ssObjectId") or None,
            "dipole_chi2": p.get("lsst_diaSource_dipoleChi2"),
            "trail_length": p.get("lsst_diaSource_trailLength"),
            # broker/ML opinion: hidden in blind mode
            "reliability": p.get("lsst_diaSource_reliability"),
        }

    if code == SURVEY_ZTF_CAND:
        mag, err = p.get("ztf_magpsf"), p.get("ztf_sigmapsf")
        positive = str(p.get("ztf_isdiffpos")) in ("t", "1", "True", "true")
        flux = _mag_to_flux(mag) * (1 if positive else -1) if mag else None
        return {
            **base,
            "survey": "ztf",
            "kind": "detection",
            "band": ZTF_FID_BAND.get(p.get("ztf_fid"), p.get("ant_passband")),
            "diff_flux": flux,
            "diff_flux_err": abs(flux) * err / 1.0857 if flux and err else None,
            "mag": mag if positive else None,
            "mag_err": err,
            "lim_mag": p.get("ztf_diffmaglim"),
            "sci_mag": None,
            "is_negative": not positive,
            "ra": p.get("ztf_ra", p.get("ant_ra")),
            "dec": p.get("ztf_dec", p.get("ant_dec")),
            "ss_name": p.get("ztf_ssnamenr") if p.get("ztf_ssnamenr") not in (None, "null") else None,
            "ss_dist_arcsec": p.get("ztf_ssdistnr") if (p.get("ztf_ssdistnr") or -1) >= 0 else None,
            "distnr": p.get("ztf_distnr"),
            "magnr": p.get("ztf_magnr"),
            "rb": p.get("ztf_rb"),
            "drb": p.get("ztf_drb"),
            "sgscore1": p.get("ztf_sgscore1"),
            "distpsnr1": p.get("ztf_distpsnr1"),
        }

    if code == SURVEY_ZTF_ULIM:
        return {
            **base,
            "survey": "ztf",
            "kind": "upper_limit",
            "band": ZTF_FID_BAND.get(p.get("ztf_fid"), p.get("ant_passband")),
            "lim_mag": p.get("ztf_diffmaglim") or p.get("ant_maglim"),
        }
    return None


def thumbnails(alert_id):
    data = _get(f"{ANTARES_API}/alerts/{alert_id}/thumbnails").get("data", [])
    return {d["attributes"]["thumbnail_type"]: d["attributes"]["src"] for d in data}
