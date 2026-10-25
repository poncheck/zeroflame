#!/usr/bin/env python3
"""
Quick summary / sanity-check CLI for the Boost roasts SQLite DB.

Usage:
    python3 boost_db_summary.py                  # overview
    python3 boost_db_summary.py --today          # today's roasts
    python3 boost_db_summary.py --manual         # roasts with manual data filled in
"""
import os
import sys
import sqlite3
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import DB_PATH  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", action="store_true")
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if args.today:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cur.execute(
            "SELECT prfl_name, rst_dt_iso, grn_bean_amt, dev_time, dtr, bean_name, memo "
            "FROM roasts WHERE rst_dt_iso LIKE ? ORDER BY rst_dt", (f"{today}%",)
        )
        rows = cur.fetchall()
        print(f"Roasts today ({today} UTC): {len(rows)}")
        for r in rows:
            print(f"  {r['prfl_name']:25s} {r['rst_dt_iso']} bean={r['bean_name']!r} grn={r['grn_bean_amt']}kg "
                  f"dev={r['dev_time']}s dtr={r['dtr']}% memo={r['memo']!r}")
        return

    if args.manual:
        cur.execute("""
            SELECT r.prfl_name, r.rst_dt_iso, w.color_value AS whole_c, g.color_value AS ground_c,
                   p.weight_kg, r.grn_bean_amt
            FROM roasts r
            LEFT JOIN whole_bean_color w ON w.prfl_m_cd = r.prfl_m_cd
            LEFT JOIN ground_bean_color g ON g.prfl_m_cd = r.prfl_m_cd
            LEFT JOIN post_roast_weight p ON p.prfl_m_cd = r.prfl_m_cd
            WHERE w.id IS NOT NULL OR g.id IS NOT NULL OR p.id IS NOT NULL
            ORDER BY r.rst_dt DESC
        """)
        rows = cur.fetchall()
        print(f"Roasts with manual data entered: {len(rows)}")
        for r in rows:
            loss = None
            if r["weight_kg"] and r["grn_bean_amt"]:
                loss = round((1 - r["weight_kg"] / r["grn_bean_amt"]) * 100, 2)
            print(f"  {r['prfl_name']:25s} {r['rst_dt_iso']} whole={r['whole_c']} ground={r['ground_c']} "
                  f"weight={r['weight_kg']}kg loss={loss}%")
        return

    # default overview
    cur.execute("SELECT COUNT(*) AS c FROM roasts")
    total = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM roasts WHERE raw_detail_json IS NOT NULL")
    with_detail = cur.fetchone()["c"]
    cur.execute("SELECT MIN(rst_dt_iso) AS a, MAX(rst_dt_iso) AS b FROM roasts")
    span = cur.fetchone()
    cur.execute("SELECT COUNT(DISTINCT bean_cd) AS c FROM roasts WHERE bean_cd IS NOT NULL")
    beans = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM whole_bean_color")
    wc = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM ground_bean_color")
    gc = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) AS c FROM post_roast_weight")
    pw = cur.fetchone()["c"]

    print(f"Total roasts in DB:        {total}")
    print(f"  with full detail fetched: {with_detail}")
    print(f"  date range:               {span['a']} .. {span['b']}")
    print(f"  distinct green beans:     {beans}")
    print(f"Manual entries:")
    print(f"  whole_bean_color:  {wc}")
    print(f"  ground_bean_color: {gc}")
    print(f"  post_roast_weight: {pw}")


if __name__ == "__main__":
    main()
