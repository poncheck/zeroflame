"""
Central, environment-driven configuration for Zeroflame.

Everything that used to be a hardcoded absolute path or credential in the
original single-machine scripts now comes from environment variables (with
sane local-dev defaults), so the project runs unmodified on any server /
inside Docker.

Loads a `.env` file (if present) at import time using python-dotenv, so
scripts and the webapp behave the same whether run directly or in a
container with `--env-file`.
"""
import os

from dotenv import load_dotenv

# Looks for a .env in the current working directory and parent dirs.
load_dotenv()

# Directory where all three SQLite databases live. In Docker this is a
# mounted volume (e.g. /app/data); for local dev it defaults to ./data
# relative to the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("ZEROFLAME_DATA_DIR", os.path.join(_REPO_ROOT, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "boost_roasts.db")
INV_DB_PATH = os.path.join(DATA_DIR, "green_bean_inventory.db")
AUTH_DB_PATH = os.path.join(DATA_DIR, "auth.db")

# Boost (stronghold.coffee) scraper login — required only for
# boost_build_db.py / boost_incremental_update.py.
BOOST_EMAIL = os.environ.get("BOOST_EMAIL")
BOOST_PASSWORD = os.environ.get("BOOST_PASSWORD")
BOOST_BASE_URL = os.environ.get("BOOST_BASE_URL", "https://boost.stronghold.coffee")
BOOST_PROD_CD = os.environ.get("BOOST_PROD_CD", "3528")  # Boost device/product code

# Flask session signing key. MUST be overridden in production via env/.env.
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-insecure-fallback-key")

# Bootstrap admin account, used only by scripts/init_db.py on first run
# (when the auth DB has no users yet). Not required after that.
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
