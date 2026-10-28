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
from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import DB_PATH, INV_DB_PATH, AUTH_DB_PATH, FLASK_SECRET_KEY  # noqa: E402

WARSAW_TZ = ZoneInfo("Europe/Warsaw")

VALID_ROLES = ("admin", "roaster", "viewer")

# Fixed vocabulary lifted from the cupping/brewing sensory sheet (arkusz
# brewing intermediate.pdf) — same group list reused for both aroma and
# flavour, per the sheet.
CUPPING_FLAVOUR_GROUPS = [
    "kwiaty", "owoce", "warzywa", "chemiczne", "palone",
    "przyprawy", "czekolada", "orzechy", "słodkie",
]
CUPPING_AROMA_INTENSITY = ["wysoka", "średnia", "niska"]
CUPPING_MOUTHFEEL = ["gładki", "cierpki", "pylisty", "wysuszający"]
CUPPING_TEXTURE = ["gładka", "jedwabista", "musująca", "szorstka", "pylista", "cierpka", "ostra"]
CUPPING_AFTERTASTE_LENGTH = ["długi", "średni", "krótki"]
CUPPING_AFTERTASTE_TYPE = ["słodki", "gorzki", "kwaśny"]
CUPPING_BODY = ["wysokie", "średnie", "niskie", "krągłe", "traci krągłość"]

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


def role_required(*allowed_roles):
    """Restrict a view to the given roles (server-side enforcement — the
    UI may also hide controls, but this is the real gate). Viewers get a
    403 on write endpoints; admin-only views pass a single role."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login", next=request.path))
            if session.get("role") not in allowed_roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


@app.before_request
def require_login_everywhere():
    allowed = {"login", "static"}
    if request.endpoint in allowed or request.endpoint is None:
        return None
    if not session.get("user_id"):
        return redirect(url_for("login", next=request.path))
    # Self-healing: refresh role/display_name from the DB on every request.
    # Covers (a) a session cookie issued before the 'role' column/session
    # key existed, (b) an admin changing someone's role while they're
    # still logged in -- without this, role_required() would keep reading
    # a stale/missing value from the signed cookie until the user logs
    # out and back in, which looks like an inexplicable 403.
    conn = get_auth_conn()
    row = conn.execute(
        "SELECT username, display_name, role FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    conn.close()
    if not row:
        # Account was deleted while the session was still active.
        session.clear()
        return redirect(url_for("login", next=request.path))
    session["username"] = row["username"]
    session["display_name"] = row["display_name"] or row["username"]
    session["role"] = row["role"]
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
            session["role"] = user["role"]
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


@app.route("/account/password", methods=["GET", "POST"])
@login_required
def account_password():
    error = None
    success = None
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        conn = get_auth_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE id=?", (session["user_id"],))
        user = cur.fetchone()
        if not user or not check_password_hash(user["password_hash"], current_password):
            error = "Aktualne hasło jest nieprawidłowe."
        elif len(new_password) < 8:
            error = "Nowe hasło musi mieć co najmniej 8 znaków."
        elif new_password != confirm_password:
            error = "Nowe hasło i powtórzenie nie są identyczne."
        else:
            cur.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (generate_password_hash(new_password), session["user_id"]),
            )
            conn.commit()
            success = "Hasło zostało zmienione."
        conn.close()
    return render_template("account_password.html", error=error, success=success)


@app.route("/admin/users")
@role_required("admin")
def admin_users():
    conn = get_auth_conn()
    users = conn.execute(
        "SELECT id, username, display_name, role, created_at, last_login FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return render_template("admin_users.html", users=users, valid_roles=VALID_ROLES)


@app.route("/admin/users/create", methods=["POST"])
@role_required("admin")
def admin_users_create():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    role = request.form.get("role", "roaster").strip()
    error = None
    if not username or not password:
        error = "Login i hasło są wymagane."
    elif role not in VALID_ROLES:
        error = "Nieprawidłowa rola."
    elif len(password) < 8:
        error = "Hasło musi mieć co najmniej 8 znaków."
    if not error:
        conn = get_auth_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE username=?", (username,))
        if cur.fetchone():
            error = f"Użytkownik '{username}' już istnieje."
        else:
            cur.execute(
                "INSERT INTO users (username, password_hash, display_name, is_admin, role) VALUES (?, ?, ?, ?, ?)",
                (username, generate_password_hash(password), username, 1 if role == "admin" else 0, role),
            )
            conn.commit()
        conn.close()
    if error:
        conn = get_auth_conn()
        users = conn.execute(
            "SELECT id, username, display_name, role, created_at, last_login FROM users ORDER BY id"
        ).fetchall()
        conn.close()
        return render_template("admin_users.html", users=users, valid_roles=VALID_ROLES, error=error)
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
@role_required("admin")
def admin_users_set_role(user_id):
    role = request.form.get("role", "").strip()
    if role not in VALID_ROLES:
        abort(400)
    conn = get_auth_conn()
    cur = conn.cursor()
    cur.execute("SELECT username FROM users WHERE id=?", (user_id,))
    target = cur.fetchone()
    if not target:
        conn.close()
        abort(404)
    if target["username"] == session.get("username") and role != "admin":
        # Prevent an admin from locking themselves out by demoting their own account.
        conn.close()
        return redirect(url_for("admin_users"))
    cur.execute(
        "UPDATE users SET role=?, is_admin=? WHERE id=?",
        (role, 1 if role == "admin" else 0, user_id),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@role_required("admin")
def admin_users_reset_password(user_id):
    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        return redirect(url_for("admin_users"))
    conn = get_auth_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if not cur.fetchone():
        conn.close()
        abort(404)
    cur.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(new_password), user_id))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@role_required("admin")
def admin_users_delete(user_id):
    conn = get_auth_conn()
    cur = conn.cursor()
    cur.execute("SELECT username FROM users WHERE id=?", (user_id,))
    target = cur.fetchone()
    if not target:
        conn.close()
        abort(404)
    if target["username"] == session.get("username"):
        # Prevent self-deletion (would orphan the session / possibly leave no admins).
        conn.close()
        return redirect(url_for("admin_users"))
    cur.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_users"))


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
@role_required("admin", "roaster")
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
        if session.get("role") not in ("admin", "roaster"):
            conn.close()
            abort(403)
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
    inv_conn = get_inv_conn()
    if roast["bean_name"]:
        bean_in_inventory = inv_conn.execute(
            "SELECT 1 FROM beans WHERE code=?", (roast["bean_name"],)
        ).fetchone() is not None
    roast_cuppings = inv_conn.execute(
        """SELECT i.*, s.cupped_at, s.session_label, s.cupper FROM cupping_items i
           JOIN cupping_sessions s ON s.id = i.session_id
           WHERE i.roast_prfl_m_cd=? ORDER BY s.cupped_at DESC""", (prfl_m_cd,)
    ).fetchall()
    inv_conn.close()

    conn.close()
    return render_template(
        "detail.html", r=roast, whole_row=whole_row, ground_row=ground_row,
        weight_row=weight_row, loss_pct=loss_pct,
        rst_mod_labels=RST_MOD_LABELS, rst_lvl_labels=RST_LVL_LABELS,
        original_profile_cd=original_profile_cd, bean_in_inventory=bean_in_inventory,
        roast_cuppings=roast_cuppings,
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
@role_required("admin", "roaster")
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
@role_required("admin", "roaster")
def inventory_delete_tag(code, tag):
    conn = get_inv_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM bean_tags WHERE bean_code=? AND tag=?", (code, tag))
    conn.commit()
    conn.close()
    return redirect(url_for("inventory_detail", code=code))


@app.route("/inventory/<code>/set-status", methods=["POST"])
@role_required("admin", "roaster")
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
@role_required("admin", "roaster")
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
@role_required("admin", "roaster")
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
@role_required("admin", "roaster")
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

    cur.execute("""
        SELECT i.*, s.cupped_at, s.session_label FROM cupping_items i
        JOIN cupping_sessions s ON s.id = i.session_id
        WHERE i.bean_code=? ORDER BY s.cupped_at DESC
    """, (code,))
    bean_cuppings = cur.fetchall()

    conn.close()

    return render_template(
        "inventory_detail.html", bean=bean, movements=movements,
        moisture_readings=moisture_readings, tags=tags, bean_cuppings=bean_cuppings,
    )


def _cupping_checkbox_list(field_prefix, idx, field_name):
    """Join multiple selected checkbox values for item `idx` in a multi-item form."""
    key = f"{field_prefix}{idx}_{field_name}"
    values = [v.strip() for v in request.form.getlist(key) if v.strip()]
    return ", ".join(values) if values else None


def _save_cupping_session_from_form(conn, session_id=None):
    """Insert or update a cupping_sessions row + its cupping_items from request.form.

    Items are submitted as item0_*, item1_*, ... (count given by 'item_count').
    Returns the session id.
    """
    session_label = request.form.get("session_label", "").strip() or None
    overall_notes = request.form.get("session_notes", "").strip() or None
    cupper = session.get("username")

    if session_id is None:
        cur = conn.execute(
            "INSERT INTO cupping_sessions (session_label, cupper, notes) VALUES (?, ?, ?)",
            (session_label, cupper, overall_notes),
        )
        conn.commit()
        session_id = cur.lastrowid
    else:
        conn.execute(
            "UPDATE cupping_sessions SET session_label=?, notes=? WHERE id=?",
            (session_label, overall_notes, session_id),
        )
        conn.execute("DELETE FROM cupping_items WHERE session_id=?", (session_id,))
        conn.commit()

    def _to_float(name):
        raw = request.form.get(name, "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    item_count = int(request.form.get("item_count", "0") or "0")
    saved_items = 0
    for idx in range(item_count):
        prefix = f"item{idx}_"
        bean_code = request.form.get(f"{prefix}bean_code", "").strip()
        if not bean_code:
            continue  # a blank/removed item slot — skip it
        roast_prfl_m_cd = request.form.get(f"{prefix}roast_prfl_m_cd", "").strip() or None

        aroma_intensity = request.form.get(f"{prefix}aroma_intensity", "").strip() or None
        aroma_groups = _cupping_checkbox_list("item", idx, "aroma_groups")

        sour_intensity = _to_float(f"{prefix}sour_intensity")
        bitter_intensity = _to_float(f"{prefix}bitter_intensity")
        sweet_intensity = _to_float(f"{prefix}sweet_intensity")
        salty_intensity = _to_float(f"{prefix}salty_intensity")
        flavour_groups = _cupping_checkbox_list("item", idx, "flavour_groups")

        aftertaste_length = request.form.get(f"{prefix}aftertaste_length", "").strip() or None
        aftertaste_type = request.form.get(f"{prefix}aftertaste_type", "").strip() or None
        mouthfeel = _cupping_checkbox_list("item", idx, "mouthfeel")

        body = request.form.get(f"{prefix}body", "").strip() or None
        texture = _cupping_checkbox_list("item", idx, "texture")

        notes = request.form.get(f"{prefix}notes", "").strip() or None

        conn.execute(
            """INSERT INTO cupping_items (
                session_id, bean_code, roast_prfl_m_cd,
                aroma_intensity, aroma_groups,
                sour_intensity, bitter_intensity, sweet_intensity, salty_intensity, flavour_groups,
                aftertaste_length, aftertaste_type, mouthfeel,
                body, texture, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, bean_code, roast_prfl_m_cd,
             aroma_intensity, aroma_groups,
             sour_intensity, bitter_intensity, sweet_intensity, salty_intensity, flavour_groups,
             aftertaste_length, aftertaste_type, mouthfeel,
             body, texture, notes),
        )
        saved_items += 1
    conn.commit()
    return session_id, saved_items


def _cupping_form_vocab():
    return dict(
        aroma_intensity_opts=CUPPING_AROMA_INTENSITY,
        flavour_groups_opts=CUPPING_FLAVOUR_GROUPS,
        aftertaste_length_opts=CUPPING_AFTERTASTE_LENGTH,
        aftertaste_type_opts=CUPPING_AFTERTASTE_TYPE,
        mouthfeel_opts=CUPPING_MOUTHFEEL,
        body_opts=CUPPING_BODY,
        texture_opts=CUPPING_TEXTURE,
    )


@app.route("/api/roasts-for-bean/<code>")
@login_required
def api_roasts_for_bean(code):
    conn = get_conn()
    rows = conn.execute(
        "SELECT prfl_m_cd, prfl_name, rst_dt_iso FROM roasts WHERE bean_name=? "
        "ORDER BY rst_dt DESC LIMIT 50",
        (code,),
    ).fetchall()
    conn.close()
    return jsonify([
        {"prfl_m_cd": r["prfl_m_cd"], "prfl_name": r["prfl_name"], "rst_dt_iso": r["rst_dt_iso"]}
        for r in rows
    ])


@app.route("/cupping")
def cupping_list():
    conn = get_inv_conn()
    cur = conn.cursor()

    bean_filter = request.args.get("bean", "").strip()
    if bean_filter:
        cur.execute("""
            SELECT DISTINCT s.* FROM cupping_sessions s
            JOIN cupping_items i ON i.session_id = s.id
            WHERE i.bean_code = ?
            ORDER BY s.cupped_at DESC
            LIMIT 500
        """, (bean_filter,))
    else:
        cur.execute("SELECT * FROM cupping_sessions ORDER BY cupped_at DESC LIMIT 500")
    sessions = cur.fetchall()

    # Pull all items for these sessions in one go, grouped by session_id.
    session_ids = [s["id"] for s in sessions]
    items_by_session = {}
    if session_ids:
        placeholders = ",".join("?" * len(session_ids))
        cur.execute(
            f"SELECT * FROM cupping_items WHERE session_id IN ({placeholders}) ORDER BY id",
            session_ids,
        )
        for item in cur.fetchall():
            items_by_session.setdefault(item["session_id"], []).append(item)

    cur.execute("SELECT DISTINCT bean_code FROM cupping_items ORDER BY bean_code")
    bean_codes = [row["bean_code"] for row in cur.fetchall()]
    conn.close()

    return render_template(
        "cupping_list.html", sessions=sessions, items_by_session=items_by_session,
        bean_codes=bean_codes, bean_filter=bean_filter,
    )


@app.route("/cupping/from-roasts", methods=["POST"])
@role_required("admin", "roaster")
def cupping_from_roasts():
    """Build a fresh multi-item cupping session form pre-filled from checked roasts
    on the /roasts list page (one item per checked roast, bean auto-resolved)."""
    prfl_cds = [v.strip() for v in request.form.getlist("prfl_m_cd") if v.strip()]
    if not prfl_cds:
        return redirect(url_for("roasts_list"))

    conn = get_conn()
    placeholders = ",".join("?" * len(prfl_cds))
    rows = conn.execute(
        f"SELECT prfl_m_cd, bean_name FROM roasts WHERE prfl_m_cd IN ({placeholders})",
        prfl_cds,
    ).fetchall()
    conn.close()

    bean_by_cd = {r["prfl_m_cd"]: (r["bean_name"] or "") for r in rows}
    items = [
        {"bean_code": bean_by_cd.get(cd, ""), "roast_prfl_m_cd": cd}
        for cd in prfl_cds
        if cd in bean_by_cd  # ignore stale/unknown ids silently
    ]
    if not items:
        return redirect(url_for("roasts_list"))

    session["cupping_pending_items"] = items
    return redirect(url_for("cupping_new"))


@app.route("/cupping/new", methods=["GET", "POST"])
@role_required("admin", "roaster")
def cupping_new():
    inv_conn = get_inv_conn()
    beans = inv_conn.execute("SELECT code FROM beans ORDER BY code").fetchall()

    preselected_bean = request.args.get("bean_code", "").strip()
    preselected_roast = request.args.get("roast_prfl_m_cd", "").strip()

    if request.method == "POST":
        new_id, saved_items = _save_cupping_session_from_form(inv_conn)
        inv_conn.close()
        if saved_items == 0:
            # Nothing usable was submitted — undo the empty session and bounce back.
            conn2 = get_inv_conn()
            conn2.execute("DELETE FROM cupping_sessions WHERE id=?", (new_id,))
            conn2.commit()
            conn2.close()
            inv_conn2 = get_inv_conn()
            beans2 = inv_conn2.execute("SELECT code FROM beans ORDER BY code").fetchall()
            inv_conn2.close()
            return render_template(
                "cupping_form.html", beans=beans2,
                error="Dodaj przynajmniej jedną kawę (z kodem ziarna) do sesji.",
                preselected_bean=preselected_bean, preselected_roast=preselected_roast,
                cupping_session=None, items=[], **_cupping_form_vocab(),
            )
        return redirect(url_for("cupping_session_detail", session_id=new_id))

    pending_items = session.pop("cupping_pending_items", None)
    inv_conn.close()
    if pending_items:
        return render_template(
            "cupping_form.html", beans=beans, error=None,
            preselected_bean="", preselected_roast="",
            cupping_session=None, items=pending_items, **_cupping_form_vocab(),
        )
    return render_template(
        "cupping_form.html", beans=beans, error=None,
        preselected_bean=preselected_bean, preselected_roast=preselected_roast,
        cupping_session=None, items=[], **_cupping_form_vocab(),
    )


@app.route("/cupping/<int:session_id>")
def cupping_session_detail(session_id):
    conn = get_inv_conn()
    cupping_session = conn.execute("SELECT * FROM cupping_sessions WHERE id=?", (session_id,)).fetchone()
    if not cupping_session:
        conn.close()
        abort(404)
    items = conn.execute(
        "SELECT * FROM cupping_items WHERE session_id=? ORDER BY id", (session_id,)
    ).fetchall()
    conn.close()

    roasts_by_cd = {}
    roast_cds = [i["roast_prfl_m_cd"] for i in items if i["roast_prfl_m_cd"]]
    if roast_cds:
        rconn = get_conn()
        placeholders = ",".join("?" * len(roast_cds))
        for r in rconn.execute(
            f"SELECT prfl_m_cd, prfl_name, rst_dt_iso FROM roasts WHERE prfl_m_cd IN ({placeholders})",
            roast_cds,
        ).fetchall():
            roasts_by_cd[r["prfl_m_cd"]] = dict(r)
        rconn.close()

    return render_template(
        "cupping_detail.html", cupping_session=cupping_session, items=items, roasts_by_cd=roasts_by_cd,
    )


@app.route("/cupping/<int:session_id>/edit", methods=["GET", "POST"])
@role_required("admin", "roaster")
def cupping_session_edit(session_id):
    inv_conn = get_inv_conn()
    cupping_session = inv_conn.execute("SELECT * FROM cupping_sessions WHERE id=?", (session_id,)).fetchone()
    if not cupping_session:
        inv_conn.close()
        abort(404)
    beans = inv_conn.execute("SELECT code FROM beans ORDER BY code").fetchall()

    if request.method == "POST":
        _, saved_items = _save_cupping_session_from_form(inv_conn, session_id=session_id)
        inv_conn.close()
        if saved_items == 0:
            return redirect(url_for("cupping_session_edit", session_id=session_id))
        return redirect(url_for("cupping_session_detail", session_id=session_id))

    items = inv_conn.execute(
        "SELECT * FROM cupping_items WHERE session_id=? ORDER BY id", (session_id,)
    ).fetchall()
    inv_conn.close()
    return render_template(
        "cupping_form.html", beans=beans, error=None,
        preselected_bean="", preselected_roast="",
        cupping_session=cupping_session, items=[dict(i) for i in items], **_cupping_form_vocab(),
    )


@app.route("/cupping/<int:session_id>/delete", methods=["POST"])
@role_required("admin", "roaster")
def cupping_session_delete(session_id):
    conn = get_inv_conn()
    conn.execute("DELETE FROM cupping_items WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM cupping_sessions WHERE id=?", (session_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("cupping_list"))


@app.route("/cupping/<int:session_id>/items/<int:item_id>/delete", methods=["POST"])
@role_required("admin", "roaster")
def cupping_item_delete(session_id, item_id):
    conn = get_inv_conn()
    conn.execute("DELETE FROM cupping_items WHERE id=? AND session_id=?", (item_id, session_id))
    conn.commit()
    remaining = conn.execute(
        "SELECT COUNT(*) c FROM cupping_items WHERE session_id=?", (session_id,)
    ).fetchone()["c"]
    if remaining == 0:
        # Don't leave an empty session dangling.
        conn.execute("DELETE FROM cupping_sessions WHERE id=?", (session_id,))
        conn.commit()
        conn.close()
        return redirect(url_for("cupping_list"))
    conn.close()
    return redirect(url_for("cupping_session_detail", session_id=session_id))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8088)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)
