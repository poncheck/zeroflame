#!/usr/bin/env python3
"""
Bootstrap script: creates empty SQLite schemas for a brand-new Zeroflame
instance (auth.db + green_bean_inventory.db) and, if ADMIN_USERNAME /
ADMIN_PASSWORD are set in the environment and no users exist yet, creates
the first admin account.

boost_roasts.db's schema is created lazily by boost_build_db.py's init_db(),
so it isn't touched here.

Safe to re-run: all CREATE TABLE statements are IF NOT EXISTS, and the admin
user is only created once (skipped if the users table is non-empty).

Usage:
  python3 init_db.py
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import AUTH_DB_PATH, INV_DB_PATH, ADMIN_USERNAME, ADMIN_PASSWORD  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    is_admin INTEGER DEFAULT 0,
    role TEXT NOT NULL DEFAULT 'roaster',  -- 'admin' / 'roaster' / 'viewer'
    created_at TEXT DEFAULT (datetime('now')),
    last_login TEXT
);
"""

INVENTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS beans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,          -- short code/name used in Boost (e.g. CHR, EBDLG)
    full_name TEXT,
    country TEXT,
    region TEXT,
    variety TEXT,
    processing TEXT,
    status TEXT,                        -- 'active' / 'historical'
    boost_bean_cd TEXT,
    current_stock_kg REAL,
    first_used TEXT,
    last_used TEXT,
    roast_count INTEGER,
    source_note TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    batch_count INTEGER,
    farm TEXT
);

CREATE TABLE IF NOT EXISTS stock_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bean_code TEXT NOT NULL,
    movement_type TEXT,     -- 'delivery' / 'usage' / 'adjustment'
    amount_kg REAL,         -- + for delivery, - for usage
    movement_date TEXT,
    notes TEXT,
    source TEXT,
    reference_cd TEXT,      -- 'boost_api' / 'manual' / 'roast_auto'
    FOREIGN KEY (bean_code) REFERENCES beans(code)
);

CREATE TABLE IF NOT EXISTS moisture_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bean_code TEXT NOT NULL,
    moisture_pct REAL,
    density REAL,
    bean_temp_c REAL,
    measured_at TEXT NOT NULL,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (bean_code) REFERENCES beans(code)
);

CREATE TABLE IF NOT EXISTS bean_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bean_code TEXT NOT NULL,
    tag TEXT NOT NULL,
    UNIQUE(bean_code, tag),
    FOREIGN KEY (bean_code) REFERENCES beans(code)
);
"""


def main():
    auth_conn = sqlite3.connect(AUTH_DB_PATH)
    auth_conn.executescript(AUTH_SCHEMA)
    auth_conn.commit()
    print(f"auth.db ready at {AUTH_DB_PATH}")

    # Migration: older databases may not have the `role` column yet.
    existing_cols = {row[1] for row in auth_conn.execute("PRAGMA table_info(users)")}
    if "role" not in existing_cols:
        auth_conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'roaster'")
        auth_conn.execute("UPDATE users SET role = 'admin' WHERE is_admin = 1")
        auth_conn.commit()
        print("Migrated auth.db: added 'role' column (existing admins -> role='admin').")

    inv_conn = sqlite3.connect(INV_DB_PATH)
    inv_conn.executescript(INVENTORY_SCHEMA)
    inv_conn.commit()
    print(f"green_bean_inventory.db ready at {INV_DB_PATH}")

    cur = auth_conn.execute("SELECT COUNT(*) FROM users")
    user_count = cur.fetchone()[0]
    if user_count == 0:
        if ADMIN_USERNAME and ADMIN_PASSWORD:
            auth_conn.execute(
                "INSERT INTO users (username, password_hash, display_name, is_admin, role) VALUES (?, ?, ?, 1, 'admin')",
                (ADMIN_USERNAME, generate_password_hash(ADMIN_PASSWORD), ADMIN_USERNAME),
            )
            auth_conn.commit()
            print(f"Created admin user '{ADMIN_USERNAME}'.")
        else:
            print(
                "No users exist yet and ADMIN_USERNAME/ADMIN_PASSWORD are not set — "
                "set them in .env and re-run this script to create the first login."
            )
    else:
        print(f"{user_count} user(s) already exist, skipping admin bootstrap.")

    auth_conn.close()
    inv_conn.close()


if __name__ == "__main__":
    main()
