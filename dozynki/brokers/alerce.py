"""ALeRCE classifier probabilities for the LSST object and its ZTF counterpart."""

from alerce.core import Alerce

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
    result = {"lsst": None, "ztf": None, "errors": {}}
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
