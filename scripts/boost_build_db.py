#!/usr/bin/env python3
"""
Boost (stronghold.coffee) roast data scraper -> SQLite DB.

Builds a full local database of all roasts recorded in Boost for a given
roastery/device, including:
  - roast metadata (name, mode, level, bean, weights, crack points, etc.)
  - roast phases (drying/yellowing/development times and ratios)
  - full temperature curve (per-second internal/tower/hot-air temps)
  - full heat control curve (per-second hot air / halogen / band / damper levels)

Plus three empty tables for manual data entry:
  - whole_bean_color
  - ground_bean_color
  - post_roast_weight

Usage:
  python3 boost_build_db.py [--limit N] [--concurrency N] [--resume]

Configuration (via environment / .env, see common/config.py):
  BOOST_EMAIL, BOOST_PASSWORD   - Boost login credentials (required)
  BOOST_BASE_URL                - defaults to https://boost.stronghold.coffee
  BOOST_PROD_CD                 - Boost device/product code (defaults to 3528)
  ZEROFLAME_DATA_DIR            - directory holding the SQLite DBs
"""
import os
import sys
import json
import time
import sqlite3
import asyncio
import argparse
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import DB_PATH, BOOST_EMAIL, BOOST_PASSWORD, BOOST_BASE_URL, BOOST_PROD_CD  # noqa: E402

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

BASE_URL = BOOST_BASE_URL
PROD_CD = BOOST_PROD_CD
PAGE_SET_CNT = 20


def login_and_get_cookies():
    email = BOOST_EMAIL
    password = BOOST_PASSWORD
    if not email or not password:
        print("Missing BOOST_EMAIL/BOOST_PASSWORD (set them in .env or the environment)", file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{BASE_URL}/acct/login.sq?lang=en", wait_until="networkidle")
        page.fill('input[type="email"]', email)
        page.fill('input[type="password"]', password)
        with page.expect_navigation(wait_until="load", timeout=20000):
            page.click('button:has-text("Sign in")')
        page.wait_for_timeout(2000)
        if "login" in page.url:
            print("Login appears to have failed, still on login page:", page.url, file=sys.stderr)
            sys.exit(1)
        cookies = page.context.cookies()
        browser.close()
    return {c["name"]: c["value"] for c in cookies}


SCHEMA = """
CREATE TABLE IF NOT EXISTS roasts (
    prfl_m_cd TEXT PRIMARY KEY,
    prfl_name TEXT,
    prod_cd TEXT,
    prod_nickname TEXT,
    model_name TEXT,
    rst_mod TEXT,            -- roasting mode code (1=manual,2=replication,3=program per observed UI)
    rst_lvl TEXT,            -- roasting level code
    total_rst_time INTEGER,  -- seconds
    dev_time INTEGER,        -- development time seconds
    dtr REAL,                -- development time ratio %
    rst_dt INTEGER,          -- epoch millis of roast
    rst_dt_iso TEXT,         -- human readable local time
    bean_name TEXT,
    bean_cd TEXT,
    grn_bean_amt REAL,       -- green bean input weight (kg)
    memo TEXT,
    up_prfl_m_cd TEXT,       -- original/reference profile id (for replication roasts)
    up_prfl_name TEXT,
    up_prod_cd TEXT,
    prgm_mode TEXT,
    prgm_sample_amount REAL,
    prgm_nation_name TEXT,
    prgm_processing TEXT,
    prgm_rost_lvl_set TEXT,
    bean_insert_c_tmpt REAL,
    bean_insert_f_tmpt REAL,
    first_crck_set_c_temp REAL,
    first_crck_set_f_temp REAL,
    dschge_set_c_temp REAL,
    dschge_set_f_temp REAL,
    tp_time INTEGER,          -- turning point time (sec)
    tp_in_c_temp REAL,
    tp_in_f_temp REAL,
    tp_ir_c_temp REAL,
    tp_ir_f_temp REAL,
    first_crk_time INTEGER,
    first_crk_in_c_temp REAL,
    first_crk_in_f_temp REAL,
    first_crk_ir_c_temp REAL,
    first_crk_ir_f_temp REAL,
    second_crk_time INTEGER,
    second_crk_in_c_temp REAL,
    second_crk_in_f_temp REAL,
    second_crk_ir_c_temp REAL,
    second_crk_ir_f_temp REAL,
    dschg_time INTEGER,       -- release/discharge time (sec)
    dschg_in_c_temp REAL,
    dschg_in_f_temp REAL,
    dschg_ir_c_temp REAL,
    dschg_ir_f_temp REAL,
    rep_to_man_time INTEGER,
    rep_to_man_c_temp REAL,
    rep_to_man_f_temp REAL,
    rep_to_man_ir_c_temp REAL,
    rep_to_man_ir_f_temp REAL,
    -- phase breakdown
    base_temp TEXT,
    drying_time INTEGER,
    drying_end_c_temp REAL,
    drying_end_f_temp REAL,
    drying_ratio REAL,
    yellow_time INTEGER,
    yellow_end_c_temp REAL,
    yellow_end_f_temp REAL,
    yellow_ratio REAL,
    develop_time INTEGER,
    develop_end_c_temp REAL,
    develop_end_f_temp REAL,
    develop_ratio REAL,
    raw_list_json TEXT,       -- raw JSON from list endpoint (lightweight fields)
    raw_detail_json TEXT,     -- raw JSON from detail endpoint (full fidelity backup)
    fetched_at TEXT           -- when this row was scraped (UTC ISO)
);

CREATE TABLE IF NOT EXISTS roast_temps (
    prfl_m_cd TEXT NOT NULL REFERENCES roasts(prfl_m_cd),
    rst_time INTEGER NOT NULL,   -- seconds since roast start
    internal_c_temp REAL,
    internal_f_temp REAL,
    tower_drum_c_temp REAL,
    tower_drum_f_temp REAL,
    hot_air_c_temp REAL,
    hot_air_f_temp REAL,
    band_c_temp REAL,
    band_f_temp REAL,
    ir_c_temp REAL,
    ir_f_temp REAL,
    PRIMARY KEY (prfl_m_cd, rst_time)
);

CREATE TABLE IF NOT EXISTS roast_controls (
    prfl_m_cd TEXT NOT NULL REFERENCES roasts(prfl_m_cd),
    rst_time INTEGER NOT NULL,
    hot_air_lvl REAL,
    hlgn_lvl REAL,            -- halogen heater level
    band_lvl REAL,
    agt_lvl REAL,             -- agitator level
    blower_lvl REAL,
    PRIMARY KEY (prfl_m_cd, rst_time)
);

-- Manual data-entry tables, linked 1:1 to a roast.
CREATE TABLE IF NOT EXISTS whole_bean_color (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prfl_m_cd TEXT NOT NULL REFERENCES roasts(prfl_m_cd),
    color_value REAL,          -- e.g. Agtron / colorimeter reading
    color_unit TEXT,           -- e.g. 'Agtron', 'L*', free text
    measured_at TEXT,          -- local ISO timestamp of measurement
    notes TEXT
);

CREATE TABLE IF NOT EXISTS ground_bean_color (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prfl_m_cd TEXT NOT NULL REFERENCES roasts(prfl_m_cd),
    color_value REAL,
    color_unit TEXT,
    measured_at TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS post_roast_weight (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prfl_m_cd TEXT NOT NULL REFERENCES roasts(prfl_m_cd),
    weight_kg REAL,
    measured_at TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_roasts_rst_dt ON roasts(rst_dt);
CREATE INDEX IF NOT EXISTS idx_roasts_bean_cd ON roasts(bean_cd);
CREATE INDEX IF NOT EXISTS idx_temps_prfl ON roast_temps(prfl_m_cd);
CREATE INDEX IF NOT EXISTS idx_controls_prfl ON roast_controls(prfl_m_cd);
"""


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def ms_to_iso(ms):
    """Convert epoch-millis roast timestamp to a local Europe/Warsaw ISO string."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(WARSAW_TZ).isoformat()
    except Exception:
        return None


async def fetch_total_count(client, prod_cd):
    resp = await client.get(f"{BASE_URL}/rstry/profile/status/all/list.sq?rc=w0")
    resp.raise_for_status()
    data = resp.json()
    for s in data.get("status", []):
        if s.get("prodCd") == prod_cd:
            return s.get("totalPrflCnt")
    return None


async def fetch_all_roast_list(client, prod_cd, expected_total=None, stop_at=None):
    """Paginate through /rstry/profile/status/list.sq and return all lightweight roast dicts."""
    all_profiles = []
    seen_codes = set()
    page_num = 1
    while True:
        params = {
            "rc": "w0", "prodCd": prod_cd, "page": str(page_num), "pageSetCnt": str(PAGE_SET_CNT),
            "minRstTime": "0", "maxRstTime": "99999", "prflName": "", "grnBeanName": "",
            "startRstDt": "0", "endRstDt": "0",
        }
        qs = urllib.parse.urlencode(params)
        resp = await client.get(f"{BASE_URL}/rstry/profile/status/list.sq?{qs}")
        resp.raise_for_status()
        data = resp.json()
        profiles = data.get("profiles", [])
        if not profiles:
            break
        new_codes = [p["prflMCd"] for p in profiles if p["prflMCd"] not in seen_codes]
        if not new_codes:
            print(f"  page {page_num}: all codes already seen, stopping (likely wrapped around).", file=sys.stderr)
            break
        for p in profiles:
            if p["prflMCd"] not in seen_codes:
                seen_codes.add(p["prflMCd"])
                all_profiles.append(p)
        print(f"  page {page_num}: +{len(new_codes)} new (total {len(all_profiles)})", file=sys.stderr)
        if stop_at and len(all_profiles) >= stop_at:
            print(f"  reached --limit cutoff ({stop_at}), stopping list fetch early.", file=sys.stderr)
            break
        if expected_total and len(all_profiles) >= expected_total:
            print(f"  reached expected total ({expected_total}), stopping.", file=sys.stderr)
            break
        if len(profiles) < PAGE_SET_CNT:
            break
        page_num += 1
        if page_num > 600:  # hard safety cap (600*20=12000, well above known roast counts)
            print("  hard page cap reached, stopping.", file=sys.stderr)
            break
    return all_profiles


async def fetch_detail(client, prod_cd, prfl_m_cd, retries=3):
    url = f"{BASE_URL}/rstry/profile/detail/get.sq?prodCd={prod_cd}&prflMCd={prfl_m_cd}&rc=w0"
    for attempt in range(retries):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt == retries - 1:
                print(f"  FAILED detail for {prfl_m_cd}: {e}", file=sys.stderr)
                return None
            await asyncio.sleep(1.5 * (attempt + 1))
    return None


def upsert_roast(conn, list_item, detail):
    prfl_m_cd = list_item["prflMCd"]
    prflM = (detail or {}).get("prflM", {}) or {}
    phase = (detail or {}).get("phase", {}) or {}

    row = {
        "prfl_m_cd": prfl_m_cd,
        "prfl_name": list_item.get("prflName"),
        "prod_cd": PROD_CD,
        "prod_nickname": prflM.get("prodNickName"),
        "model_name": prflM.get("modelName"),
        "rst_mod": list_item.get("rstMod"),
        "rst_lvl": list_item.get("rstLvl"),
        "total_rst_time": list_item.get("totalRstTime"),
        "dev_time": list_item.get("devTime"),
        "dtr": list_item.get("dtr"),
        "rst_dt": list_item.get("rstDt"),
        "rst_dt_iso": ms_to_iso(list_item.get("rstDt")),
        "bean_name": list_item.get("beanName") or prflM.get("beanName"),
        "bean_cd": list_item.get("beanCd") or prflM.get("beanCd"),
        "grn_bean_amt": prflM.get("grnBeanAmt"),
        "memo": prflM.get("memo"),
        "up_prfl_m_cd": prflM.get("upPrflMCd"),
        "up_prfl_name": prflM.get("upPrflName"),
        "up_prod_cd": prflM.get("upProdCd"),
        "prgm_mode": prflM.get("prgmMode"),
        "prgm_sample_amount": prflM.get("prgmSampleAmount"),
        "prgm_nation_name": prflM.get("prgmNationName"),
        "prgm_processing": prflM.get("prgmProcessing"),
        "prgm_rost_lvl_set": prflM.get("prgmRostLvlSet"),
        "bean_insert_c_tmpt": prflM.get("beanInsertCTmpt"),
        "bean_insert_f_tmpt": prflM.get("beanInsertFTmpt"),
        "first_crck_set_c_temp": prflM.get("firstCrckSetCTemp"),
        "first_crck_set_f_temp": prflM.get("firstCrckSetFTemp"),
        "dschge_set_c_temp": prflM.get("dschgeSetCTemp"),
        "dschge_set_f_temp": prflM.get("dschgeSetFTemp"),
        "tp_time": prflM.get("tpTime"),
        "tp_in_c_temp": prflM.get("tpInCTemp"),
        "tp_in_f_temp": prflM.get("tpInFTemp"),
        "tp_ir_c_temp": prflM.get("tpIrCTemp"),
        "tp_ir_f_temp": prflM.get("tpIrFTemp"),
        "first_crk_time": prflM.get("firstCrkTime"),
        "first_crk_in_c_temp": prflM.get("firstCrkInCTemp"),
        "first_crk_in_f_temp": prflM.get("firstCrkInFTemp"),
        "first_crk_ir_c_temp": prflM.get("firstCrkIrCTemp"),
        "first_crk_ir_f_temp": prflM.get("firstCrkIrFTemp"),
        "second_crk_time": prflM.get("secondCrkTime"),
        "second_crk_in_c_temp": prflM.get("secondCrkInCTemp"),
        "second_crk_in_f_temp": prflM.get("secondCrkInFTemp"),
        "second_crk_ir_c_temp": prflM.get("secondCrkIrCTemp"),
        "second_crk_ir_f_temp": prflM.get("secondCrkIrFTemp"),
        "dschg_time": prflM.get("dschgTime"),
        "dschg_in_c_temp": prflM.get("dschgInCTemp") if prflM else list_item.get("dschgInCTemp"),
        "dschg_in_f_temp": prflM.get("dschgInFTemp") if prflM else list_item.get("dschgInFTemp"),
        "dschg_ir_c_temp": prflM.get("dschgIrCTemp") if prflM else list_item.get("dschgIrCTemp"),
        "dschg_ir_f_temp": prflM.get("dschgIrFTemp") if prflM else list_item.get("dschgIrFTemp"),
        "rep_to_man_time": prflM.get("repToManTime"),
        "rep_to_man_c_temp": prflM.get("repToManCTemp"),
        "rep_to_man_f_temp": prflM.get("repToManFTemp"),
        "rep_to_man_ir_c_temp": prflM.get("repToManIrCTemp"),
        "rep_to_man_ir_f_temp": prflM.get("repToManIrFTemp"),
        "base_temp": phase.get("baseTemp"),
        "drying_time": phase.get("dryingTime"),
        "drying_end_c_temp": phase.get("dryingEndCTemp"),
        "drying_end_f_temp": phase.get("dryingEndFTemp"),
        "drying_ratio": phase.get("dryingRatio"),
        "yellow_time": phase.get("yellowTime"),
        "yellow_end_c_temp": phase.get("yellowEndCTemp"),
        "yellow_end_f_temp": phase.get("yellowEndFTemp"),
        "yellow_ratio": phase.get("yellowRatio"),
        "develop_time": phase.get("developTime"),
        "develop_end_c_temp": phase.get("developEndCTemp"),
        "develop_end_f_temp": phase.get("developEndFTemp"),
        "develop_ratio": phase.get("developRatio"),
        "raw_list_json": json.dumps(list_item, ensure_ascii=False),
        "raw_detail_json": json.dumps(detail, ensure_ascii=False) if detail else None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    updates = ", ".join(f"{k}=excluded.{k}" for k in row.keys() if k != "prfl_m_cd")
    sql = f"INSERT INTO roasts ({cols}) VALUES ({placeholders}) ON CONFLICT(prfl_m_cd) DO UPDATE SET {updates}"
    conn.execute(sql, list(row.values()))

    if detail:
        temps = detail.get("temps", []) or []
        controls = detail.get("controls", []) or []
        conn.execute("DELETE FROM roast_temps WHERE prfl_m_cd=?", (prfl_m_cd,))
        conn.executemany(
            "INSERT INTO roast_temps (prfl_m_cd, rst_time, internal_c_temp, internal_f_temp, "
            "tower_drum_c_temp, tower_drum_f_temp, hot_air_c_temp, hot_air_f_temp, "
            "band_c_temp, band_f_temp, ir_c_temp, ir_f_temp) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (prfl_m_cd, t.get("rstTime"), t.get("internalCTemp"), t.get("internalFTemp"),
                 t.get("towerDrumCTemp"), t.get("towerDrumFTemp"), t.get("hotAirCTemp"), t.get("hotAirFTemp"),
                 t.get("bandCTemp"), t.get("bandFTemp"), t.get("irCTemp"), t.get("irFTemp"))
                for t in temps
            ],
        )
        conn.execute("DELETE FROM roast_controls WHERE prfl_m_cd=?", (prfl_m_cd,))
        conn.executemany(
            "INSERT INTO roast_controls (prfl_m_cd, rst_time, hot_air_lvl, hlgn_lvl, band_lvl, agt_lvl, blower_lvl) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (prfl_m_cd, c.get("rstTime"), c.get("hotAirLvl"), c.get("hlgnLvl"),
                 c.get("bandLvl"), c.get("agtLvl"), c.get("blowerLvl"))
                for c in controls
            ],
        )


async def main_async(args, cookies):
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    async with httpx.AsyncClient(cookies=cookies, timeout=30) as client:
        print("Fetching total roast count...", file=sys.stderr)
        expected_total = await fetch_total_count(client, PROD_CD)
        print(f"Expected total (from status/all/list): {expected_total}", file=sys.stderr)

        print("Fetching full roast list (paginated)...", file=sys.stderr)
        all_profiles = await fetch_all_roast_list(client, PROD_CD, expected_total=expected_total, stop_at=args.limit)
        print(f"Total roasts found: {len(all_profiles)}", file=sys.stderr)

        if args.limit:
            all_profiles = all_profiles[: args.limit]
            print(f"Limiting to first {args.limit} for this run.", file=sys.stderr)

        if args.resume:
            existing = set(
                r[0] for r in conn.execute("SELECT prfl_m_cd FROM roasts WHERE raw_detail_json IS NOT NULL")
            )
            before = len(all_profiles)
            all_profiles = [p for p in all_profiles if p["prflMCd"] not in existing]
            print(f"Resume mode: skipping {before - len(all_profiles)} already-fetched roasts, {len(all_profiles)} remaining.", file=sys.stderr)

        sem = asyncio.Semaphore(args.concurrency)
        results_lock = asyncio.Lock()
        done_count = [0]
        total = len(all_profiles)

        async def process(list_item):
            async with sem:
                detail = await fetch_detail(client, PROD_CD, list_item["prflMCd"])
            async with results_lock:
                upsert_roast(conn, list_item, detail)
                conn.commit()
                done_count[0] += 1
                if done_count[0] % 100 == 0 or done_count[0] == total:
                    print(f"  progress: {done_count[0]}/{total}", file=sys.stderr)

        t0 = time.time()
        await asyncio.gather(*[process(p) for p in all_profiles])
        t1 = time.time()
        print(f"Done. Processed {total} roasts in {t1-t0:.1f}s", file=sys.stderr)

    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit number of roasts (for testing)")
    parser.add_argument("--concurrency", type=int, default=15, help="Concurrent detail requests")
    parser.add_argument("--resume", action="store_true", help="Skip roasts already fully fetched in DB")
    args = parser.parse_args()
    cookies = login_and_get_cookies()
    print("Logged in, cookies obtained.", file=sys.stderr)
    asyncio.run(main_async(args, cookies))


if __name__ == "__main__":
    main()
