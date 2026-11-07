import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.db import get_db, init_db
from app.ticket_ops import TicketActionError, change_urgency, classify_urgency
from tests.fixtures import TicketTestCase


class UrgencyTests(TicketTestCase):
    def test_classification_rules_and_invalid_answers(self):
        cases = (
            ("no", "yes", "minor", "low"),
            ("no", "no", "minor", "low"),
            ("no", "yes", "disruptive", "normal"),
            ("yes", "yes", "minor", "normal"),
            ("yes", "no", "minor", "high"),
            ("no", "yes", "serious", "high"),
        )
        for blocked, workaround, impact, expected in cases:
            with self.subTest(blocked=blocked, workaround=workaround, impact=impact):
                self.assertEqual(classify_urgency(blocked, workaround, impact), expected)
        for answers in ((None, "yes", "minor"), ("yes", "maybe", "minor"), ("no", "yes", "other")):
            with self.assertRaises(TicketActionError):
                classify_urgency(*answers)

    def test_creation_requires_answers_and_records_urgency(self):
        self.login_as("Rina")
        self.assertEqual(self.client.post("/tickets", data={"title": "Masalah", "description": "Penjelasan"}).status_code, 400)
        self.assertEqual(self.client.post("/tickets", data={
            "title": "Masalah", "description": "Penjelasan", "work_blocked": "yes",
            "workaround_available": "no", "waiting_impact": "invalid",
        }).status_code, 400)
        ticket_id = self.new_ticket()
        with self.app.app_context():
            connection = get_db()
            ticket = connection.execute("SELECT urgency, work_blocked, workaround_available, waiting_impact FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            self.assertEqual(tuple(ticket), ("high", 1, 0, "disruptive"))
            event = connection.execute("SELECT to_urgency FROM ticket_events WHERE ticket_id = ? AND event_type = 'created'", (ticket_id,)).fetchone()
            self.assertEqual(event[0], "high")
        page = self.client.get(f"/tickets/{ticket_id}").get_data(as_text=True)
        self.assertIn("Tinggi", page)
        self.assertIn("Pekerjaan terhenti: Ya", page)

    def test_queue_orders_high_then_normal_then_low(self):
        self.login_as("Rina")
        ids = []
        for impact, blocked, workaround in (("minor", "no", "yes"), ("disruptive", "no", "yes"), ("minor", "yes", "no")):
            response = self.client.post("/tickets", data={
                "title": f"Tiket {len(ids)}", "description": "Penjelasan",
                "work_blocked": blocked, "workaround_available": workaround,
                "waiting_impact": impact,
            })
            ids.append(int(response.headers["Location"].split("/")[-1]))
        self.login_as("Budi")
        page = self.client.get("/queue").get_data(as_text=True)
        self.assertTrue(page.index(f"#{ids[2]}") < page.index(f"#{ids[1]}") < page.index(f"#{ids[0]}"))

    def test_admin_correction_requires_reason_and_fresh_version(self):
        ticket_id = self.new_ticket()
        self.login_as("Rina")
        self.assertEqual(self.client.post(f"/admin/tickets/{ticket_id}/urgency", data={"version": 0, "urgency": "low", "reason": "Coba"}).status_code, 403)
        self.login_as("Sari")
        url = f"/admin/tickets/{ticket_id}/urgency"
        self.assertEqual(self.client.post(url, data={"version": 0, "urgency": "low", "reason": ""}).status_code, 400)
        self.assertEqual(self.client.post(url, data={"version": 0, "urgency": "bad", "reason": "Tinjau"}).status_code, 400)
        self.assertEqual(self.client.post(url, data={"version": 0, "urgency": "low", "reason": "Pekerjaan masih berjalan"}).status_code, 303)
        self.assertEqual(self.client.post(url, data={"version": 0, "urgency": "normal", "reason": "Form lama"}).status_code, 409)
        with self.app.app_context():
            connection = get_db()
            ticket = connection.execute("SELECT urgency, version, status, assigned_agent_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            self.assertEqual(tuple(ticket), ("low", 1, "open", None))
            event = connection.execute("SELECT from_urgency, to_urgency, reason FROM ticket_events WHERE ticket_id = ? AND event_type = 'urgency_changed'", (ticket_id,)).fetchone()
            self.assertEqual(tuple(event), ("high", "low", "Pekerjaan masih berjalan"))
        history = self.client.get(f"/admin/tickets/{ticket_id}/history").get_data(as_text=True)
        self.assertIn("Tinggi → Rendah", history)
        self.assertIn("Pekerjaan masih berjalan", history)
        self.assertIn("Wi-Fi lab terputus", self.client.get("/admin/tickets?urgency=low").get_data(as_text=True))
        self.assertNotIn("Wi-Fi lab terputus", self.client.get("/admin/tickets?urgency=high").get_data(as_text=True))

    def test_event_failure_rolls_back_urgency(self):
        ticket_id = self.new_ticket()
        with self.app.app_context():
            connection = get_db()
            connection.execute("CREATE TRIGGER reject_urgency BEFORE INSERT ON ticket_events WHEN NEW.event_type = 'urgency_changed' BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            admin = connection.execute("SELECT * FROM users WHERE id = ?", (self.users["Sari"],)).fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                change_urgency(admin, ticket_id, 0, "low", "Koreksi")
            ticket = connection.execute("SELECT urgency, version FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            self.assertEqual(tuple(ticket), ("high", 0))


class LegacyMigrationTests(unittest.TestCase):
    def test_existing_tickets_and_events_survive_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite3"
            legacy = sqlite3.connect(database)
            legacy.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, display_name TEXT, password_hash TEXT, role TEXT, active INTEGER DEFAULT 1, session_version INTEGER DEFAULT 0, created_at TEXT);
                CREATE TABLE tickets (id INTEGER PRIMARY KEY, requester_id INTEGER REFERENCES users(id), assigned_agent_id INTEGER, title TEXT, description TEXT, status TEXT DEFAULT 'open', version INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT, resolved_at TEXT, closed_at TEXT);
                CREATE TABLE comments (id INTEGER PRIMARY KEY, ticket_id INTEGER REFERENCES tickets(id), author_id INTEGER REFERENCES users(id), body TEXT, created_at TEXT);
                CREATE TABLE ticket_events (id INTEGER PRIMARY KEY, ticket_id INTEGER REFERENCES tickets(id), actor_id INTEGER REFERENCES users(id), event_type TEXT CHECK (event_type IN ('created', 'claimed', 'assigned', 'reassigned', 'unassigned', 'started', 'resolved', 'closed', 'reopened', 'commented')), from_status TEXT, to_status TEXT, from_assignee_id INTEGER, to_assignee_id INTEGER, comment_id INTEGER, reason TEXT, occurred_at TEXT);
                INSERT INTO users VALUES (1, 'demo@example.test', 'Demo', 'hash', 'requester', 1, 0, '2026-01-01');
                INSERT INTO tickets VALUES (7, 1, NULL, 'Tiket lama', 'Masalah lama', 'open', 0, '2026-01-01', '2026-01-01', NULL, NULL);
                INSERT INTO ticket_events VALUES (9, 7, 1, 'created', NULL, 'open', NULL, NULL, NULL, NULL, '2026-01-01');
            """)
            legacy.close()
            app = create_app({"TESTING": True, "SECRET_KEY": "test-secret", "DATABASE": str(database)})
            with app.app_context():
                init_db()
                init_db()
                connection = get_db()
                ticket = connection.execute("SELECT title, urgency, work_blocked FROM tickets WHERE id = 7").fetchone()
                self.assertEqual(tuple(ticket), ("Tiket lama", "normal", None))
                event = connection.execute("SELECT id, event_type, from_urgency FROM ticket_events WHERE id = 9").fetchone()
                self.assertEqual(tuple(event), (9, "created", None))
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
