#!/bin/sh
# Container entrypoint: initializes the SQLite schemas (idempotent), starts
# a background loop that syncs new Boost roasts every minute (only if
# BOOST_EMAIL/BOOST_PASSWORD are set), and runs the webapp in the foreground.
set -e

cd /app

echo "[entrypoint] Initializing databases..."
python3 scripts/init_db.py

if [ -n "$BOOST_EMAIL" ] && [ -n "$BOOST_PASSWORD" ]; then
  echo "[entrypoint] BOOST_EMAIL set — starting background roast sync loop (every 60s)."
  (
    while true; do
      python3 scripts/boost_incremental_update.py || echo "[sync] run failed, will retry in 60s" >&2
      sleep 60
    done
  ) &
else
  echo "[entrypoint] BOOST_EMAIL/BOOST_PASSWORD not set — skipping automatic Boost sync."
  echo "[entrypoint] You can still browse/edit an already-populated database."
fi

echo "[entrypoint] Starting webapp on 0.0.0.0:8088..."
exec python3 webapp/boost_webapp.py --host 0.0.0.0 --port 8088
