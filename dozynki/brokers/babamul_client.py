"""Babamul object lookup: forced photometry, ZTF match, properties, crossmatches.

Needs BABAMUL_API_TOKEN in .env; without it every call returns {"enabled": False}.
"""

from ..config import BABAMUL_ENABLED

NJY_ZP = 31.4


def _to_njy(flux, zp):
    if flux is None:
        return None
    return flux if zp is None else flux * 10 ** (-0.4 * (zp - NJY_ZP))


def _phot(points, survey, kind):
    out = []
    for p in points or []:
        out.append(
            {
                "survey": survey,
                "kind": kind,
                "mjd": p.jd - 2400000.5,
                "band": str(getattr(p.band, "value", p.band)),
                "diff_flux": _to_njy(p.psfFlux, p.zp),
                "diff_flux_err": _to_njy(p.psfFluxErr, p.zp),
                "snr": p.snr,
            }
        )
    return out


def fetch(object_id):
    """object_id is an LSST diaObjectId, or a ZTF object id for objects sampled from ZTF."""
    if not BABAMUL_ENABLED:
        return {"enabled": False}
    from babamul import api

    survey = "ZTF" if str(object_id).startswith("ZTF") else "LSST"
    result = {"enabled": True, "found": False, "errors": {}}
    try:
        obj = api.get_object(survey, str(object_id))
        result["found"] = True
        result["forced_phot"] = _phot(obj.fp_hists, survey.lower(), "forced")
        result["properties"] = obj.properties.model_dump(mode="json") if obj.properties else None
        result["reliability"] = obj.drb
        ztf = obj.survey_matches.ztf if obj.survey_matches and survey == "LSST" else None
        result["ztf_match"] = (
            {"objectId": ztf.objectId, "ra": ztf.ra, "dec": ztf.dec, "forced_phot": _phot(ztf.fp_hists, "ztf", "forced")}
            if ztf
            else None
        )
    except Exception as e:
        result["errors"]["object"] = f"{type(e).__name__}: {e}"
    try:
        result["cross_matches"] = api.get_cross_matches(survey, str(object_id)).model_dump(mode="json")
    except Exception as e:
        result["errors"]["cross_matches"] = f"{type(e).__name__}: {e}"
    return result
