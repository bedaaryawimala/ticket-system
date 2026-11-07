from app import create_app
from app.db import get_db

from tests.fixtures import TicketTestCase


class AuthenticationTests(TicketTestCase):
    def test_smoke_login_and_static(self):
        self.assertEqual(self.client.get("/login").status_code, 200)
        response = self.client.get("/static/style.css")
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_login_logout_and_wrong_password(self):
        wrong = self.client.post("/login", data={"email": "rina@example.test", "password": "wrong"})
        self.assertEqual(wrong.status_code, 200)
        self.assertIn("Email or password is incorrect", wrong.get_data(as_text=True))
        accepted = self.client.post("/login", data={"email": "rina@example.test", "password": "test-password-123"})
        self.assertEqual(accepted.status_code, 303)
        self.assertEqual(self.client.get("/tickets").status_code, 200)
        self.assertEqual(self.client.post("/logout").status_code, 303)
        self.assertEqual(self.client.get("/tickets").status_code, 302)

    def test_inactive_account_loses_access(self):
        self.login_as("Budi")
        with self.app.app_context():
            get_db().execute("UPDATE users SET active = 0 WHERE id = ?", (self.users["Budi"],))
        self.assertEqual(self.client.get("/queue").status_code, 302)

    def test_csrf_blocks_post_without_token(self):
        app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-only-secret-key",
            "DATABASE": self.app.config["DATABASE"],
            "ERROR_LOG": self.app.config["ERROR_LOG"],
        })
        client = app.test_client()
        response = client.post("/login", data={"email": "rina@example.test", "password": "test-password-123"})
        self.assertEqual(response.status_code, 400)
