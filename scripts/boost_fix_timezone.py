#!/usr/bin/env python3
"""One-off fix: recompute rst_dt_iso from rst_dt (epoch ms) using Europe/Warsaw tz,
   and fix measured_at timestamps in the manual tables from UTC to Warsaw-local.
   Safe to re-run (idempotent recompute from source epoch / stored UTC values)."""
import os
import sys
import sqlite3
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import DB_PATH  # noqa: E402

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("SELECT prfl_m_cd, rst_dt FROM roasts WHERE rst_dt IS NOT NULL")
rows = cur.fetchall()
print(f"Updating rst_dt_iso for {len(rows)} roasts...")
for prfl_m_cd, rst_dt in rows:
    iso = datetime.fromtimestamp(rst_dt / 1000, tz=timezone.utc).astimezone(WARSAW_TZ).isoformat()
    cur.execute("UPDATE roasts SET rst_dt_iso=? WHERE prfl_m_cd=?", (iso, prfl_m_cd))
conn.commit()
print("Done roasts.")

# measured_at in manual tables were stored as naive UTC isoformat (no offset) earlier;
# convert them by assuming UTC and re-expressing in Warsaw time.
for table in ("whole_bean_color", "ground_bean_color", "post_roast_weight"):
    cur.execute(f"SELECT id, measured_at FROM {table}")
    trows = cur.fetchall()
    for rid, measured_at in trows:
        if not measured_at:
            continue
        try:
            dt = datetime.fromisoformat(measured_at)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        new_val = dt.astimezone(WARSAW_TZ).isoformat()
        cur.execute(f"UPDATE {table} SET measured_at=? WHERE id=?", (new_val, rid))
    print(f"Updated {len(trows)} rows in {table}.")
conn.commit()
conn.close()
print("All done.")
