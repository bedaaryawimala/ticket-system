from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from .auth import login_required, role_required
from .db import get_db
from .ticket_ops import (
    add_comment,
    can_view,
    change_status,
    claim_ticket,
    create_ticket,
    parse_version,
)

bp = Blueprint("tickets", __name__)


@bp.get("/tickets")
@login_required
def ticket_list():
    if g.user["role"] == "admin":
        return redirect(url_for("admin.ticket_list"))
    if g.user["role"] == "requester":
        rows = get_db().execute(
            "SELECT t.*, a.display_name AS agent_name FROM tickets t "
            "LEFT JOIN users a ON a.id = t.assigned_agent_id "
            "WHERE t.requester_id = ? ORDER BY t.updated_at DESC, t.id DESC",
            (g.user["id"],),
        ).fetchall()
        heading = "Tiket saya"
    else:
        rows = get_db().execute(
            "SELECT t.*, r.display_name AS requester_name FROM tickets t "
            "JOIN users r ON r.id = t.requester_id "
            "WHERE t.assigned_agent_id = ? ORDER BY t.updated_at DESC, t.id DESC",
            (g.user["id"],),
        ).fetchall()
        heading = "Tiket yang saya tangani"
    return render_template("ticket_list.html", tickets=rows, heading=heading)


@bp.get("/tickets/new")
@role_required("requester")
def new_ticket():
    return render_template("ticket_new.html")


@bp.post("/tickets")
@role_required("requester")
def submit_ticket():
    ticket_id = create_ticket(
        g.user, request.form.get("title"), request.form.get("description"),
        request.form.get("work_blocked"), request.form.get("workaround_available"),
        request.form.get("waiting_impact"),
    )
    return redirect(url_for("tickets.ticket_detail", ticket_id=ticket_id), code=303)


@bp.get("/tickets/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id):
    ticket = get_db().execute(
        "SELECT t.*, r.display_name AS requester_name, a.display_name AS agent_name "
        "FROM tickets t JOIN users r ON r.id = t.requester_id "
        "LEFT JOIN users a ON a.id = t.assigned_agent_id WHERE t.id = ?",
        (ticket_id,),
    ).fetchone()
    if ticket is None or not can_view(ticket, g.user):
        abort(404)
    comments = get_db().execute(
        "SELECT c.*, u.display_name AS author_name, u.role AS author_role "
        "FROM comments c JOIN users u ON u.id = c.author_id "
        "WHERE c.ticket_id = ? ORDER BY c.created_at, c.id",
        (ticket_id,),
    ).fetchall()
    agents = []
    if g.user["role"] == "admin":
        agents = get_db().execute(
            "SELECT id, display_name FROM users WHERE role = 'agent' AND active = 1 ORDER BY display_name"
        ).fetchall()
    return render_template("ticket_detail.html", ticket=ticket, comments=comments, agents=agents)


@bp.post("/tickets/<int:ticket_id>/comments")
@login_required
def submit_comment(ticket_id):
    add_comment(g.user, ticket_id, request.form.get("body"))
    return redirect(url_for("tickets.ticket_detail", ticket_id=ticket_id), code=303)


@bp.get("/queue")
@role_required("agent", "admin")
def queue():
    rows = get_db().execute(
        "SELECT t.*, r.display_name AS requester_name FROM tickets t "
        "JOIN users r ON r.id = t.requester_id "
        "WHERE t.status = 'open' AND t.assigned_agent_id IS NULL "
        "ORDER BY CASE t.urgency WHEN 'high' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, "
        "t.created_at, t.id"
    ).fetchall()
    return render_template("ticket_list.html", tickets=rows, heading="Antrean tanpa petugas", is_queue=True)


@bp.post("/tickets/<int:ticket_id>/claim")
@role_required("agent")
def claim(ticket_id):
    claim_ticket(g.user, ticket_id, parse_version(request.form.get("version")))
    return redirect(url_for("tickets.ticket_detail", ticket_id=ticket_id), code=303)


@bp.post("/tickets/<int:ticket_id>/<action>")
@login_required
def transition(ticket_id, action):
    if action not in ("start", "resolve", "close", "reopen"):
        abort(404)
    change_status(
        g.user,
        ticket_id,
        parse_version(request.form.get("version")),
        action,
        request.form.get("reason"),
    )
    return redirect(url_for("tickets.ticket_detail", ticket_id=ticket_id), code=303)
