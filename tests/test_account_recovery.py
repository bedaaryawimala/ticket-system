import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.db import get_db
from tests.fixtures import TicketTestCase


class AccountRecoveryTests(TicketTestCase):
    def register_data(self, email="new@example.test", password="new-password-123"):
        return {
            "display_name": "New User",
            "email": email,
            "password": password,
            "password_confirm": password,
        }

    def latest_code(self):
        outbox = Path(self.app.config["RESET_OUTBOX"])
        return json.loads(outbox.read_text(encoding="utf-8").splitlines()[-1])["code"]

    def test_registration_creates_requester_and_rejects_duplicate(self):
        self.assertEqual(self.client.get("/register").status_code, 200)
        submitted = self.register_data(email="NEW@example.test")
        submitted["role"] = "admin"
        response = self.client.post("/register", data=submitted)
        self.assertEqual(response.status_code, 303)
        with self.app.app_context():
            user = get_db().execute(
                "SELECT role, email FROM users WHERE email = ?", ("new@example.test",)
            ).fetchone()
            self.assertEqual((user["role"], user["email"]), ("requester", "new@example.test"))
        self.assertEqual(self.client.get("/tickets").status_code, 200)
        self.client.post("/logout")
        duplicate = self.client.post("/register", data=self.register_data())
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("already in use", duplicate.get_data(as_text=True))

    def test_registration_checks_password_and_confirmation(self):
        short = self.client.post("/register", data=self.register_data(password="short"))
        self.assertEqual(short.status_code, 400)
        mismatch = self.register_data()
        mismatch["password_confirm"] = "different-123456"
        self.assertEqual(self.client.post("/register", data=mismatch).status_code, 400)
        with self.app.app_context():
            self.assertEqual(
                get_db().execute("SELECT COUNT(*) FROM users WHERE email = 'new@example.test'").fetchone()[0],
                0,
            )

    def test_reset_is_generic_single_use_and_invalidates_old_sessions(self):
        self.login_as("Rina")
        reset_client = self.app.test_client()
        unknown = reset_client.post("/forgot-password", data={"email": "missing@example.test"})
        self.assertEqual(unknown.status_code, 303)
        self.assertFalse(Path(self.app.config["RESET_OUTBOX"]).exists())
        requested = reset_client.post("/forgot-password", data={"email": "rina@example.test"})
        self.assertEqual(requested.status_code, 303)
        code = self.latest_code()
        self.assertNotIn(code, requested.get_data(as_text=True))
        with self.app.app_context():
            token = get_db().execute("SELECT token_hash FROM password_reset_tokens").fetchone()[0]
            self.assertEqual(token, hashlib.sha256(code.encode()).hexdigest())
        reset_client.post("/forgot-password", data={"email": "rina@example.test"})
        self.assertEqual(len(Path(self.app.config["RESET_OUTBOX"]).read_text().splitlines()), 1)
        changed = reset_client.post("/reset-password", data={
            "code": code, "password": "new-password-123", "password_confirm": "new-password-123",
        })
        self.assertEqual(changed.status_code, 303)
        self.assertEqual(self.client.get("/tickets").status_code, 302)
        self.assertEqual(reset_client.post("/reset-password", data={
            "code": code, "password": "other-password-123", "password_confirm": "other-password-123",
        }).status_code, 400)
        self.assertIn("incorrect", reset_client.post("/login", data={
            "email": "rina@example.test", "password": "test-password-123",
        }).get_data(as_text=True))
        self.assertEqual(reset_client.post("/login", data={
            "email": "rina@example.test", "password": "new-password-123",
        }).status_code, 303)

    def test_expired_code_and_inactive_account(self):
        self.client.post("/forgot-password", data={"email": "rina@example.test"})
        code = self.latest_code()
        with self.app.app_context():
            get_db().execute("UPDATE password_reset_tokens SET expires_at = '2000-01-01T00:00:00+00:00'")
        expired = self.client.post("/reset-password", data={
            "code": code, "password": "new-password-123", "password_confirm": "new-password-123",
        })
        self.assertEqual(expired.status_code, 400)
        with self.app.app_context():
            get_db().execute("UPDATE users SET active = 0 WHERE id = ?", (self.users["Wati"],))
        self.client.post("/forgot-password", data={"email": "wati@example.test"})
        self.assertEqual(len(Path(self.app.config["RESET_OUTBOX"]).read_text().splitlines()), 1)

    def test_mailbox_failure_rolls_back_code(self):
        with patch("app.auth.deliver_reset_code", side_effect=OSError("outbox unavailable")):
            response = self.client.post("/forgot-password", data={"email": "rina@example.test"})
        self.assertEqual(response.status_code, 500)
        with self.app.app_context():
            count = get_db().execute("SELECT COUNT(*) FROM password_reset_tokens").fetchone()[0]
            self.assertEqual(count, 0)

    def test_csrf_protects_new_forms(self):
        app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret-key",
            "DATABASE": self.app.config["DATABASE"],
            "ERROR_LOG": self.app.config["ERROR_LOG"],
            "RESET_OUTBOX": self.app.config["RESET_OUTBOX"],
        })
        client = app.test_client()
        for path, data in (
            ("/register", self.register_data()),
            ("/forgot-password", {"email": "rina@example.test"}),
            ("/reset-password", {"code": "invalid", "password": "new-password-123", "password_confirm": "new-password-123"}),
        ):
            self.assertEqual(client.post(path, data=data).status_code, 400)
