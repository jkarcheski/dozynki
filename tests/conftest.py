"""Point the app at an empty, throwaway data folder before anything imports it.

dozynki.db opens (and migrates) the database as soon as it is imported, so this has to happen first:
otherwise the tests would run against data/dozynki.sqlite.
"""

import atexit
import os
import shutil
import tempfile

os.environ["DOZYNKI_DATA_DIR"] = tempfile.mkdtemp(prefix="dozynki-tests-")
atexit.register(shutil.rmtree, os.environ["DOZYNKI_DATA_DIR"], ignore_errors=True)
os.environ["DOZYNKI_ACCOUNTS"] = "0"
