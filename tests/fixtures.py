import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.auth import hash_password
from app.db import get_db, init_db
from app.ticket_ops import create_ticket, utc_now


class TicketTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret-key",
            "DATABASE": str(root / "test.sqlite3"),
            "ERROR_LOG": str(root / "events.jsonl"),
            "RESET_OUTBOX": str(root / "reset-mail.jsonl"),
            "WTF_CSRF_ENABLED": False,
        })
        self.client = self.app.test_client()
        with self.app.app_context():
            init_db()
            connection = get_db()
            now = utc_now()
            self.users = {}
            for name, role in (("Rina", "requester"), ("Budi", "agent"), ("Sari", "admin"), ("Doni", "agent"), ("Wati", "requester")):
                cursor = connection.execute(
                    "INSERT INTO users (email, display_name, password_hash, role, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"{name.lower()}@example.test", name, hash_password("test-password-123"), role, now),
                )
                self.users[name] = cursor.lastrowid

    def tearDown(self):
        self.directory.cleanup()

    def login_as(self, name):
        with self.client.session_transaction() as session:
            session["user_id"] = self.users[name]

    def new_ticket(self, owner="Rina"):
        with self.app.app_context():
            user = get_db().execute("SELECT * FROM users WHERE id = ?", (self.users[owner],)).fetchone()
            return create_ticket(user, "Wi-Fi lab terputus", "Koneksi terputus sejak pagi.", "yes", "no", "disruptive")

    def version(self, ticket_id):
        with self.app.app_context():
            return get_db().execute("SELECT version FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0]
