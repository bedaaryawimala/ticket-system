import tempfile
from pathlib import Path

from app import create_app
from app.db import get_db, init_db
from scripts.create_first_admin import create_first_admin
from tests.fixtures import TicketTestCase


class AdminAccountTests(TicketTestCase):
    def account_data(self, email="nina@example.test", role="agent"):
        return {
            "display_name": "Nina",
            "email": email,
            "role": role,
            "password": "kata-sandi-tim-123",
            "password_confirm": "kata-sandi-tim-123",
        }

    def test_only_admin_can_manage_team_accounts(self):
        self.assertEqual(self.client.get("/admin/accounts").status_code, 302)
        self.login_as("Rina")
        self.assertEqual(self.client.get("/admin/accounts").status_code, 403)
        self.assertEqual(self.client.post("/admin/accounts", data=self.account_data()).status_code, 403)
        self.login_as("Budi")
        self.assertEqual(self.client.get("/admin/accounts").status_code, 403)
        self.assertEqual(self.client.post("/admin/accounts", data=self.account_data()).status_code, 403)

    def test_admin_creates_agent_and_another_admin(self):
        self.login_as("Sari")
        self.assertEqual(self.client.get("/admin/accounts").status_code, 200)
        self.assertEqual(self.client.post("/admin/accounts", data=self.account_data()).status_code, 303)
        self.assertEqual(self.client.post("/admin/accounts", data=self.account_data(
            email="reza@example.test", role="admin"
        )).status_code, 303)
        with self.app.app_context():
            connection = get_db()
            created = connection.execute(
                "SELECT id, role, password_hash FROM users WHERE email = ?",
                ("nina@example.test",),
            ).fetchone()
            self.assertEqual(created["role"], "agent")
            self.assertNotEqual(created["password_hash"], "kata-sandi-tim-123")
            self.assertEqual(connection.execute(
                "SELECT role FROM users WHERE email = ?", ("reza@example.test",)
            ).fetchone()[0], "admin")
            agent_id = created["id"]
        ticket_id = self.new_ticket()
        self.assertEqual(self.client.post(f"/admin/tickets/{ticket_id}/assign", data={
            "version": 0, "agent_id": agent_id,
        }).status_code, 303)
        agent_client = self.app.test_client()
        self.assertEqual(agent_client.post("/login", data={
            "email": "nina@example.test", "password": "kata-sandi-tim-123",
        }).status_code, 303)
        self.assertIn("Wi-Fi lab", agent_client.get("/tickets").get_data(as_text=True))
        admin_client = self.app.test_client()
        self.assertEqual(admin_client.post("/login", data={
            "email": "reza@example.test", "password": "kata-sandi-tim-123",
        }).status_code, 303)
        self.assertEqual(admin_client.get("/admin/accounts").status_code, 200)

    def test_account_form_rejects_invalid_role_and_duplicate(self):
        self.login_as("Sari")
        self.assertEqual(self.client.post("/admin/accounts", data=self.account_data(
            role="requester"
        )).status_code, 400)
        short = self.account_data()
        short["password"] = short["password_confirm"] = "pendek"
        self.assertEqual(self.client.post("/admin/accounts", data=short).status_code, 400)
        self.assertEqual(self.client.post("/admin/accounts", data=self.account_data(
            email="rina@example.test"
        )).status_code, 400)
        with self.app.app_context():
            self.assertEqual(get_db().execute(
                "SELECT COUNT(*) FROM users WHERE email = 'nina@example.test'"
            ).fetchone()[0], 0)

    def test_create_account_requires_csrf(self):
        app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret-key",
            "DATABASE": self.app.config["DATABASE"],
            "ERROR_LOG": self.app.config["ERROR_LOG"],
            "RESET_OUTBOX": self.app.config["RESET_OUTBOX"],
        })
        client = app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = self.users["Sari"]
        self.assertEqual(client.post("/admin/accounts", data=self.account_data()).status_code, 400)

    def test_first_admin_script_refuses_second_admin(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "test-only-secret-key",
                "DATABASE": str(root / "fresh.sqlite3"),
                "ERROR_LOG": str(root / "errors.jsonl"),
                "RESET_OUTBOX": str(root / "reset.jsonl"),
            })
            with app.app_context():
                init_db()
                create_first_admin("Admin Awal", "awal@example.test", "kata-sandi-awal-123")
                self.assertEqual(get_db().execute(
                    "SELECT role FROM users WHERE email = ?", ("awal@example.test",)
                ).fetchone()[0], "admin")
                with self.assertRaisesRegex(ValueError, "Admin sudah ada"):
                    create_first_admin("Admin Dua", "dua@example.test", "kata-sandi-dua-123")
