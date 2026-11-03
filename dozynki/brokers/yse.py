"""YSE PZ: transients YSE has near the object's position, and the nearest one's photometry.

Needs YSE_PZ_USER and YSE_PZ_PASSWORD in .env (a YSE PZ account; its API refuses anonymous requests);
without them every call returns {"enabled": False}. The match's name, status and classification are broker
opinion, shown only after a blind label; its photometry is evidence, on the light curve from the start.

The API's transient list filters by an RA/Dec box; separations are computed here. Photometry comes from
download_photometry/<slug>/, a text file whose "# KEY: value" header carries the transient's fields with
names instead of API links, followed by one "OBS:" line per point.
"""

import math

import requests

from ..config import HTTP_TIMEOUT, YSE_PZ_ENABLED, YSE_PZ_PASSWORD, YSE_PZ_URL, YSE_PZ_USER

MATCH_RADIUS_ARCSEC = 3.0
# header fields the vetter shows (the rest stay in the cache)
HEADER_FIELDS = ("status", "best_spec_class", "photo_class", "context_class", "redshift", "disc_date", "tags", "obs_group")
OBS_COLUMNS = ("mjd", "band", "fluxcal", "fluxcal_err", "mag", "mag_err", "mag_sys", "telescope", "instrument", "dq")

_session = requests.Session()


def _get(path, params=None):
    r = _session.get(f"{YSE_PZ_URL}/{path}", params=params, auth=(YSE_PZ_USER, YSE_PZ_PASSWORD), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r


def _sep_arcsec(ra1, dec1, ra2, dec2):
    r1, d1, r2, d2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = math.sin((d2 - d1) / 2) ** 2 + math.cos(d1) * math.cos(d2) * math.sin((r2 - r1) / 2) ** 2
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(h)))) * 3600


def _ra_ranges(ra, half_width):
    """The box's RA range, split in two where it crosses 0/360."""
    lo, hi = ra - half_width, ra + half_width
    if lo < 0:
        return [(lo + 360, 360), (0, hi)]
    if hi >= 360:
        return [(lo, 360), (0, hi - 360)]
    return [(lo, hi)]


def cone(ra, dec, radius_arcsec=MATCH_RADIUS_ARCSEC):
    """YSE transients within radius_arcsec, nearest first."""
    half_dec = radius_arcsec / 3600
    half_ra = min(180, half_dec / max(math.cos(math.radians(dec)), 1e-6))
    found = {}
    for ra_lo, ra_hi in _ra_ranges(ra, half_ra):
        params = {"ra_gte": ra_lo, "ra_lte": ra_hi, "dec_gte": dec - half_dec, "dec_lte": dec + half_dec, "limit": 100}
        body = _get("api/transients/", params).json()
        for t in body["results"] if isinstance(body, dict) else body:
            sep = _sep_arcsec(ra, dec, t["ra"], t["dec"])
            if sep <= radius_arcsec:
                found[t["slug"]] = {"name": t["name"], "slug": t["slug"], "ra": t["ra"], "dec": t["dec"], "sep": sep,
                                    "redshift": t.get("redshift"), "disc_date": t.get("disc_date")}
    return sorted(found.values(), key=lambda m: m["sep"])


def _number(s):
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def parse_photometry(text):
    """(header {field: value}, points) from a download_photometry file."""
    header, points = {}, []
    for line in text.splitlines():
        if line.startswith("# ") and ":" in line:
            k, v = line[2:].split(":", 1)
            header[k.strip().lower()] = v.strip()
        elif line.startswith("OBS:"):
            values = line.split()[1:]
            if len(values) != len(OBS_COLUMNS):
                continue
            p = dict(zip(OBS_COLUMNS, values))
            for k in ("mjd", "fluxcal", "fluxcal_err", "mag", "mag_err"):
                p[k] = _number(p[k])
            p["dq"] = None if p["dq"] == "None" else p["dq"]
            points.append(p)
    return header, points


def page_url(slug):
    return f"{YSE_PZ_URL}/transient_detail/{slug}/"


def fetch(ra, dec):
    if not YSE_PZ_ENABLED:
        return {"enabled": False}
    result = {"enabled": True, "found": False, "matches": [], "errors": {}}
    try:
        result["matches"] = cone(ra, dec)
    except Exception as e:
        result["errors"]["cone"] = f"{type(e).__name__}: {e}"
        return result
    if not result["matches"]:
        return result
    best = result["matches"][0]
    result["found"] = True
    result["url"] = page_url(best["slug"])
    try:
        header, points = parse_photometry(_get(f"download_photometry/{best['slug']}/").text)
        result["header"] = header
        result["photometry"] = points
    except Exception as e:
        result["errors"]["photometry"] = f"{type(e).__name__}: {e}"
    return result
