"""ALeRCE classifier probabilities for the LSST object and its ZTF counterpart, and ZTF objects
and light curves for when ANTARES's search is down."""

from alerce.core import Alerce

from .antares import SURVEY_ZTF_CAND, SURVEY_ZTF_ULIM, _normalize

_client = Alerce()


def _group(rows):
    """{classifier: {"version", "top", "probs": {class: p}}}"""
    out = {}
    for r in rows or []:
        name = r.get("classifier_name")
        entry = out.setdefault(name, {"version": r.get("classifier_version"), "probs": {}})
        entry["probs"][r.get("class_name")] = r.get("probability")
    for entry in out.values():
        entry["top"] = max(entry["probs"], key=entry["probs"].get) if entry["probs"] else None
    return out


def lsst_cone(ra, dec, radius_arcsec):
    """LSST diaObjects within radius_arcsec, as [(dia_object_id, meanra, meandec)]. Only positions are kept:
    ALeRCE's rows also carry a class, which is broker opinion."""
    rows = _client.query_objects(survey="lsst", ra=ra, dec=dec, radius=radius_arcsec, format="json", page_size=100)
    return list({str(r["oid"]): (str(r["oid"]), r["meanra"], r["meandec"]) for r in rows or []}.values())


def lsst_position(dia_object_id):
    """(meanra, meandec) of an LSST diaObject ALeRCE has processed."""
    o = _client.query_object(survey="lsst", oid=int(dia_object_id), format="json")
    return o["meanra"], o["meandec"]


COUNT_CAP = 50001  # ALeRCE stops counting matches here


def _ztf_query(mjd_lo, mjd_hi, min_det, **kw):
    return _client.query_objects(survey="ztf", firstmjd=[mjd_lo, mjd_hi], ndet=[min_det, None], format="json", **kw)


def ztf_count(mjd_lo, mjd_hi, min_det):
    """ZTF objects first detected in [mjd_lo, mjd_hi] with at least min_det detections, up to COUNT_CAP."""
    return _ztf_query(mjd_lo, mjd_hi, min_det, page_size=1, count=True)["total"] or 0


def ztf_page(mjd_lo, mjd_hi, min_det, page, page_size):
    """One page (from 1) of those objects, oldest first. Only positions and counts are kept, not ALeRCE's class."""
    rows = _ztf_query(mjd_lo, mjd_hi, min_det, page=page, page_size=page_size,
                      order_by="firstmjd", order_mode="ASC")["items"]
    return [{"ztf_object_id": r["oid"], "ra": r["meanra"], "dec": r["meandec"], "n_det": r["ndet"],
             "first_mjd": r["firstmjd"], "last_mjd": r["lastmjd"]} for r in rows]


def ztf_payload(oid):
    """A ZTF object's light curve in antares.get_locus's shape, for objects whose ANTARES locus can't be found.

    ALeRCE's rows are recast as ANTARES alert properties and normalized the same way; the alert ids are
    ANTARES's (ztf_candidate:<candid>), so the cutouts still come from ANTARES. There are no ANTARES tags,
    catalogs or PS1 star/galaxy scores.
    """
    obj = _client.query_object(survey="ztf", oid=oid, format="json")
    alerts = [{"id": f"ztf_candidate:{d['candid']}" if d.get("candid") else None,
               "properties": {"ant_survey": SURVEY_ZTF_CAND, "ant_mjd": d["mjd"], "ztf_fid": d["fid"],
                              "ztf_magpsf": d.get("magpsf"), "ztf_sigmapsf": d.get("sigmapsf"),
                              "ztf_isdiffpos": "t" if (d.get("isdiffpos") or 1) > 0 else "f",
                              "ztf_diffmaglim": d.get("diffmaglim"), "ztf_ra": d.get("ra"), "ztf_dec": d.get("dec"),
                              "ztf_distnr": d.get("distnr"), "ztf_rb": d.get("rb"), "ztf_drb": d.get("drb")}}
              for d in _client.query_detections(oid, survey="ztf", format="json")]
    alerts += [{"id": None, "properties": {"ant_survey": SURVEY_ZTF_ULIM, "ant_mjd": u["mjd"], "ztf_fid": u["fid"],
                                           "ztf_diffmaglim": u.get("diffmaglim")}}
               for u in _client.query_non_detections(oid, survey="ztf", format="json")]
    return {
        "locus_id": None,
        "ra": obj["meanra"],
        "dec": obj["meandec"],
        "survey": {},
        "ztf_object_id": oid,
        "tags": [],
        "catalogs": [],
        "catalog_matches": [],
        "num_alerts": len(alerts),
        "points": [p for p in (_normalize(a) for a in alerts) if p],
        "lsst_details": {},
        "from": "alerce",
    }


def lsst_top_class_since(classifier, class_name, min_prob, since_mjd, max_pages=100):
    """LSST objects whose top class from `classifier` is class_name at probability >= min_prob, first detected
    at or after since_mjd. ALeRCE's firstmjd filter times out, so this pages newest first and stops there."""
    out = []
    for page in range(1, max_pages + 1):
        rows = _client.query_objects(survey="lsst", classifier=classifier, class_name=class_name, probability=min_prob,
                                     ranking=1, order_by="firstmjd", order_mode="DESC", page=page, page_size=100,
                                     format="json") or []
        out += [r for r in rows if r["firstmjd"] >= since_mjd]
        if len(rows) < 100 or rows[-1]["firstmjd"] < since_mjd:
            break
    return out


def fetch(dia_object_id, ztf_object_id=None):
    """dia_object_id is None for objects sampled from ZTF."""
    result = {"lsst": None, "ztf": None, "errors": {}}
    if dia_object_id:
        try:
            result["lsst"] = _group(_client.query_probabilities(survey="lsst", oid=int(dia_object_id)))
        except Exception as e:  # ALeRCE returns 404 for objects it hasn't processed
            result["errors"]["lsst"] = str(e)
    if ztf_object_id:
        try:
            result["ztf"] = _group(_client.query_probabilities(survey="ztf", oid=ztf_object_id))
        except Exception as e:
            result["errors"]["ztf"] = str(e)
    return result
