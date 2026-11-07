import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import current_app, has_request_context, request

from .db import get_db

SAFE_MESSAGES = {
    "DB_UNAVAILABLE": ("database", "Storage is temporarily unavailable.", 503),
    "APP_ERROR": ("application", "The application could not process the request.", 500),
    "DEMO_ERROR": ("demo", "Sample incident for the dashboard.", 500),
}


def append_fallback(event):
    path = Path(current_app.config["ERROR_LOG"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 1_000_000:
        for number in (2, 1):
            older = path.with_name(f"{path.name}.{number}")
            newer = path.with_name(f"{path.name}.{number + 1}")
            if older.exists():
                os.replace(older, newer)
        os.replace(path, path.with_name(f"{path.name}.1"))
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def insert_error_event(connection, event):
    code = event.get("code")
    if code not in SAFE_MESSAGES:
        return False
    component, safe_message, http_status = SAFE_MESSAGES[code]
    route_template = event.get("route_template")
    known_routes = {rule.rule for rule in current_app.url_map.iter_rules()}
    if route_template not in known_routes:
        route_template = None
    cursor = connection.execute(
        "INSERT OR IGNORE INTO error_events "
        "(id, occurred_at, severity, code, component, safe_message, route_template, http_status, request_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event["id"], event["occurred_at"], event["severity"], code,
            component, safe_message, route_template, http_status,
            event["request_id"],
        ),
    )
    connection.execute("INSERT OR IGNORE INTO error_reviews (error_id) VALUES (?)", (event["id"],))
    return cursor.rowcount == 1


def record_error(code, component, safe_message, http_status):
    if code not in SAFE_MESSAGES:
        raise ValueError("Unknown error code")
    expected_component, expected_message, expected_status = SAFE_MESSAGES[code]
    if (component, safe_message, http_status) != (expected_component, expected_message, expected_status):
        raise ValueError("Error fields must match the safe catalog")
    event = {
        "id": uuid4().hex,
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "severity": "error",
        "code": code,
        "route_template": request.url_rule.rule if has_request_context() and request.url_rule else None,
        "request_id": uuid4().hex,
    }
    try:
        append_fallback(event)
    except OSError:
        print("Safe error file could not be written.", file=sys.stderr)
    try:
        connection = get_db()
        connection.execute("BEGIN IMMEDIATE")
        insert_error_event(connection, event)
        connection.execute("COMMIT")
    except sqlite3.Error:
        if "connection" in locals() and connection.in_transaction:
            connection.execute("ROLLBACK")
    return event["id"]


def reconcile_fallback():
    path = Path(current_app.config["ERROR_LOG"])
    paths = [path.with_name(f"{path.name}.{number}") for number in (3, 2, 1)] + [path]
    if not any(candidate.exists() for candidate in paths):
        return 0
    count = 0
    connection = get_db()
    for candidate in paths:
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("severity") not in ("error", "critical"):
                    continue
                connection.execute("BEGIN IMMEDIATE")
                if insert_error_event(connection, event):
                    count += 1
                connection.execute("COMMIT")
            except (ValueError, KeyError, TypeError):
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                continue
            except sqlite3.Error:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
    return count
