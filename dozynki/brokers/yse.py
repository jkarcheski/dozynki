"""YSE PZ: transients YSE has near the object's position, and the nearest one's photometry.

YSE PZ's API refuses anonymous requests, and what it shows depends on the account, so each labeler enters
their own YSE PZ login on the profile page and their matches are cached under their name (assemble.py).
Labelers without one get {"enabled": False}. The match's name, status and classification are broker
opinion, shown only after a blind label; its photometry is evidence, on the light curve from the start.

The API takes the username and password with every request, so the password is stored encrypted, not
hashed, with a key in the data directory (YSE_PZ_KEY): a copy of the database alone doesn't reveal it,
but anyone who can read the data directory can.

The API's transient list filters by an RA/Dec box; separations are computed here. Photometry comes from
download_photometry/<slug>/, a text file whose "# KEY: value" header carries the transient's fields with
names instead of API links, followed by one "OBS:" line per point.
"""

import math
import os

import requests
from cryptography.fernet import Fernet

from .. import db
from ..config import HTTP_TIMEOUT, YSE_PZ_KEY, YSE_PZ_URL

MATCH_RADIUS_ARCSEC = 3.0
# header fields the vetter shows (the rest stay in the cache)
HEADER_FIELDS = ("status", "best_spec_class", "photo_class", "context_class", "redshift", "disc_date", "tags", "obs_group")
OBS_COLUMNS = ("mjd", "band", "fluxcal", "fluxcal_err", "mag", "mag_err", "mag_sys", "telescope", "instrument", "dq")

_session = requests.Session()


class LoginError(ValueError):
    pass


def _get(path, login, params=None):
    r = _session.get(f"{YSE_PZ_URL}/{path}", params=params, auth=login, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r


# ---------- each labeler's login ----------

def _fernet():
    if not YSE_PZ_KEY.exists():
        fd = os.open(YSE_PZ_KEY, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return Fernet(YSE_PZ_KEY.read_bytes())


def cache_kind(labeler):
    return f"yse:{labeler}"


def login_for(labeler):
    """(username, password) for requests, or None."""
    rows = db.query("SELECT username, password FROM yse_logins WHERE labeler = ?", (labeler,))
    return (rows[0]["username"], _fernet().decrypt(rows[0]["password"].encode()).decode()) if rows else None


def username_for(labeler):
    rows = db.query("SELECT username FROM yse_logins WHERE labeler = ?", (labeler,))
    return rows[0]["username"] if rows else None


def save_login(labeler, username, password):
    """Check the login against YSE PZ, then keep it. A new login drops the old one's cached matches,
    since another account may see other photometry."""
    username = (username or "").strip()
    if not username or not password:
        raise LoginError("enter your YSE PZ username and password")
    try:
        _get("api/transients/", (username, password), {"limit": 1})
    except requests.HTTPError as e:
        if e.response.status_code in (401, 403):
            raise LoginError("YSE PZ didn't accept that username and password")
        raise
    db.execute("INSERT OR REPLACE INTO yse_logins (labeler, username, password, saved_at) VALUES (?, ?, ?, ?)",
               (labeler, username, _fernet().encrypt(password.encode()).decode(), db.now()))
    db.execute("DELETE FROM cache WHERE kind = ?", (cache_kind(labeler),))


def forget_login(labeler):
    db.execute("DELETE FROM yse_logins WHERE labeler = ?", (labeler,))
    db.execute("DELETE FROM cache WHERE kind = ?", (cache_kind(labeler),))


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


def cone(ra, dec, login, radius_arcsec=MATCH_RADIUS_ARCSEC):
    """YSE transients within radius_arcsec, nearest first."""
    half_dec = radius_arcsec / 3600
    half_ra = min(180, half_dec / max(math.cos(math.radians(dec)), 1e-6))
    found = {}
    for ra_lo, ra_hi in _ra_ranges(ra, half_ra):
        params = {"ra_gte": ra_lo, "ra_lte": ra_hi, "dec_gte": dec - half_dec, "dec_lte": dec + half_dec, "limit": 100}
        body = _get("api/transients/", login, params).json()
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


def fetch(ra, dec, login):
    result = {"enabled": True, "found": False, "matches": [], "errors": {}}
    try:
        result["matches"] = cone(ra, dec, login)
    except Exception as e:
        result["errors"]["cone"] = f"{type(e).__name__}: {e}"
        return result
    if not result["matches"]:
        return result
    best = result["matches"][0]
    result["found"] = True
    result["url"] = page_url(best["slug"])
    try:
        header, points = parse_photometry(_get(f"download_photometry/{best['slug']}/", login).text)
        result["header"] = header
        result["photometry"] = points
    except Exception as e:
        result["errors"]["photometry"] = f"{type(e).__name__}: {e}"
    return result
