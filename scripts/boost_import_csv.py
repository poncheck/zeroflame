#!/usr/bin/env python3
"""
Import manual roast data (post-roast weight, whole/ground bean Agtron color)
from the historical "Wypaly.csv" export into the boost_roasts.db manual tables.

Matching strategy: CSV "Batch" name + roast date (day resolution) must match
EXACTLY ONE roast in the `roasts` table (by prfl_name + rst_dt_iso date part).
Ambiguous matches (multiple roast ids sharing the same name+date) and rows
with no reliable date are skipped and reported, never guessed.

Usage:
    python3 boost_import_csv.py --csv /path/Wypaly.csv --db /path/boost_roasts.db           # dry run (default)
    python3 boost_import_csv.py --csv /path/Wypaly.csv --db /path/boost_roasts.db --apply    # actually write
"""
import argparse
import csv
import datetime as dt
import re
import sqlite3
from collections import defaultdict

PL_MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "wrzesnia": 9, "wrze\u015bnia": 9,
    "pazdziernika": 10, "pa\u017adziernika": 10, "listopada": 11, "grudnia": 12,
}


def to_float(s):
    if s is None:
        return None
    s = s.strip().replace(" ", "")
    if s in ("", "0"):
        return None
    s = s.replace(",", ".").rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return dt.date(y, mo, d)
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})\s+([a-z\u017c\u017a\u0107\u0144\u00f3\u0142\u0119\u0105\u015b]+)\s+(\d{4})$", s, re.IGNORECASE)
    if m:
        d = int(m.group(1))
        monthname = m.group(2).lower()
        y = int(m.group(3))
        mo = PL_MONTHS.get(monthname)
        if mo:
            try:
                return dt.date(y, mo, d)
            except ValueError:
                return None
    return None


def find_col(header_cells, keyword):
    kw = keyword.lower()
    for idx, c in enumerate(header_cells):
        if kw in c.strip().lower():
            return idx
    return None


def parse_csv(csv_path):
    with open(csv_path, encoding="utf-8-sig", errors="replace") as f:
        lines = [l.rstrip("\r\n") for l in f.readlines()]

    header_idxs = [i for i, l in enumerate(lines) if l.split(";")[0].strip().lower() == "batch"]

    sections = []
    for h in header_idxs:
        group_header = lines[h - 1].split(";")
        title = None
        for back in range(1, 6):
            if h - 1 - back < 0:
                break
            cand = lines[h - 1 - back]
            if cand.strip() and cand.count(";") < 5:
                title = cand.strip()
                break
        end = h + 1
        while end < len(lines) and lines[end].strip() != "":
            end += 1
        sections.append(dict(
            title=title, data_start=h + 1, data_end=end,
            zasyp_idx=find_col(group_header, "zasyp"),
            wysyp_idx=find_col(group_header, "ysyp"),
            kolor_idx=find_col(group_header, "kolor"),
        ))

    records = []
    for s in sections:
        for i in range(s["data_start"], s["data_end"]):
            cells = lines[i].split(";")
            batch = cells[0].strip() if cells else ""
            if not batch:
                continue
            date_str = cells[1].strip() if len(cells) > 1 else ""
            wysyp_idx = s["wysyp_idx"]
            kolor_idx = s["kolor_idx"]
            wysyp = to_float(cells[wysyp_idx]) if wysyp_idx is not None and len(cells) > wysyp_idx else None
            whole = to_float(cells[kolor_idx]) if kolor_idx is not None and len(cells) > kolor_idx else None
            ground = to_float(cells[kolor_idx + 2]) if kolor_idx is not None and len(cells) > kolor_idx + 2 else None
            records.append(dict(
                section=s["title"], batch=batch, date=parse_date(date_str),
                wysyp_g=wysyp, whole_agtron=whole, ground_agtron=ground,
            ))
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true", help="Actually write to DB. Default is dry-run.")
    ap.add_argument("--source-note", default="import z Wyp\u0142a\u0142y.csv (historyczne dane)")
    args = ap.parse_args()

    records = parse_csv(args.csv)
    print(f"Parsed {len(records)} CSV rows across all sections.")

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    cur.execute("SELECT prfl_name, rst_dt_iso, prfl_m_cd FROM roasts")
    name_date_to_cds = defaultdict(list)
    for name, iso, cd in cur.fetchall():
        if iso:
            name_date_to_cds[(name, iso[:10])].append(cd)

    cur.execute("SELECT prfl_m_cd, weight_kg FROM post_roast_weight")
    existing_weight = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT prfl_m_cd, color_value FROM whole_bean_color")
    existing_whole = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT prfl_m_cd, color_value FROM ground_bean_color")
    existing_ground = {r[0]: r[1] for r in cur.fetchall()}

    stats = dict(no_date=0, no_name_match=0, ambiguous=0, matched=0,
                 skip_weight_conflict=0, skip_whole_conflict=0, skip_ground_conflict=0,
                 insert_weight=0, insert_whole=0, insert_ground=0)

    now_iso = dt.datetime.now().isoformat()
    inserts_weight, inserts_whole, inserts_ground = [], [], []

    for r in records:
        if not r["date"]:
            stats["no_date"] += 1
            continue
        key = (r["batch"], r["date"].isoformat())
        cds = name_date_to_cds.get(key, [])
        if not cds:
            stats["no_name_match"] += 1
            continue
        if len(cds) > 1:
            stats["ambiguous"] += 1
            continue
        stats["matched"] += 1
        cd = cds[0]

        if r["wysyp_g"]:
            if cd in existing_weight:
                stats["skip_weight_conflict"] += 1
            else:
                stats["insert_weight"] += 1
                inserts_weight.append((cd, r["wysyp_g"] / 1000.0, now_iso,
                                        f"{args.source_note} (batch={r['batch']})"))
        if r["whole_agtron"]:
            if cd in existing_whole:
                stats["skip_whole_conflict"] += 1
            else:
                stats["insert_whole"] += 1
                inserts_whole.append((cd, r["whole_agtron"], "agtron", now_iso,
                                       f"{args.source_note} (batch={r['batch']})"))
        if r["ground_agtron"]:
            if cd in existing_ground:
                stats["skip_ground_conflict"] += 1
            else:
                stats["insert_ground"] += 1
                inserts_ground.append((cd, r["ground_agtron"], "agtron", now_iso,
                                        f"{args.source_note} (batch={r['batch']})"))

    print()
    print("=== DRY RUN SUMMARY ===" if not args.apply else "=== APPLYING CHANGES ===")
    for k, v in stats.items():
        print(f"  {k:24s}: {v}")

    if args.apply:
        cur.executemany(
            "INSERT INTO post_roast_weight (prfl_m_cd, weight_kg, measured_at, notes) VALUES (?, ?, ?, ?)",
            inserts_weight)
        cur.executemany(
            "INSERT INTO whole_bean_color (prfl_m_cd, color_value, color_unit, measured_at, notes) VALUES (?, ?, ?, ?, ?)",
            inserts_whole)
        cur.executemany(
            "INSERT INTO ground_bean_color (prfl_m_cd, color_value, color_unit, measured_at, notes) VALUES (?, ?, ?, ?, ?)",
            inserts_ground)
        conn.commit()
        print()
        print(f"Committed: {len(inserts_weight)} weight rows, {len(inserts_whole)} whole-color rows, {len(inserts_ground)} ground-color rows.")
    else:
        print()
        print("Dry run only -- nothing written. Re-run with --apply to commit.")

    conn.close()


if __name__ == "__main__":
    main()
