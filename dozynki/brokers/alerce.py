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
