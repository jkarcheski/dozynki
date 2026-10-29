import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.environ.get("DOZYNKI_DATA_DIR") or ROOT / "data")
EXPORT_DIR = ROOT / "exports"
DB_PATH = DATA_DIR / "dozynki.sqlite"
DATA_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)

LABELER = os.environ.get("DOZYNKI_LABELER") or "anonymous"
BLIND_DEFAULT = os.environ.get("DOZYNKI_BLIND", "1") not in ("0", "false", "False", "")
BABAMUL_ENABLED = bool(os.environ.get("BABAMUL_API_TOKEN"))

# Profiles (accounts.py), for a shared server. Off: everyone is LABELER, as on a laptop.
ACCOUNTS = os.environ.get("DOZYNKI_ACCOUNTS", "0") not in ("0", "false", "False", "")
SECURE_COOKIES = os.environ.get("DOZYNKI_SECURE_COOKIES", "0") not in ("0", "false", "False", "")  # behind https
BASE_URL = os.environ.get("DOZYNKI_BASE_URL", "").rstrip("/")  # for reset links; else the request's own address
SMTP = {k: os.environ.get(f"DOZYNKI_SMTP_{k.upper()}", "") for k in ("host", "port", "user", "password", "from")}

ANTARES_API = "https://api.antares.noirlab.edu/v2"
# Pitt-Google's BigQuery archive of the LSST alert stream; queries are billed to your own project
PITTGOOGLE_TABLE = os.environ.get("PITTGOOGLE_TABLE", "pitt-alert-broker.lsst.alerts_v10_0")
PITTGOOGLE_BILLING_PROJECT = os.environ.get("PITTGOOGLE_BILLING_PROJECT", "")
PITTGOOGLE_MAX_BYTES = int(os.environ.get("PITTGOOGLE_MAX_BYTES", 20 * 10**9))  # refuse queries scanning more
DATALAB_QUERY ="https://datalab.noirlab.edu/query/query"
DATALAB_ANON_TOKEN = "anonymous.0.0.anon_access"

HTTP_TIMEOUT = 60
XMATCH_RADIUS_ARCSEC = 10.0      # default; the UI can re-search wider per object
XMATCH_MAX_RADIUS_ARCSEC = 300.0
