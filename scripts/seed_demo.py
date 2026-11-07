import os

from app import create_app
from app.auth import hash_password
from app.db import get_db
from app.error_logging import record_error
from app.ticket_ops import utc_now

DEMO_USERS = (
    ("rina.demo@example.test", "Rina", "requester"),
    ("budi.demo@example.test", "Budi", "agent"),
    ("sari.demo@example.test", "Sari", "admin"),
)


def main():
    password = os.environ.get("TICKET_DEMO_PASSWORD", "")
    if len(password) < 12:
        raise SystemExit("Set TICKET_DEMO_PASSWORD to at least 12 characters for local demo accounts.")
    app = create_app()
    with app.app_context():
        connection = get_db()
        addresses = tuple(user[0] for user in DEMO_USERS)
        existing = connection.execute(
            "SELECT COUNT(*) FROM users WHERE email IN (?, ?, ?)", addresses
        ).fetchone()[0]
        if existing:
            raise SystemExit("Akun demo sudah ada. Seed tidak dijalankan ulang.")
        now = utc_now()
        connection.execute("BEGIN IMMEDIATE")
        try:
            ids = {}
            for email, name, role in DEMO_USERS:
                cursor = connection.execute(
                    "INSERT INTO users (email, display_name, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                    (email, name, hash_password(password), role, now),
                )
                ids[role] = cursor.lastrowid
            examples = (
                ("Wi-Fi lab tidak tersambung", "Laptop tidak mendapat koneksi sejak pagi. Sudah mencoba menyambung ulang.", "open", None),
                ("Akses ruang rapat", "Kartu akses belum membuka ruang rapat lantai dua.", "in_progress", ids["agent"]),
                ("Permintaan akun percobaan", "Saya memerlukan akun percobaan untuk pelatihan internal.", "resolved", ids["agent"]),
            )
            for title, description, status, agent_id in examples:
                cursor = connection.execute(
                    "INSERT INTO tickets (requester_id, assigned_agent_id, title, description, status, version, created_at, updated_at, resolved_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ids["requester"], agent_id, title, description, status, 0 if status == "open" else 2 if status == "in_progress" else 3, now, now, now if status == "resolved" else None),
                )
                ticket_id = cursor.lastrowid
                connection.execute(
                    "INSERT INTO ticket_events (ticket_id, actor_id, event_type, to_status, occurred_at) VALUES (?, ?, 'created', 'open', ?)",
                    (ticket_id, ids["requester"], now),
                )
                if agent_id:
                    connection.execute(
                        "INSERT INTO ticket_events (ticket_id, actor_id, event_type, to_assignee_id, occurred_at) VALUES (?, ?, 'claimed', ?, ?)",
                        (ticket_id, agent_id, agent_id, now),
                    )
                    connection.execute(
                        "INSERT INTO ticket_events (ticket_id, actor_id, event_type, from_status, to_status, occurred_at) VALUES (?, ?, 'started', 'open', 'in_progress', ?)",
                        (ticket_id, agent_id, now),
                    )
                if status == "resolved":
                    connection.execute(
                        "INSERT INTO ticket_events (ticket_id, actor_id, event_type, from_status, to_status, occurred_at) VALUES (?, ?, 'resolved', 'in_progress', 'resolved', ?)",
                        (ticket_id, agent_id, now),
                    )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        record_error("DEMO_ERROR", "demo", "Kejadian contoh untuk dashboard.", 500)
    print("Tiga akun, tiga tiket, dan satu error fiktif tersedia.")


if __name__ == "__main__":
    main()
