from contextlib import contextmanager
from datetime import datetime, timezone

from .db import get_db


class TicketActionError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message
        super().__init__(message)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def required_text(raw_text, maximum, field_name):
    if not isinstance(raw_text, str):
        raise TicketActionError(400, f"{field_name} harus berupa teks.")
    cleaned = raw_text.strip()
    if not 1 <= len(cleaned) <= maximum:
        raise TicketActionError(400, f"{field_name} harus berisi 1 sampai {maximum} karakter.")
    return cleaned


def parse_version(raw_version):
    try:
        version = int(raw_version)
    except (TypeError, ValueError):
        raise TicketActionError(400, "Versi tiket tidak valid.") from None
    if version < 0:
        raise TicketActionError(400, "Versi tiket tidak valid.")
    return version


@contextmanager
def write_transaction():
    connection = get_db()
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def ticket_in_transaction(connection, ticket_id):
    ticket = connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if ticket is None:
        raise TicketActionError(404, "Tiket tidak ditemukan.")
    return ticket


def can_view(ticket, user):
    if user["role"] == "admin":
        return True
    if user["role"] == "requester":
        return ticket["requester_id"] == user["id"]
    return ticket["assigned_agent_id"] == user["id"] or (
        ticket["status"] == "open" and ticket["assigned_agent_id"] is None
    )


def classify_urgency(work_blocked, workaround_available, waiting_impact):
    if work_blocked not in ("yes", "no") or workaround_available not in ("yes", "no"):
        raise TicketActionError(400, "Jawab pertanyaan tentang pekerjaan dan solusi sementara.")
    if waiting_impact not in ("minor", "disruptive", "serious"):
        raise TicketActionError(400, "Pilih dampak jika penanganan tertunda.")
    blocked = work_blocked == "yes"
    workaround = workaround_available == "yes"
    if waiting_impact == "serious" or (blocked and not workaround):
        return "high"
    if blocked or waiting_impact == "disruptive":
        return "normal"
    return "low"


def create_ticket(user, title, description, work_blocked, workaround_available, waiting_impact):
    if user["role"] != "requester":
        raise TicketActionError(403, "Akun ini tidak dapat membuat tiket.")
    title = required_text(title, 120, "Judul")
    description = required_text(description, 5000, "Penjelasan")
    urgency = classify_urgency(work_blocked, workaround_available, waiting_impact)
    now = utc_now()
    with write_transaction() as connection:
        cursor = connection.execute(
            "INSERT INTO tickets (requester_id, title, description, urgency, work_blocked, "
            "workaround_available, waiting_impact, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], title, description, urgency, int(work_blocked == "yes"),
             int(workaround_available == "yes"), waiting_impact, now, now),
        )
        ticket_id = cursor.lastrowid
        connection.execute(
            "INSERT INTO ticket_events (ticket_id, actor_id, event_type, to_status, to_urgency, occurred_at) "
            "VALUES (?, ?, 'created', 'open', ?, ?)",
            (ticket_id, user["id"], urgency, now),
        )
    return ticket_id


def change_urgency(user, ticket_id, expected_version, urgency, reason):
    if user["role"] != "admin":
        raise TicketActionError(403, "Hanya admin yang dapat mengubah tingkat urgensi.")
    if urgency not in ("low", "normal", "high"):
        raise TicketActionError(400, "Tingkat urgensi tidak valid.")
    reason = required_text(reason, 500, "Alasan perubahan urgensi")
    now = utc_now()
    with write_transaction() as connection:
        ticket = ticket_in_transaction(connection, ticket_id)
        if ticket["version"] != expected_version:
            raise TicketActionError(409, "Tiket sudah berubah. Muat ulang sebelum mengubah urgensi.")
        if ticket["urgency"] == urgency:
            raise TicketActionError(400, "Tingkat urgensi belum berubah.")
        changed = connection.execute(
            "UPDATE tickets SET urgency = ?, version = version + 1, updated_at = ? "
            "WHERE id = ? AND version = ?",
            (urgency, now, ticket_id, expected_version),
        ).rowcount
        if changed != 1:
            raise TicketActionError(409, "Tiket sudah berubah. Muat ulang sebelum mengubah urgensi.")
        connection.execute(
            "INSERT INTO ticket_events (ticket_id, actor_id, event_type, from_urgency, "
            "to_urgency, reason, occurred_at) "
            "VALUES (?, ?, 'urgency_changed', ?, ?, ?, ?)",
            (ticket_id, user["id"], ticket["urgency"], urgency, reason, now),
        )


def add_comment(user, ticket_id, body):
    body = required_text(body, 2000, "Komentar")
    now = utc_now()
    with write_transaction() as connection:
        ticket = ticket_in_transaction(connection, ticket_id)
        if not can_view(ticket, user) or (user["role"] == "agent" and ticket["assigned_agent_id"] != user["id"]):
            raise TicketActionError(404, "Tiket tidak ditemukan.")
        if ticket["status"] == "closed":
            raise TicketActionError(409, "Tiket sudah ditutup. Pemilik dapat membukanya kembali.")
        cursor = connection.execute(
            "INSERT INTO comments (ticket_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
            (ticket_id, user["id"], body, now),
        )
        connection.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
        connection.execute(
            "INSERT INTO ticket_events (ticket_id, actor_id, event_type, comment_id, occurred_at) VALUES (?, ?, 'commented', ?, ?)",
            (ticket_id, user["id"], cursor.lastrowid, now),
        )


def claim_ticket(user, ticket_id, expected_version):
    if user["role"] != "agent":
        raise TicketActionError(403, "Hanya petugas yang dapat mengambil tiket.")
    now = utc_now()
    with write_transaction() as connection:
        ticket_in_transaction(connection, ticket_id)
        changed = connection.execute(
            "UPDATE tickets SET assigned_agent_id = ?, version = version + 1, updated_at = ? "
            "WHERE id = ? AND status = 'open' AND assigned_agent_id IS NULL AND version = ?",
            (user["id"], now, ticket_id, expected_version),
        ).rowcount
        if changed != 1:
            raise TicketActionError(409, "Tiket sudah diambil atau berubah. Muat ulang antrean.")
        connection.execute(
            "INSERT INTO ticket_events (ticket_id, actor_id, event_type, to_assignee_id, occurred_at) "
            "VALUES (?, ?, 'claimed', ?, ?)",
            (ticket_id, user["id"], user["id"], now),
        )


def assign_ticket(user, ticket_id, expected_version, agent_id, reason):
    if user["role"] != "admin":
        raise TicketActionError(403, "Hanya admin yang dapat menugaskan tiket.")
    now = utc_now()
    with write_transaction() as connection:
        ticket = ticket_in_transaction(connection, ticket_id)
        if ticket["version"] != expected_version:
            raise TicketActionError(409, "Tiket sudah berubah. Muat ulang sebelum menugaskan.")
        if ticket["status"] not in ("open", "in_progress"):
            raise TicketActionError(409, "Tiket pada status ini tidak dapat ditugaskan.")
        if agent_id is not None:
            agent = connection.execute(
                "SELECT id FROM users WHERE id = ? AND role = 'agent' AND active = 1", (agent_id,)
            ).fetchone()
            if agent is None:
                raise TicketActionError(400, "Pilih petugas yang aktif.")
        elif ticket["status"] != "open":
            raise TicketActionError(409, "Tiket yang sedang dikerjakan harus memiliki petugas.")
        if ticket["assigned_agent_id"] == agent_id:
            raise TicketActionError(400, "Petugas tiket belum berubah.")
        if ticket["assigned_agent_id"] is not None:
            reason = required_text(reason, 500, "Alasan perubahan petugas")
        else:
            reason = reason.strip() if isinstance(reason, str) else ""
            if len(reason) > 500:
                raise TicketActionError(400, "Alasan terlalu panjang.")
        changed = connection.execute(
            "UPDATE tickets SET assigned_agent_id = ?, version = version + 1, updated_at = ? "
            "WHERE id = ? AND version = ?",
            (agent_id, now, ticket_id, expected_version),
        ).rowcount
        if changed != 1:
            raise TicketActionError(409, "Tiket sudah berubah. Muat ulang sebelum menugaskan.")
        event_type = "assigned" if ticket["assigned_agent_id"] is None else "unassigned" if agent_id is None else "reassigned"
        connection.execute(
            "INSERT INTO ticket_events (ticket_id, actor_id, event_type, from_assignee_id, to_assignee_id, reason, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, user["id"], event_type, ticket["assigned_agent_id"], agent_id, reason or None, now),
        )


def change_status(user, ticket_id, expected_version, action, reason=None):
    transitions = {
        "start": ("open", "in_progress", "started"),
        "resolve": ("in_progress", "resolved", "resolved"),
        "close": ("resolved", "closed", "closed"),
    }
    now = utc_now()
    with write_transaction() as connection:
        ticket = ticket_in_transaction(connection, ticket_id)
        if action == "reopen":
            if user["role"] != "requester" or ticket["requester_id"] != user["id"]:
                raise TicketActionError(404, "Tiket tidak ditemukan.")
            if ticket["version"] != expected_version:
                raise TicketActionError(409, "Tiket sudah berubah. Muat ulang sebelum melanjutkan.")
            if ticket["status"] not in ("resolved", "closed"):
                raise TicketActionError(409, "Tiket ini belum dapat dibuka kembali.")
            reason = required_text(reason, 500, "Alasan buka kembali")
            to_status, event_type = "open", "reopened"
        else:
            if action not in transitions:
                raise TicketActionError(400, "Tindakan tiket tidak dikenal.")
            from_status, to_status, event_type = transitions[action]
            if action in ("start", "resolve"):
                if user["role"] != "agent" or ticket["assigned_agent_id"] != user["id"]:
                    raise TicketActionError(404, "Tiket tidak ditemukan.")
            elif user["role"] != "requester" or ticket["requester_id"] != user["id"]:
                raise TicketActionError(404, "Tiket tidak ditemukan.")
            if ticket["version"] != expected_version:
                raise TicketActionError(409, "Tiket sudah berubah. Muat ulang sebelum melanjutkan.")
            if ticket["status"] != from_status:
                raise TicketActionError(409, "Status tiket sudah berubah. Muat ulang halaman.")
        resolved_at = now if to_status == "resolved" else None if to_status == "open" else ticket["resolved_at"]
        closed_at = now if to_status == "closed" else None if to_status == "open" else ticket["closed_at"]
        connection.execute(
            "UPDATE tickets SET status = ?, version = version + 1, updated_at = ?, resolved_at = ?, closed_at = ? "
            "WHERE id = ? AND version = ?",
            (to_status, now, resolved_at, closed_at, ticket_id, expected_version),
        )
        connection.execute(
            "INSERT INTO ticket_events (ticket_id, actor_id, event_type, from_status, to_status, reason, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ticket_id, user["id"], event_type, ticket["status"], to_status, reason, now),
        )
