#!/usr/bin/env python3
"""
Parse manual roast notes (entered in Boost's "Notes"/memo field for each
roast) into the 3 manual-entry tables: whole_bean_color, ground_bean_color,
post_roast_weight. Also computes weight-loss % (vs. green bean input weight)
and stores it as a note on the post_roast_weight row.

Expected note format (free word order, Polish/English mixed OK):
    "kolor cały <num> zmielony <num> waga <num>g"
e.g. "kolor cały 55 zmielony 80 waga 480g"

Numbers for cały (whole) are expected in Agtron ~40-70, zmielony (ground) ~70-120,
waga (weight) in grams, typically 400-550g (roasted from a 600g charge).

Usage:
    python3 boost_parse_notes.py [--dry-run]

Safe to re-run: re-parses the roasts.memo field and REPLACES existing manual
rows for that prfl_m_cd each time (so edits to Notes in Boost propagate).
"""
import os
import re
import sys
import sqlite3
import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import DB_PATH  # noqa: E402

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

# Matches "cały 55", "caly 55.5", "whole 55"
RE_WHOLE = re.compile(r"(?:ca[łl]y|whole)\D{0,10}?(\d+(?:[.,]\d+)?)", re.IGNORECASE)
# Matches "zmielony 80", "zmielona 80", "ground 80"
RE_GROUND = re.compile(r"(?:zmielon\w*|ground)\D{0,10}?(\d+(?:[.,]\d+)?)", re.IGNORECASE)
# Matches "waga 480g", "waga 480 g", "weight 480g"
RE_WEIGHT = re.compile(r"(?:waga|weight)\D{0,10}?(\d+(?:[.,]\d+)?)\s*g?", re.IGNORECASE)


def to_float(s):
    return float(s.replace(",", ".")) if s else None


def parse_note(text):
    """Return (whole_color, ground_color, weight_g) tuple, any may be None."""
    if not text:
        return None, None, None
    m_whole = RE_WHOLE.search(text)
    m_ground = RE_GROUND.search(text)
    m_weight = RE_WEIGHT.search(text)
    whole = to_float(m_whole.group(1)) if m_whole else None
    ground = to_float(m_ground.group(1)) if m_ground else None
    weight = to_float(m_weight.group(1)) if m_weight else None
    return whole, ground, weight


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show what would be parsed without writing")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT prfl_m_cd, prfl_name, memo, grn_bean_amt FROM roasts WHERE memo IS NOT NULL AND TRIM(memo) != ''")
    rows = cur.fetchall()
    print(f"Found {len(rows)} roasts with a non-empty memo/notes field.", file=sys.stderr)

    parsed_count = 0
    now = datetime.now(WARSAW_TZ).isoformat()

    for prfl_m_cd, prfl_name, memo, grn_bean_amt in rows:
        whole, ground, weight_g = parse_note(memo)
        if whole is None and ground is None and weight_g is None:
            continue
        parsed_count += 1

        loss_pct = None
        weight_kg = None
        if weight_g is not None:
            weight_kg = weight_g / 1000.0
            if grn_bean_amt:
                loss_pct = round((1 - weight_kg / grn_bean_amt) * 100, 2)

        print(f"{prfl_name} ({prfl_m_cd}): whole={whole} ground={ground} weight_g={weight_g} "
              f"green_kg={grn_bean_amt} loss_pct={loss_pct} | raw memo: {memo!r}", file=sys.stderr)

        if args.dry_run:
            continue

        if whole is not None:
            cur.execute("DELETE FROM whole_bean_color WHERE prfl_m_cd=?", (prfl_m_cd,))
            cur.execute(
                "INSERT INTO whole_bean_color (prfl_m_cd, color_value, color_unit, measured_at, notes) VALUES (?,?,?,?,?)",
                (prfl_m_cd, whole, "Agtron", now, memo),
            )
        if ground is not None:
            cur.execute("DELETE FROM ground_bean_color WHERE prfl_m_cd=?", (prfl_m_cd,))
            cur.execute(
                "INSERT INTO ground_bean_color (prfl_m_cd, color_value, color_unit, measured_at, notes) VALUES (?,?,?,?,?)",
                (prfl_m_cd, ground, "Agtron", now, memo),
            )
        if weight_kg is not None:
            cur.execute("DELETE FROM post_roast_weight WHERE prfl_m_cd=?", (prfl_m_cd,))
            note = memo
            if loss_pct is not None:
                note = f"{memo} | utrata wagi: {loss_pct}% (green {grn_bean_amt}kg -> {weight_kg}kg)"
            cur.execute(
                "INSERT INTO post_roast_weight (prfl_m_cd, weight_kg, measured_at, notes) VALUES (?,?,?,?)",
                (prfl_m_cd, weight_kg, now, note),
            )

    if not args.dry_run:
        conn.commit()
    print(f"\nParsed {parsed_count}/{len(rows)} notes successfully.{' (dry run, nothing written)' if args.dry_run else ''}", file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()
