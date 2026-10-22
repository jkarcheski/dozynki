import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
EXPORT_DIR = ROOT / "exports"
DB_PATH = DATA_DIR / "dozynki.sqlite"
DATA_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)

LABELER = os.environ.get("DOZYNKI_LABELER") or "anonymous"
BLIND_DEFAULT = os.environ.get("DOZYNKI_BLIND", "1") not in ("0", "false", "False", "")
BABAMUL_ENABLED = bool(os.environ.get("BABAMUL_API_TOKEN"))

ANTARES_API = "https://api.antares.noirlab.edu/v1"
DATALAB_QUERY = "https://datalab.noirlab.edu/query/query"
DATALAB_ANON_TOKEN = "anonymous.0.0.anon_access"

HTTP_TIMEOUT = 60
XMATCH_RADIUS_ARCSEC = 30.0
