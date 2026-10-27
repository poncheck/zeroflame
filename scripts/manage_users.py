#!/usr/bin/env python3
"""
CLI user management for Zeroflame's auth.db — create users with a role,
change passwords, list/set roles. Safe to run repeatedly; no web server
needed. Mirrors what the in-app admin panel (Settings -> Users) can do,
for server-side / scripted access.

Roles: admin (full access incl. user management) / roaster (day-to-day
use: edit roasts, inventory) / viewer (read-only).

Usage:
  python3 scripts/manage_users.py list
  python3 scripts/manage_users.py create-admin <username> <password>
  python3 scripts/manage_users.py create-user <username> <password> [role]   # role defaults to 'roaster'
  python3 scripts/manage_users.py set-password <username> <new_password>
  python3 scripts/manage_users.py set-role <username> <admin|roaster|viewer>
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import AUTH_DB_PATH  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

VALID_ROLES = ("admin", "roaster", "viewer")


def _connect():
    return sqlite3.connect(AUTH_DB_PATH)


def list_users():
    conn = _connect()
    rows = conn.execute(
        "SELECT id, username, role, created_at, last_login FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        print("No users found.")
        return
    for uid, username, role, created_at, last_login in rows:
        print(f"[{uid}] {username:<20} role={role:<8} created={created_at} last_login={last_login}")


def create_user(username, password, role):
    if role not in VALID_ROLES:
        print(f"Error: invalid role '{role}'. Must be one of: {', '.join(VALID_ROLES)}")
        sys.exit(1)
    conn = _connect()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        print(f"Error: user '{username}' already exists. Use set-password/set-role to change it.")
        conn.close()
        sys.exit(1)
    conn.execute(
        "INSERT INTO users (username, password_hash, display_name, is_admin, role) VALUES (?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), username, 1 if role == "admin" else 0, role),
    )
    conn.commit()
    conn.close()
    print(f"Created {role} '{username}'.")


def set_password(username, new_password):
    conn = _connect()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not existing:
        print(f"Error: user '{username}' not found. Use create-admin/create-user first.")
        conn.close()
        sys.exit(1)
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?",
        (generate_password_hash(new_password), username),
    )
    conn.commit()
    conn.close()
    print(f"Password updated for '{username}'.")


def set_role(username, role):
    if role not in VALID_ROLES:
        print(f"Error: invalid role '{role}'. Must be one of: {', '.join(VALID_ROLES)}")
        sys.exit(1)
    conn = _connect()
    existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if not existing:
        print(f"Error: user '{username}' not found.")
        conn.close()
        sys.exit(1)
    conn.execute(
        "UPDATE users SET role = ?, is_admin = ? WHERE username = ?",
        (role, 1 if role == "admin" else 0, username),
    )
    conn.commit()
    conn.close()
    print(f"Role for '{username}' set to '{role}'.")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]
    if cmd == "list":
        list_users()
    elif cmd == "create-admin" and len(args) == 3:
        create_user(args[1], args[2], role="admin")
    elif cmd == "create-user" and len(args) in (3, 4):
        role = args[3] if len(args) == 4 else "roaster"
        create_user(args[1], args[2], role=role)
    elif cmd == "set-password" and len(args) == 3:
        set_password(args[1], args[2])
    elif cmd == "set-role" and len(args) == 3:
        set_role(args[1], args[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
