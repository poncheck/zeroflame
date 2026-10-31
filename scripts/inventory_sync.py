#!/usr/bin/env python3
"""
Keeps green_bean_inventory.db's stock_movements / beans in sync with
boost_roasts.db's roasts.bean_name field.

For every roast that has a bean_name set (whether assigned automatically
by Boost's own bean-linking, or manually after editing roasts.bean_name),
ensures there's a corresponding 'usage' stock_movements row. Then recomputes
each affected bean's current_stock_kg (sum of deliveries - sum of usage),
roast_count and last_used.

Safe to run repeatedly (idempotent): skips roasts that already have a
usage movement recorded (matched by reference_cd).

Usage:
  python3 inventory_sync.py                 # sync all unsynced roasts
  python3 inventory_sync.py --bean KCH       # only recompute totals for one bean
"""
import os
import sys
import sqlite3
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import DB_PATH as MAIN_DB, INV_DB_PATH as INV_DB  # noqa: E402


def sync_new_usage_movements(main_conn, inv_conn):
    """Insert a usage movement for any roast with bean_name set that doesn't have one yet."""
    main_cur = main_conn.cursor()
    inv_cur = inv_conn.cursor()

    inv_cur.execute("SELECT reference_cd FROM stock_movements WHERE reference_cd IS NOT NULL")
    already_synced = {r[0] for r in inv_cur.fetchall()}

    inv_cur.execute("SELECT code FROM beans")
    known_beans = {r[0] for r in inv_cur.fetchall()}

    main_cur.execute(
        "SELECT prfl_m_cd, prfl_name, bean_name, grn_bean_amt, rst_dt_iso "
        "FROM roasts WHERE bean_name IS NOT NULL AND bean_name != ''"
    )
    rows = main_cur.fetchall()

    inserted = []
    unknown_bean_roasts = []
    for prfl_m_cd, prfl_name, bean_name, grn_bean_amt, rst_dt_iso in rows:
        if prfl_m_cd in already_synced:
            continue
        if bean_name not in known_beans:
            unknown_bean_roasts.append((prfl_m_cd, prfl_name, bean_name))
            continue
        amount = -(grn_bean_amt or 0.6)
        inv_cur.execute(
            "INSERT INTO stock_movements (bean_code, movement_type, amount_kg, movement_date, notes, source, reference_cd) "
            "VALUES (?, 'usage', ?, ?, ?, 'roast_auto', ?)",
            (bean_name, amount, rst_dt_iso, f"wypał: {prfl_name}", prfl_m_cd),
        )
        inserted.append((bean_name, prfl_name, amount))

    inv_conn.commit()
    return inserted, unknown_bean_roasts


def recompute_bean_stats(inv_conn, bean_code=None):
    """Recompute current_stock_kg, roast_count, batch_count, first_used, last_used for one or all beans."""
    inv_cur = inv_conn.cursor()
    main_conn = sqlite3.connect(MAIN_DB)
    main_cur = main_conn.cursor()

    if bean_code:
        codes = [bean_code]
    else:
        codes = [r[0] for r in inv_cur.execute("SELECT code FROM beans").fetchall()]

    for code in codes:
        inv_cur.execute(
            "SELECT COALESCE(SUM(amount_kg), 0) FROM stock_movements WHERE bean_code=? AND movement_type='delivery'",
            (code,),
        )
        delivered = inv_cur.fetchone()[0]
        inv_cur.execute(
            "SELECT COALESCE(SUM(-amount_kg), 0) FROM stock_movements WHERE bean_code=? AND movement_type='usage'",
            (code,),
        )
        used = inv_cur.fetchone()[0]
        inv_cur.execute(
            "SELECT COALESCE(SUM(amount_kg), 0) FROM stock_movements WHERE bean_code=? AND movement_type='adjustment'",
            (code,),
        )
        adjusted = inv_cur.fetchone()[0]
        stock = delivered - used + adjusted

        inv_cur.execute(
            "SELECT COUNT(*), MIN(movement_date), MAX(movement_date) FROM stock_movements "
            "WHERE bean_code=? AND movement_type='usage'",
            (code,),
        )
        roast_count, first_used, last_used = inv_cur.fetchone()

        main_cur.execute(
            "SELECT COUNT(DISTINCT prfl_name) FROM roasts WHERE bean_name=?",
            (code,),
        )
        batch_count = main_cur.fetchone()[0]

        inv_cur.execute(
            "UPDATE beans SET current_stock_kg=?, roast_count=?, batch_count=?, first_used=COALESCE(?, first_used), "
            "last_used=COALESCE(?, last_used) WHERE code=?",
            (stock, roast_count, batch_count, first_used, last_used, code),
        )
    inv_conn.commit()
    main_conn.close()


def reassign_roast_bean(main_conn, inv_conn, prfl_m_cd, new_bean_code, new_grn_bean_amt):
    """Change which bean a saved roast is attributed to (and/or its green-bean
    weight), used from the roast detail page — e.g. roasts synced from Boost
    with no bean assigned yet, or a wrong assignment that needs fixing.

    Keeps stock_movements in sync: removes the old 'usage' movement tied to
    this roast (if any — it belonged to whatever bean/weight was set before),
    inserts a fresh one for the new bean/weight (if a bean is now set), and
    recomputes current_stock_kg/roast_count for every bean affected so the
    inventory numbers never drift from what's actually on roasts.

    new_bean_code may be None/'' to explicitly clear the assignment (roast
    goes back to having no bean, e.g. to fix a wrong auto-match) — in that
    case no new usage movement is inserted.
    """
    main_cur = main_conn.cursor()
    inv_cur = inv_conn.cursor()

    main_cur.execute("SELECT bean_name, grn_bean_amt, prfl_name, rst_dt_iso FROM roasts WHERE prfl_m_cd=?", (prfl_m_cd,))
    row = main_cur.fetchone()
    if not row:
        raise ValueError(f"No roast found for prfl_m_cd={prfl_m_cd}")
    old_bean_code, _old_amt, prfl_name, rst_dt_iso = row

    new_bean_code = (new_bean_code or "").strip() or None

    # Clear any previous usage movement tied to this specific roast — it no
    # longer reflects reality once the bean/weight changes.
    inv_cur.execute("DELETE FROM stock_movements WHERE reference_cd=?", (prfl_m_cd,))

    main_cur.execute(
        "UPDATE roasts SET bean_name=?, grn_bean_amt=? WHERE prfl_m_cd=?",
        (new_bean_code, new_grn_bean_amt, prfl_m_cd),
    )
    main_conn.commit()

    affected = set()
    if old_bean_code:
        affected.add(old_bean_code)

    if new_bean_code:
        known = inv_cur.execute("SELECT 1 FROM beans WHERE code=?", (new_bean_code,)).fetchone()
        if known:
            amount = -(new_grn_bean_amt or 0.6)
            inv_cur.execute(
                "INSERT INTO stock_movements (bean_code, movement_type, amount_kg, movement_date, notes, source, reference_cd) "
                "VALUES (?, 'usage', ?, ?, ?, 'roast_manual_reassign', ?)",
                (new_bean_code, amount, rst_dt_iso, f"wypał: {prfl_name} (ziarno przypisane ręcznie)", prfl_m_cd),
            )
            affected.add(new_bean_code)
        # else: bean code not in inventory yet — roasts.bean_name is still set
        # (so cupping/detail pages show it), but no stock movement is created
        # until the bean exists in `beans` (same behaviour as the auto-sync path).

    inv_conn.commit()

    for code in affected:
        recompute_bean_stats(inv_conn, code)

    return {"old_bean_code": old_bean_code, "new_bean_code": new_bean_code, "affected_beans": sorted(affected)}


def main_db_sync(quiet=False):
    """Programmatic entry point: sync + recompute, print only if something changed (unless quiet=False forces always)."""
    main_conn = sqlite3.connect(MAIN_DB)
    inv_conn = sqlite3.connect(INV_DB)

    inserted, unknown = sync_new_usage_movements(main_conn, inv_conn)
    affected_beans = {b for b, _, _ in inserted}
    for b in affected_beans:
        recompute_bean_stats(inv_conn, b)

    main_conn.close()
    inv_conn.close()

    if inserted:
        print(f"Magazyn: zsynchronizowano {len(inserted)} nowych ruchów (zużycie): "
              + ", ".join(f"{p} ({b})" for b, p, _ in inserted))
    if unknown:
        names = ", ".join(f"{p} ({b})" for _, p, b in unknown[:10])
        more = f" i {len(unknown) - 10} więcej" if len(unknown) > 10 else ""
        print(f"Magazyn UWAGA: {len(unknown)} wypałów ma ziarno nieznane w magazynie: {names}{more}")
    if not inserted and not unknown and not quiet:
        print("Magazyn: brak nowych ruchów do zsynchronizowania.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bean", default=None, help="Only recompute totals for this bean code")
    parser.add_argument("--quiet", action="store_true", help="Suppress output when nothing changed")
    args = parser.parse_args()

    main_conn = sqlite3.connect(MAIN_DB)
    inv_conn = sqlite3.connect(INV_DB)

    inserted, unknown = sync_new_usage_movements(main_conn, inv_conn)
    affected_beans = {b for b, _, _ in inserted}
    if args.bean:
        recompute_bean_stats(inv_conn, args.bean)
    elif affected_beans:
        for b in affected_beans:
            recompute_bean_stats(inv_conn, b)
    elif not args.quiet:
        recompute_bean_stats(inv_conn)  # cheap safety net, full recompute if explicitly asked with no new movements

    main_conn.close()
    inv_conn.close()

    if inserted:
        print(f"Zsynchronizowano {len(inserted)} nowych ruchów magazynowych (zużycie): "
              + ", ".join(f"{p} ({b})" for b, p, _ in inserted))
    if unknown and not args.quiet:
        names = ", ".join(f"{p} ({b})" for _, p, b in unknown[:10])
        more = f" i {len(unknown) - 10} więcej" if len(unknown) > 10 else ""
        print(f"UWAGA: {len(unknown)} wypałów ma ziarno nieznane w magazynie: {names}{more}")
    if not inserted and not unknown and not args.quiet:
        print("Brak nowych ruchów do zsynchronizowania.")


if __name__ == "__main__":
    main()
