import sqlite3

from app.db import get_db
from app.ticket_ops import TicketActionError, claim_ticket

from tests.fixtures import TicketTestCase


class TicketFlowTests(TicketTestCase):
    def test_create_is_unassigned_and_private(self):
        self.login_as("Rina")
        created = self.client.post("/tickets", data={"title": "Projector is off", "description": "The lamp does not turn on.", "work_blocked": "yes", "workaround_available": "no", "waiting_impact": "disruptive"})
        self.assertEqual(created.status_code, 303)
        ticket_id = int(created.headers["Location"].split("/")[-1])
        with self.app.app_context():
            ticket = get_db().execute("SELECT status, assigned_agent_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            self.assertEqual(ticket["status"], "open")
            self.assertIsNone(ticket["assigned_agent_id"])
        self.login_as("Wati")
        self.assertEqual(self.client.get(f"/tickets/{ticket_id}").status_code, 404)
        self.assertNotIn("Projector is off", self.client.get("/tickets").get_data(as_text=True))
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/close", data={"version": 0}).status_code, 404)

    def test_user_text_is_escaped_in_html(self):
        self.login_as("Rina")
        response = self.client.post(
            "/tickets",
            data={"title": "<script>alert(1)</script>", "description": "<b>text</b>", "work_blocked": "no", "workaround_available": "yes", "waiting_impact": "minor"},
        )
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_agent_claim_start_resolve_requester_reopen_close(self):
        ticket_id = self.new_ticket()
        self.login_as("Budi")
        self.assertEqual(self.client.get("/queue").status_code, 200)
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/claim", data={"version": 0}).status_code, 303)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0], "open")
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/start", data={"version": 1}).status_code, 303)
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/resolve", data={"version": 2}).status_code, 303)
        self.login_as("Rina")
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/reopen", data={"version": 3, "reason": "Still disconnected"}).status_code, 303)
        with self.app.app_context():
            ticket = get_db().execute("SELECT status, assigned_agent_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            self.assertEqual((ticket["status"], ticket["assigned_agent_id"]), ("open", self.users["Budi"]))
        self.login_as("Budi")
        self.client.post(f"/tickets/{ticket_id}/start", data={"version": 4})
        self.client.post(f"/tickets/{ticket_id}/resolve", data={"version": 5})
        self.login_as("Rina")
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/close", data={"version": 6}).status_code, 303)
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/reopen", data={"version": 7, "reason": "It happened again"}).status_code, 303)

    def test_role_and_transition_rules(self):
        ticket_id = self.new_ticket()
        self.login_as("Rina")
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/claim", data={"version": 0}).status_code, 403)
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/close", data={"version": 0}).status_code, 409)
        self.login_as("Budi")
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/resolve", data={"version": 0}).status_code, 404)
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/claim", data={"version": 0}).status_code, 303)
        self.login_as("Doni")
        self.assertEqual(self.client.get(f"/tickets/{ticket_id}").status_code, 404)
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/start", data={"version": 1}).status_code, 404)
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/claim", data={"version": 0}).status_code, 409)

    def test_admin_assign_reassign_and_stale_form(self):
        ticket_id = self.new_ticket()
        self.login_as("Sari")
        self.assertEqual(self.client.post(f"/admin/tickets/{ticket_id}/assign", data={"version": 0, "agent_id": self.users["Budi"]}).status_code, 303)
        self.assertEqual(self.client.post(f"/admin/tickets/{ticket_id}/assign", data={"version": 0, "agent_id": self.users["Doni"], "reason": "Agent unavailable"}).status_code, 409)
        self.assertEqual(self.client.post(f"/admin/tickets/{ticket_id}/assign", data={"version": 1, "agent_id": self.users["Doni"]}).status_code, 400)
        self.assertEqual(self.client.post(f"/admin/tickets/{ticket_id}/assign", data={"version": 1, "agent_id": self.users["Doni"], "reason": "Agent unavailable"}).status_code, 303)
        self.assertEqual(self.client.get(f"/admin/tickets/{ticket_id}/history").status_code, 200)
        with self.app.app_context():
            ticket = get_db().execute("SELECT assigned_agent_id, status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            self.assertEqual((ticket["assigned_agent_id"], ticket["status"]), (self.users["Doni"], "open"))

    def test_comment_is_scoped_and_recorded(self):
        ticket_id = self.new_ticket()
        self.login_as("Wati")
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/comments", data={"body": "Not allowed"}).status_code, 404)
        self.login_as("Rina")
        self.assertEqual(self.client.post(f"/tickets/{ticket_id}/comments", data={"body": "Still not working"}).status_code, 303)
        with self.app.app_context():
            connection = get_db()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM comments WHERE ticket_id = ?", (ticket_id,)).fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ticket_events WHERE ticket_id = ? AND event_type = 'commented'", (ticket_id,)).fetchone()[0], 1)

    def test_event_failure_rolls_back_claim(self):
        ticket_id = self.new_ticket()
        with self.app.app_context():
            connection = get_db()
            connection.execute("CREATE TRIGGER reject_claim BEFORE INSERT ON ticket_events WHEN NEW.event_type = 'claimed' BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            user = connection.execute("SELECT * FROM users WHERE id = ?", (self.users["Budi"],)).fetchone()
            with self.assertRaises(sqlite3.IntegrityError):
                claim_ticket(user, ticket_id, 0)
            ticket = connection.execute("SELECT assigned_agent_id, version FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            self.assertEqual((ticket["assigned_agent_id"], ticket["version"]), (None, 0))
