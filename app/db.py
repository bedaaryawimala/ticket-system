import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db():
    if "db" not in g:
        connection = sqlite3.connect(
            current_app.config["DATABASE"], timeout=5, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        g.db = connection
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    connection = get_db()
    connection.executescript(schema)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
    if "session_version" not in columns:
        connection.execute(
            "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL "
            "DEFAULT 0 CHECK (session_version >= 0)"
        )
    ticket_columns = {row[1] for row in connection.execute("PRAGMA table_info(tickets)")}
    event_columns = {row[1] for row in connection.execute("PRAGMA table_info(ticket_events)")}
    if "urgency" not in ticket_columns or "from_urgency" not in event_columns:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if "urgency" not in ticket_columns:
                connection.execute(
                    "ALTER TABLE tickets ADD COLUMN urgency TEXT NOT NULL DEFAULT 'normal' "
                    "CHECK (urgency IN ('low', 'normal', 'high'))"
                )
                connection.execute(
                    "ALTER TABLE tickets ADD COLUMN work_blocked INTEGER CHECK (work_blocked IN (0, 1))"
                )
                connection.execute(
                    "ALTER TABLE tickets ADD COLUMN workaround_available INTEGER "
                    "CHECK (workaround_available IN (0, 1))"
                )
                connection.execute(
                    "ALTER TABLE tickets ADD COLUMN waiting_impact TEXT "
                    "CHECK (waiting_impact IN ('minor', 'disruptive', 'serious'))"
                )
            if "from_urgency" not in event_columns:
                connection.execute(
                    "CREATE TABLE ticket_events_v2 ("
                    "id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL REFERENCES tickets(id), "
                    "actor_id INTEGER REFERENCES users(id), "
                    "event_type TEXT NOT NULL CHECK (event_type IN "
                    "('created', 'claimed', 'assigned', 'reassigned', 'unassigned', 'started', "
                    "'resolved', 'closed', 'reopened', 'commented', 'urgency_changed')), "
                    "from_status TEXT, to_status TEXT, "
                    "from_urgency TEXT CHECK (from_urgency IN ('low', 'normal', 'high')), "
                    "to_urgency TEXT CHECK (to_urgency IN ('low', 'normal', 'high')), "
                    "from_assignee_id INTEGER REFERENCES users(id), "
                    "to_assignee_id INTEGER REFERENCES users(id), "
                    "comment_id INTEGER REFERENCES comments(id), "
                    "reason TEXT CHECK (reason IS NULL OR length(trim(reason)) BETWEEN 1 AND 500), "
                    "occurred_at TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO ticket_events_v2 "
                    "(id, ticket_id, actor_id, event_type, from_status, to_status, "
                    "from_assignee_id, to_assignee_id, comment_id, reason, occurred_at) "
                    "SELECT id, ticket_id, actor_id, event_type, from_status, to_status, "
                    "from_assignee_id, to_assignee_id, comment_id, reason, occurred_at "
                    "FROM ticket_events"
                )
                connection.execute("DROP TABLE ticket_events")
                connection.execute("ALTER TABLE ticket_events_v2 RENAME TO ticket_events")
                connection.execute(
                    "CREATE INDEX idx_ticket_events_ticket ON ticket_events(ticket_id, occurred_at)"
                )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tickets_urgency_queue "
        "ON tickets(status, assigned_agent_id, urgency, created_at)"
    )


def init_app(app):
    app.teardown_appcontext(close_db)
