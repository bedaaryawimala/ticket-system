import sqlite3
from datetime import date

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .auth import create_user, new_user_error, role_required
from .db import get_db
from .ticket_ops import TicketActionError, assign_ticket, change_urgency, parse_version, required_text, utc_now, write_transaction

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/accounts")
@role_required("admin")
def account_list():
    accounts = get_db().execute(
        "SELECT id, display_name, email, role, active, created_at FROM users "
        "WHERE role IN ('agent', 'admin') ORDER BY created_at DESC, id DESC LIMIT 100"
    ).fetchall()
    return render_template("admin_accounts.html", accounts=accounts)


@bp.post("/accounts")
@role_required("admin")
def create_account():
    name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role", "")
    password = request.form.get("password", "")
    confirmation = request.form.get("password_confirm", "")
    if role not in ("agent", "admin"):
        raise TicketActionError(400, "Pilih peran petugas atau admin.")
    error = new_user_error(name, email, password, confirmation)
    if error is not None:
        raise TicketActionError(400, error)
    try:
        with write_transaction() as connection:
            create_user(connection, name, email, password, role)
    except sqlite3.IntegrityError:
        raise TicketActionError(400, "Alamat email sudah digunakan.") from None
    flash("Akun tim dibuat. Sampaikan kata sandi awal kepada pemilik akun secara langsung.")
    return redirect(url_for("admin.account_list"), code=303)


def search_text():
    query = request.args.get("q", "").strip()
    if len(query) > 100:
        raise TicketActionError(400, "Pencarian terlalu panjang.")
    return query


def filter_date(name):
    raw_date = request.args.get(name, "")
    if not raw_date:
        return ""
    try:
        parsed = date.fromisoformat(raw_date)
    except ValueError:
        raise TicketActionError(400, "Tanggal filter tidak valid.") from None
    if parsed.isoformat() != raw_date:
        raise TicketActionError(400, "Tanggal filter tidak valid.")
    return raw_date


@bp.get("/tickets")
@role_required("admin")
def ticket_list():
    query = search_text()
    status = request.args.get("status", "")
    urgency = request.args.get("urgency", "")
    if status not in ("", "open", "in_progress", "resolved", "closed"):
        raise TicketActionError(400, "Status filter tidak valid.")
    if urgency not in ("", "low", "normal", "high"):
        raise TicketActionError(400, "Urgensi filter tidak valid.")
    conditions = []
    parameters = []
    if query:
        conditions.append("(t.title LIKE ? OR CAST(t.id AS TEXT) = ?)")
        parameters.extend((f"%{query}%", query))
    if status in ("open", "in_progress", "resolved", "closed"):
        conditions.append("t.status = ?")
        parameters.append(status)
    if urgency:
        conditions.append("t.urgency = ?")
        parameters.append(urgency)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = get_db().execute(
        "SELECT t.*, r.display_name AS requester_name, a.display_name AS agent_name "
        "FROM tickets t JOIN users r ON r.id = t.requester_id "
        "LEFT JOIN users a ON a.id = t.assigned_agent_id"
        + where + " ORDER BY t.updated_at DESC, t.id DESC LIMIT 100",
        parameters,
    ).fetchall()
    return render_template("admin_tickets.html", tickets=rows, query=query, status=status, urgency=urgency)


@bp.post("/tickets/<int:ticket_id>/assign")
@role_required("admin")
def assign(ticket_id):
    raw_agent = request.form.get("agent_id", "")
    try:
        agent_id = int(raw_agent) if raw_agent else None
    except ValueError:
        raise TicketActionError(400, "Pilih petugas yang valid.") from None
    assign_ticket(
        g.user,
        ticket_id,
        parse_version(request.form.get("version")),
        agent_id,
        request.form.get("reason", ""),
    )
    return redirect(url_for("tickets.ticket_detail", ticket_id=ticket_id), code=303)


@bp.post("/tickets/<int:ticket_id>/urgency")
@role_required("admin")
def update_urgency(ticket_id):
    change_urgency(
        g.user, ticket_id, parse_version(request.form.get("version")),
        request.form.get("urgency"), request.form.get("reason"),
    )
    return redirect(url_for("tickets.ticket_detail", ticket_id=ticket_id), code=303)


@bp.get("/tickets/<int:ticket_id>/history")
@role_required("admin")
def history(ticket_id):
    ticket = get_db().execute("SELECT id, title FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        abort(404)
    events = get_db().execute(
        "SELECT e.*, actor.display_name AS actor_name, previous.display_name AS previous_agent, "
        "next_agent.display_name AS next_agent_name "
        "FROM ticket_events e LEFT JOIN users actor ON actor.id = e.actor_id "
        "LEFT JOIN users previous ON previous.id = e.from_assignee_id "
        "LEFT JOIN users next_agent ON next_agent.id = e.to_assignee_id "
        "WHERE e.ticket_id = ? ORDER BY e.occurred_at, e.id",
        (ticket_id,),
    ).fetchall()
    return render_template("history.html", ticket=ticket, events=events)


@bp.get("/errors")
@role_required("admin")
def error_list():
    query = search_text()
    severity = request.args.get("severity", "")
    status = request.args.get("status", "")
    if severity not in ("", "error", "critical") or status not in ("", "new", "reviewed"):
        raise TicketActionError(400, "Filter error tidak valid.")
    date_from = filter_date("from")
    date_to = filter_date("to")
    if date_from and date_to and date_from > date_to:
        raise TicketActionError(400, "Tanggal awal harus sebelum tanggal akhir.")
    conditions = []
    parameters = []
    if query:
        conditions.append("(e.id LIKE ? OR e.code LIKE ? OR e.safe_message LIKE ?)")
        parameters.extend((f"%{query}%",) * 3)
    if severity in ("error", "critical"):
        conditions.append("e.severity = ?")
        parameters.append(severity)
    if status in ("new", "reviewed"):
        conditions.append("r.status = ?")
        parameters.append(status)
    if date_from:
        conditions.append("substr(e.occurred_at, 1, 10) >= ?")
        parameters.append(date_from)
    if date_to:
        conditions.append("substr(e.occurred_at, 1, 10) <= ?")
        parameters.append(date_to)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    events = get_db().execute(
        "SELECT e.*, COALESCE(r.status, 'new') AS review_status FROM error_events e "
        "LEFT JOIN error_reviews r ON r.error_id = e.id"
        + where + " ORDER BY e.occurred_at DESC, e.id DESC LIMIT 100",
        parameters,
    ).fetchall()
    return render_template(
        "error_list.html", events=events, query=query, severity=severity,
        status=status, date_from=date_from, date_to=date_to,
    )


@bp.get("/errors/<event_id>")
@role_required("admin")
def error_detail(event_id):
    event = get_db().execute(
        "SELECT e.*, COALESCE(r.status, 'new') AS review_status, "
        "r.reviewed_at, u.display_name AS reviewer_name "
        "FROM error_events e LEFT JOIN error_reviews r ON r.error_id = e.id "
        "LEFT JOIN users u ON u.id = r.reviewed_by WHERE e.id = ?",
        (event_id,),
    ).fetchone()
    if event is None:
        abort(404)
    notes = get_db().execute(
        "SELECT n.*, u.display_name AS author_name FROM error_review_notes n "
        "JOIN users u ON u.id = n.admin_id WHERE n.error_id = ? "
        "ORDER BY n.created_at, n.id",
        (event_id,),
    ).fetchall()
    return render_template("error_detail.html", event=event, notes=notes)


@bp.post("/errors/<event_id>/review")
@role_required("admin")
def review_error(event_id):
    with write_transaction() as connection:
        if connection.execute("SELECT id FROM error_events WHERE id = ?", (event_id,)).fetchone() is None:
            raise TicketActionError(404, "Error tidak ditemukan.")
        connection.execute("INSERT OR IGNORE INTO error_reviews (error_id) VALUES (?)", (event_id,))
        connection.execute(
            "UPDATE error_reviews SET status = 'reviewed', reviewed_by = ?, reviewed_at = ? "
            "WHERE error_id = ? AND status = 'new'",
            (g.user["id"], utc_now(), event_id),
        )
    return redirect(url_for("admin.error_detail", event_id=event_id), code=303)


@bp.post("/errors/<event_id>/notes")
@role_required("admin")
def add_error_note(event_id):
    body = required_text(request.form.get("body"), 2000, "Catatan")
    with write_transaction() as connection:
        if connection.execute("SELECT id FROM error_events WHERE id = ?", (event_id,)).fetchone() is None:
            raise TicketActionError(404, "Error tidak ditemukan.")
        connection.execute(
            "INSERT INTO error_review_notes (error_id, admin_id, body, created_at) VALUES (?, ?, ?, ?)",
            (event_id, g.user["id"], body, utc_now()),
        )
    return redirect(url_for("admin.error_detail", event_id=event_id), code=303)
