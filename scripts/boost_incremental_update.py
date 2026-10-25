#!/usr/bin/env python3
"""
Incremental updater for the Boost roasts SQLite DB.

Checks the most recent roasts on Boost (first page of the list, which is
sorted newest-first) and inserts any that aren't yet in the local DB.
Designed to run frequently (e.g. every minute) via cron — cheap: only hits
the API, no browser UI interaction beyond login.

Prints a short summary ONLY when new roasts were added (silence otherwise),
so a no-agent cron job naturally stays quiet except when there's real news.
"""
import sys
import os
import asyncio
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boost_build_db import (  # noqa: E402
    login_and_get_cookies, init_db, upsert_roast, fetch_detail,
    BASE_URL, PROD_CD, PAGE_SET_CNT, DB_PATH,
)
import httpx
import urllib.parse


async def main_async(cookies):
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    cur = conn.execute("SELECT prfl_m_cd FROM roasts")
    existing = set(r[0] for r in cur.fetchall())

    async with httpx.AsyncClient(cookies=cookies, timeout=20) as client:
        params = {
            "rc": "w0", "prodCd": PROD_CD, "page": "1", "pageSetCnt": str(PAGE_SET_CNT),
            "minRstTime": "0", "maxRstTime": "99999", "prflName": "", "grnBeanName": "",
            "startRstDt": "0", "endRstDt": "0",
        }
        qs = urllib.parse.urlencode(params)
        resp = await client.get(f"{BASE_URL}/rstry/profile/status/list.sq?{qs}")
        resp.raise_for_status()
        profiles = resp.json().get("profiles", [])

        new_items = [p for p in profiles if p["prflMCd"] not in existing]
        if not new_items:
            conn.close()
            return []  # nothing new, stay silent

        added = []
        for item in new_items:
            detail = await fetch_detail(client, PROD_CD, item["prflMCd"])
            upsert_roast(conn, item, detail)
            conn.commit()
            added.append(item["prflName"])

    conn.close()
    return added


def main():
    cookies = login_and_get_cookies()
    added = asyncio.run(main_async(cookies))
    if added:
        print(f"Nowe wypały dodane do bazy ({len(added)}): {', '.join(added)}")

    # keep the green bean inventory in sync with any bean_name set on roasts
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from inventory_sync import main_db_sync  # noqa: E402
    main_db_sync(quiet=True)


if __name__ == "__main__":
    main()
