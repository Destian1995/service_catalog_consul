"""Persistent portal users and role assignments, scoped by OIDC issuer + subject."""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


@contextmanager
def _database():
    path = Path(os.environ.get("CATALOG_USERS_DB") or Path(__file__).parent / "data" / "catalog-users.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    issuer TEXT NOT NULL, subject TEXT NOT NULL,
                    login TEXT NOT NULL, name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user', 'admin')),
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    PRIMARY KEY (issuer, subject)
                );
                CREATE TABLE IF NOT EXISTS role_events (
                    id INTEGER PRIMARY KEY, issuer TEXT NOT NULL,
                    actor_subject TEXT NOT NULL, actor_login TEXT NOT NULL,
                    target_subject TEXT NOT NULL, old_role TEXT NOT NULL,
                    new_role TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
            yield connection
    finally:
        connection.close()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_login(issuer, subject, login, name):
    if not issuer or not subject:
        raise ValueError("Verified issuer and subject are required")
    now = _now()
    with _database() as db:
        db.execute("""INSERT INTO users (issuer, subject, login, name, first_seen, last_seen)
                      VALUES (?, ?, ?, ?, ?, ?)
                      ON CONFLICT(issuer, subject) DO UPDATE SET
                          login=excluded.login, name=excluded.name, last_seen=excluded.last_seen""",
                   (issuer, subject, login, name, now, now))


def get_user(issuer, subject):
    with _database() as db:
        row = db.execute("SELECT * FROM users WHERE issuer=? AND subject=?", (issuer, subject)).fetchone()
        return dict(row) if row else None


def list_users(issuer):
    with _database() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM users WHERE issuer=? ORDER BY login COLLATE NOCASE, subject", (issuer,))]


def set_role(issuer, subject, role, actor):
    """Caller authorizes the owner; role change and audit are one transaction."""
    if role not in ("user", "admin"):
        raise ValueError("Unknown role")
    with _database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT role FROM users WHERE issuer=? AND subject=?", (issuer, subject)).fetchone()
        if row is None:
            return False
        if row["role"] != role:
            db.execute("UPDATE users SET role=? WHERE issuer=? AND subject=?", (role, issuer, subject))
            db.execute("""INSERT INTO role_events
                          (issuer, actor_subject, actor_login, target_subject, old_role, new_role, created_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?)""",
                       (issuer, actor["sub"], actor["login"], subject, row["role"], role, _now()))
        return True
