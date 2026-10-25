#!/usr/bin/env python3
"""
Lightweight read/edit web viewer for the Boost roasts SQLite DB.

Pages:
  /                     - overview dashboard (counts, latest roasts)
  /roasts               - paginated, searchable list of all roasts
  /roast/<prfl_m_cd>     - single roast detail: metadata, phases, temp/heat
                           curve chart, and a form to edit the 3 manual tables
  /api/roast/<id>/curve - JSON for the chart (temps + controls)

Run:
  python3 boost_webapp.py [--port 8088] [--host 0.0.0.0]

Configuration (via environment / .env, see common/config.py):
  ZEROFLAME_DATA_DIR  - directory holding boost_roasts.db / green_bean_inventory.db / auth.db
  FLASK_SECRET_KEY    - session signing key (set a real secret in production!)
"""
import os
import re
import sys
import sqlite3
import argparse
from functools import wraps
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, redirect, url_for, jsonify, abort, session
from werkzeug.security import check_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import DB_PATH, INV_DB_PATH, AUTH_DB_PATH, FLASK_SECRET_KEY  # noqa: E402

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

app = Flask(__name__)


def format_mmss(value):
    """Format a duration given in seconds as M:SS (e.g. 9:05). Returns '—' for None/empty."""
    if value is None or value == "":
        return "—"
    try:
        total_seconds = int(round(float(value)))
    except (TypeError, ValueError):
        return value
    sign = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    minutes, seconds = divmod(total_seconds, 60)
    return f"{sign}{minutes}:{seconds:02d}"


app.jinja_env.filters["mmss"] = format_mmss

app.secret_key = FLASK_SECRET_KEY
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def get_auth_conn():
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def require_login_everywhere():
    allowed = {"login", "static"}
    if request.endpoint in allowed or request.endpoint is None:
        return None
    if not session.get("user_id"):
        return redirect(url_for("login", next=request.path))
    return None


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_auth_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cur.fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["display_name"] = user["display_name"] or user["username"]
            cur.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user["id"],))
            conn.commit()
            conn.close()
            next_url = request.args.get("next") or url_for("index")
            if not next_url.startswith("/"):
                next_url = url_for("index")
            return redirect(next_url)
        conn.close()
        error = "Nieprawidłowy login lub hasło."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_inv_conn():
    conn = sqlite3.connect(INV_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) c FROM roasts")
    total = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM roasts WHERE raw_detail_json IS NOT NULL")
    with_detail = cur.fetchone()["c"]
    cur.execute("SELECT MIN(rst_dt_iso) a, MAX(rst_dt_iso) b FROM roasts")
    span = cur.fetchone()
    cur.execute("SELECT COUNT(DISTINCT bean_cd) c FROM roasts WHERE bean_cd IS NOT NULL")
    beans = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM whole_bean_color")
    wc = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM ground_bean_color")
    gc = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM post_roast_weight")
    pw = cur.fetchone()["c"]
    cur.execute("""
        SELECT r.prfl_m_cd, r.prfl_name, r.rst_dt_iso, r.bean_name, r.grn_bean_amt, r.dev_time, r.dtr
        FROM roasts r ORDER BY r.rst_dt DESC LIMIT 15
    """)
    recent = cur.fetchall()

    # today's roasts (Europe/Warsaw) with post-roast weight, grouped by bean, for the dashboard chart
    today_str = datetime.now(WARSAW_TZ).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT r.prfl_m_cd, r.prfl_name, r.rst_dt_iso, r.bean_name, w.weight_kg,
               wc.color_value as whole_color, gc.color_value as ground_color
        FROM roasts r
        JOIN post_roast_weight w ON w.prfl_m_cd = r.prfl_m_cd
        LEFT JOIN whole_bean_color wc ON wc.prfl_m_cd = r.prfl_m_cd
        LEFT JOIN ground_bean_color gc ON gc.prfl_m_cd = r.prfl_m_cd
        WHERE r.rst_dt_iso LIKE ? AND r.bean_name IS NOT NULL AND r.bean_name != ''
        ORDER BY r.rst_dt_iso ASC
    """, (today_str + "%",))
    today_weighings = cur.fetchall()
    conn.close()

    today_chart_data = None
    if today_weighings:
        by_bean = {}
        for r in today_weighings:
            by_bean.setdefault(r["bean_name"], []).append({
                "name": r["prfl_name"],
                "time": r["rst_dt_iso"][11:16] if r["rst_dt_iso"] else "",
                "weight": r["weight_kg"],
                "whole_color": r["whole_color"],
                "ground_color": r["ground_color"],
            })
        today_chart_data = {"date": today_str, "by_bean": by_bean}

    inv_conn = get_inv_conn()
    inv_codes = {r["code"] for r in inv_conn.execute("SELECT code FROM beans").fetchall()}
    inv_conn.close()

    return render_template(
        "index.html", total=total, with_detail=with_detail, span=span, beans=beans,
        wc=wc, gc=gc, pw=pw, recent=recent, inv_codes=inv_codes,
        today_chart_data=today_chart_data,
    )


RST_MOD_LABELS = {"1": "Manual", "2": "Replication", "3": "Program"}
RST_LVL_LABELS = {"1": "Light", "2": "Medium Light", "3": "Medium", "4": "Medium Dark", "5": "Dark"}

ROASTS_SORT_COLUMNS = {
    "name": "r.prfl_name",
    "date": "r.rst_dt",
    "bean": "r.bean_name",
    "amount": "r.grn_bean_amt",
    "mode": "r.rst_mod",
    "level": "r.rst_lvl",
    "dtr": "r.dtr",
    "whole_c": "w.color_value",
    "ground_c": "g.color_value",
    "weight": "p.weight_kg",
}


@app.route("/roasts")
def roasts_list():
    conn = get_conn()
    cur = conn.cursor()
    q = request.args.get("q", "").strip()
    bean = request.args.get("bean", "").strip()
    page = max(1, int(request.args.get("page", 1)))
    sort = request.args.get("sort", "date")
    direction = request.args.get("dir", "desc")
    if sort not in ROASTS_SORT_COLUMNS:
        sort = "date"
    if direction not in ("asc", "desc"):
        direction = "desc"
    page_size = 50
    offset = (page - 1) * page_size

    where = []
    params = []
    if q:
        where.append("(r.prfl_name LIKE ? OR r.memo LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if bean:
        where.append("r.bean_name = ?")
        params.append(bean)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(f"SELECT COUNT(*) c FROM roasts r {where_sql}", params)
    total = cur.fetchone()["c"]

    order_col = ROASTS_SORT_COLUMNS[sort]
    order_sql = f"{order_col} {direction.upper()}"
    if sort != "date":
        order_sql += ", r.rst_dt DESC"  # stable secondary sort

    cur.execute(f"""
        SELECT r.prfl_m_cd, r.prfl_name, r.rst_dt_iso, r.bean_name, r.grn_bean_amt,
               r.rst_mod, r.rst_lvl, r.dev_time, r.dtr, r.memo,
               w.color_value as whole_c, g.color_value as ground_c, p.weight_kg
        FROM roasts r
        LEFT JOIN whole_bean_color w ON w.prfl_m_cd = r.prfl_m_cd
        LEFT JOIN ground_bean_color g ON g.prfl_m_cd = r.prfl_m_cd
        LEFT JOIN post_roast_weight p ON p.prfl_m_cd = r.prfl_m_cd
        {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """, params + [page_size, offset])
    rows = cur.fetchall()

    cur.execute("SELECT DISTINCT bean_name FROM roasts WHERE bean_name IS NOT NULL AND bean_name != '' ORDER BY bean_name")
    bean_options = [r["bean_name"] for r in cur.fetchall()]
    conn.close()

    inv_conn = get_inv_conn()
    inv_codes = {r["code"] for r in inv_conn.execute("SELECT code FROM beans").fetchall()}
    inv_conn.close()

    total_pages = max(1, (total + page_size - 1) // page_size)
    return render_template(
        "roasts.html", rows=rows, total=total, page=page, total_pages=total_pages,
        q=q, bean=bean, bean_options=bean_options,
        rst_mod_labels=RST_MOD_LABELS, rst_lvl_labels=RST_LVL_LABELS,
        sort=sort, direction=direction, inv_codes=inv_codes,
    )


def _save_roast_manual_fields(conn, prfl_m_cd, whole, ground, weight, notes):
    """Shared save logic for whole/ground color + post-roast weight, used by both
    the full roast detail form and the inline quick-edit popup on the roasts list."""
    cur = conn.cursor()
    now = datetime.now(WARSAW_TZ).isoformat()

    cur.execute("SELECT grn_bean_amt FROM roasts WHERE prfl_m_cd=?", (prfl_m_cd,))
    row = cur.fetchone()
    grn_bean_amt = row["grn_bean_amt"] if row else None

    if whole:
        cur.execute("DELETE FROM whole_bean_color WHERE prfl_m_cd=?", (prfl_m_cd,))
        cur.execute(
            "INSERT INTO whole_bean_color (prfl_m_cd, color_value, color_unit, measured_at, notes) VALUES (?,?,?,?,?)",
            (prfl_m_cd, float(whole), "Agtron", now, notes),
        )
    if ground:
        cur.execute("DELETE FROM ground_bean_color WHERE prfl_m_cd=?", (prfl_m_cd,))
        cur.execute(
            "INSERT INTO ground_bean_color (prfl_m_cd, color_value, color_unit, measured_at, notes) VALUES (?,?,?,?,?)",
            (prfl_m_cd, float(ground), "Agtron", now, notes),
        )
    if weight:
        weight_kg = float(weight) / 1000.0
        base_notes = re.sub(r"\s*\|\s*utrata wagi:[^|]*", "", notes).strip()
        loss_note = base_notes
        if grn_bean_amt:
            loss_pct = round((1 - weight_kg / grn_bean_amt) * 100, 2)
            loss_note = f"{base_notes} | utrata wagi: {loss_pct}%".strip(" |")
        cur.execute("DELETE FROM post_roast_weight WHERE prfl_m_cd=?", (prfl_m_cd,))
        cur.execute(
            "INSERT INTO post_roast_weight (prfl_m_cd, weight_kg, measured_at, notes) VALUES (?,?,?,?)",
            (prfl_m_cd, weight_kg, now, loss_note),
        )
    conn.commit()


@app.route("/api/roast/<prfl_m_cd>/quick-edit", methods=["POST"])
def roast_quick_edit(prfl_m_cd):
    """Inline quick-edit used from the roasts list popup: save whole/ground color
    and post-roast weight without navigating to the roast detail page."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT prfl_m_cd FROM roasts WHERE prfl_m_cd=?", (prfl_m_cd,))
    if not cur.fetchone():
        conn.close()
        abort(404)

    whole = request.form.get("whole_color", "").strip()
    ground = request.form.get("ground_color", "").strip()
    weight = request.form.get("weight_g", "").strip()
    notes = request.form.get("notes", "").strip()
    try:
        _save_roast_manual_fields(conn, prfl_m_cd, whole, ground, weight, notes)
    except ValueError:
        conn.close()
        return jsonify({"error": "Nieprawidłowa wartość liczbowa."}), 400

    cur.execute("SELECT * FROM whole_bean_color WHERE prfl_m_cd=? ORDER BY id DESC LIMIT 1", (prfl_m_cd,))
    whole_row = cur.fetchone()
    cur.execute("SELECT * FROM ground_bean_color WHERE prfl_m_cd=? ORDER BY id DESC LIMIT 1", (prfl_m_cd,))
    ground_row = cur.fetchone()
    cur.execute("SELECT * FROM post_roast_weight WHERE prfl_m_cd=? ORDER BY id DESC LIMIT 1", (prfl_m_cd,))
    weight_row = cur.fetchone()
    conn.close()

    return jsonify({
        "ok": True,
        "whole_c": whole_row["color_value"] if whole_row else None,
        "ground_c": ground_row["color_value"] if ground_row else None,
        "weight_kg": weight_row["weight_kg"] if weight_row else None,
    })


@app.route("/roast/<prfl_m_cd>", methods=["GET", "POST"])
def roast_detail(prfl_m_cd):
    conn = get_conn()
    cur = conn.cursor()

    if request.method == "POST":
        whole = request.form.get("whole_color", "").strip()
        ground = request.form.get("ground_color", "").strip()
        weight = request.form.get("weight_g", "").strip()
        notes = request.form.get("notes", "").strip()
        _save_roast_manual_fields(conn, prfl_m_cd, whole, ground, weight, notes)
        conn.close()
        return redirect(url_for("roast_detail", prfl_m_cd=prfl_m_cd))

    cur.execute("SELECT * FROM roasts WHERE prfl_m_cd=?", (prfl_m_cd,))
    roast = cur.fetchone()
    if not roast:
        conn.close()
        abort(404)

    original_profile_cd = None
    if roast["up_prfl_name"]:
        up_name = roast["up_prfl_name"].strip()
        if up_name.startswith(":"):
            up_name = up_name[1:].strip()
        cur.execute(
            "SELECT prfl_m_cd FROM roasts WHERE prfl_name=? ORDER BY rst_dt ASC LIMIT 1",
            (up_name,),
        )
        orig_row = cur.fetchone()
        if orig_row and orig_row["prfl_m_cd"] != prfl_m_cd:
            original_profile_cd = orig_row["prfl_m_cd"]

    cur.execute("SELECT * FROM whole_bean_color WHERE prfl_m_cd=? ORDER BY id DESC LIMIT 1", (prfl_m_cd,))
    whole_row = cur.fetchone()
    cur.execute("SELECT * FROM ground_bean_color WHERE prfl_m_cd=? ORDER BY id DESC LIMIT 1", (prfl_m_cd,))
    ground_row = cur.fetchone()
    cur.execute("SELECT * FROM post_roast_weight WHERE prfl_m_cd=? ORDER BY id DESC LIMIT 1", (prfl_m_cd,))
    weight_row = cur.fetchone()

    loss_pct = None
    if weight_row and roast["grn_bean_amt"]:
        loss_pct = round((1 - weight_row["weight_kg"] / roast["grn_bean_amt"]) * 100, 2)

    bean_in_inventory = False
    if roast["bean_name"]:
        inv_conn = get_inv_conn()
        bean_in_inventory = inv_conn.execute(
            "SELECT 1 FROM beans WHERE code=?", (roast["bean_name"],)
        ).fetchone() is not None
        inv_conn.close()

    conn.close()
    return render_template(
        "detail.html", r=roast, whole_row=whole_row, ground_row=ground_row,
        weight_row=weight_row, loss_pct=loss_pct,
        rst_mod_labels=RST_MOD_LABELS, rst_lvl_labels=RST_LVL_LABELS,
        original_profile_cd=original_profile_cd, bean_in_inventory=bean_in_inventory,
    )


@app.route("/api/roast/<prfl_m_cd>/curve")
def roast_curve(prfl_m_cd):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT rst_time, internal_c_temp, tower_drum_c_temp, hot_air_c_temp FROM roast_temps "
        "WHERE prfl_m_cd=? ORDER BY rst_time", (prfl_m_cd,)
    )
    temps = [dict(r) for r in cur.fetchall()]
    cur.execute(
        "SELECT rst_time, hot_air_lvl, hlgn_lvl FROM roast_controls WHERE prfl_m_cd=? ORDER BY rst_time",
        (prfl_m_cd,)
    )
    controls = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"temps": temps, "controls": controls})


@app.route("/inventory")
def inventory_list():
    conn = get_inv_conn()
    cur = conn.cursor()
    status_filter = request.args.get("status", "").strip()
    tag_filter = request.args.get("tag", "").strip().upper()

    where = []
    params = []
    if status_filter:
        where.append("status = ?")
        params.append(status_filter)
    if tag_filter:
        where.append("code IN (SELECT bean_code FROM bean_tags WHERE tag = ?)")
        params.append(tag_filter)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(f"""
        SELECT b.*,
               (SELECT SUM(amount_kg) FROM stock_movements m WHERE m.bean_code = b.code AND m.movement_type='usage') as total_used_kg,
               (SELECT SUM(amount_kg) FROM stock_movements m WHERE m.bean_code = b.code AND m.movement_type='delivery') as total_delivered_kg
        FROM beans b
        {where_sql}
        ORDER BY CASE WHEN b.status='active' THEN 0 ELSE 1 END, b.last_used DESC
    """, params)
    beans = cur.fetchall()

    cur.execute("SELECT bean_code, tag FROM bean_tags ORDER BY tag")
    tags_by_bean = {}
    for row in cur.fetchall():
        tags_by_bean.setdefault(row["bean_code"], []).append(row["tag"])

    cur.execute("SELECT DISTINCT tag FROM bean_tags ORDER BY tag")
    all_tags = [row["tag"] for row in cur.fetchall()]

    cur.execute("SELECT COUNT(*) c FROM beans")
    total_beans = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM beans WHERE status='active'")
    active_beans = cur.fetchone()["c"]
    cur.execute("SELECT SUM(roast_count) c FROM beans")
    total_roasts_with_bean = cur.fetchone()["c"]
    conn.close()

    return render_template(
        "inventory.html", beans=beans, total_beans=total_beans,
        active_beans=active_beans, total_roasts_with_bean=total_roasts_with_bean,
        status_filter=status_filter, tags_by_bean=tags_by_bean, all_tags=all_tags,
        tag_filter=tag_filter,
    )


@app.route("/inventory/<code>/tags/add", methods=["POST"])
def inventory_add_tag(code):
    conn = get_inv_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM beans WHERE code=?", (code,))
    if not cur.fetchone():
        conn.close()
        abort(404)
    tag = request.form.get("tag", "").strip().upper().lstrip("#")
    if tag:
        cur.execute("INSERT OR IGNORE INTO bean_tags (bean_code, tag) VALUES (?, ?)", (code, tag))
        conn.commit()
    conn.close()
    return redirect(url_for("inventory_detail", code=code))


@app.route("/inventory/<code>/tags/<tag>/delete", methods=["POST"])
def inventory_delete_tag(code, tag):
    conn = get_inv_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM bean_tags WHERE bean_code=? AND tag=?", (code, tag))
    conn.commit()
    conn.close()
    return redirect(url_for("inventory_detail", code=code))


@app.route("/inventory/<code>/set-status", methods=["POST"])
def inventory_set_status(code):
    new_status = request.form.get("status", "").strip()
    if new_status not in ("active", "historical"):
        abort(400)
    conn = get_inv_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM beans WHERE code=?", (code,))
    if not cur.fetchone():
        conn.close()
        abort(404)
    cur.execute("UPDATE beans SET status=? WHERE code=?", (new_status, code))
    conn.commit()
    conn.close()
    next_url = request.form.get("next") or url_for("inventory_detail", code=code)
    return redirect(next_url)


@app.route("/inventory/<code>/edit", methods=["POST"])
def inventory_edit(code):
    conn = get_inv_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM beans WHERE code=?", (code,))
    if not cur.fetchone():
        conn.close()
        abort(404)

    fields = ["full_name", "country", "region", "farm", "variety", "processing", "source_note"]
    updates = {f: request.form.get(f, "").strip() or None for f in fields}
    set_clause = ", ".join(f"{f}=?" for f in fields)
    cur.execute(f"UPDATE beans SET {set_clause} WHERE code=?", (*updates.values(), code))
    conn.commit()
    conn.close()
    return redirect(url_for("inventory_detail", code=code))


@app.route("/inventory/<code>/moisture", methods=["POST"])
def inventory_add_moisture(code):
    conn = get_inv_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM beans WHERE code=?", (code,))
    if not cur.fetchone():
        conn.close()
        abort(404)

    moisture_pct = request.form.get("moisture_pct", "").strip()
    density = request.form.get("density", "").strip()
    bean_temp_c = request.form.get("bean_temp_c", "").strip()
    measured_date = request.form.get("measured_date", "").strip()
    notes = request.form.get("notes", "").strip() or None
    if not measured_date or not (moisture_pct or density or bean_temp_c):
        conn.close()
        abort(400)

    measured_at = datetime.strptime(measured_date, "%Y-%m-%d").replace(
        hour=12, tzinfo=WARSAW_TZ
    ).isoformat()

    cur.execute(
        "INSERT INTO moisture_readings (bean_code, moisture_pct, density, bean_temp_c, measured_at, notes) VALUES (?,?,?,?,?,?)",
        (code,
         float(moisture_pct) if moisture_pct else None,
         float(density) if density else None,
         float(bean_temp_c) if bean_temp_c else None,
         measured_at, notes),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("inventory_detail", code=code))


@app.route("/inventory/<code>/moisture/<int:reading_id>/delete", methods=["POST"])
def inventory_delete_moisture(code, reading_id):
    conn = get_inv_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM moisture_readings WHERE id=? AND bean_code=?", (reading_id, code))
    conn.commit()
    conn.close()
    return redirect(url_for("inventory_detail", code=code))


@app.route("/inventory/<code>")
def inventory_detail(code):
    conn = get_inv_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM beans WHERE code=?", (code,))
    bean = cur.fetchone()
    if not bean:
        conn.close()
        abort(404)

    cur.execute(
        "SELECT * FROM stock_movements WHERE bean_code=? ORDER BY movement_date DESC, id DESC LIMIT 500",
        (code,),
    )
    movements = cur.fetchall()

    cur.execute(
        "SELECT * FROM moisture_readings WHERE bean_code=? ORDER BY measured_at DESC",
        (code,),
    )
    moisture_readings = cur.fetchall()

    cur.execute("SELECT tag FROM bean_tags WHERE bean_code=? ORDER BY tag", (code,))
    tags = [row["tag"] for row in cur.fetchall()]
    conn.close()

    return render_template(
        "inventory_detail.html", bean=bean, movements=movements,
        moisture_readings=moisture_readings, tags=tags,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)
