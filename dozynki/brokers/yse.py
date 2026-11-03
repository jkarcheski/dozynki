"""YSE PZ: the object's transient in YSE PZ, found by its TNS name, and that transient's photometry.

YSE PZ's API refuses anonymous requests, and what it shows depends on the account, so each labeler enters
their own YSE PZ login on the profile page and their matches are cached under their name (assemble.py).
Labelers without one get {"enabled": False}. The match's name, status and classification are broker
opinion, shown only after a blind label; its photometry is evidence, on the light curve from the start.

The API takes the username and password with every request, so the password is stored encrypted, not
hashed, with a key in the data directory (YSE_PZ_KEY): a copy of the database alone doesn't reveal it,
but anyone who can read the data directory can.

YSE PZ names transients by their TNS names, so the object's TNS matches (ANTARES's, tns.py) are looked up
directly in download_photometry/<name>/, a text file whose "# KEY: value" header carries the transient's
fields (names, not API links), followed by one "OBS:" line per point. A position search would need the
API's transient list, which counts every transient first and times out (a 504 after five minutes); so
objects without a TNS name aren't looked up.
"""

import math
import os

import requests
from cryptography.fernet import Fernet

from .. import db
from ..config import HTTP_TIMEOUT, YSE_PZ_KEY, YSE_PZ_URL

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


def _rejected(e):
    return isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (401, 403)


def check_login(login):
    """Raise LoginError unless YSE PZ accepts login. Asks for the short list of transient statuses, which
    needs a login: the transient list itself counts every transient first and can take minutes."""
    try:
        _get("api/transientstatuses/", login, {"limit": 1})
    except requests.RequestException as e:
        if _rejected(e):
            raise LoginError(f"YSE PZ didn't accept the password for {login[0]!r}. Check it and try again.")
        raise LoginError(f"Couldn't reach YSE PZ to check the login ({type(e).__name__}); try again later.")


def save_login(labeler, username, password):
    """Check the login against YSE PZ, then keep it. A new login drops the old one's cached matches,
    since another account may see other photometry."""
    username = (username or "").strip()
    if not username or not password:
        raise LoginError("Enter your YSE PZ username and password.")
    check_login((username, password))
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


def yse_name(tns_name):
    """YSE PZ's name for a TNS object: the name without its prefix ("SN 2025ktf" -> "2025ktf")."""
    return tns_name.split()[-1]


def fetch(ra, dec, login, tns_names):
    """The first of the object's TNS names (nearest first) that YSE PZ has, with its photometry."""
    result = {"enabled": True, "found": False, "matches": [], "tns_names": list(tns_names), "errors": {}}
    for tns_name in tns_names:
        name = yse_name(tns_name)
        try:
            header, points = parse_photometry(_get(f"download_photometry/{name}/", login).text)
        except requests.HTTPError as e:
            if _rejected(e):
                result["errors"]["login"] = f"{type(e).__name__}: {e}"
                return result
            continue  # YSE PZ answers a name it doesn't have with an error page
        except Exception as e:
            result["errors"]["lookup"] = f"{type(e).__name__}: {e}"
            return result
        if header.get("name") != name:
            continue
        yra, ydec = _number(header.get("ra", "")), _number(header.get("dec", ""))
        result["matches"] = [{"name": name, "tns_name": tns_name, "ra": yra, "dec": ydec,
                              "sep": _sep_arcsec(ra, dec, yra, ydec) if yra is not None and ydec is not None else None}]
        result.update(found=True, url=page_url(name), header=header, photometry=points)
        return result
    return result
