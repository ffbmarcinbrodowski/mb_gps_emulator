"""
SQLite helpers + a tiny migration runner.

Migrations are plain .sql files in /migrations named NNN_description.sql.
Applied versions are tracked in the `schema_migrations` table, so adding a
new file (e.g. 002_add_column.sql) is all it takes to change the schema.
"""
import os
import sqlite3

from flask import g

import config


def connect():
    """Open a new connection. Used by Flask requests and emulator threads."""
    conn = sqlite3.connect(config.DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Connection bound to the current Flask request."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def run_migrations():
    """Apply every migration file that has not been applied yet."""
    conn = connect()
    # WAL lets emulator threads write while the web app reads.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY,"
        " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}

    for filename in sorted(os.listdir(config.MIGRATIONS_DIR)):
        if not filename.endswith(".sql"):
            continue
        version = filename.split("_", 1)[0]
        if version in applied:
            continue
        with open(os.path.join(config.MIGRATIONS_DIR, filename), encoding="utf-8") as f:
            sql = f.read()
        print(f"[db] applying migration {filename}")
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        conn.commit()
    conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)
    run_migrations()
