from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.db import get_db
from app.ticket_ops import TicketActionError, claim_ticket

from tests.fixtures import TicketTestCase


class ClaimConcurrencyTests(TicketTestCase):
    def test_two_connections_have_one_winner(self):
        ticket_id = self.new_ticket()
        gate = Barrier(2)

        def attempt(agent_id):
            with self.app.app_context():
                user = get_db().execute("SELECT * FROM users WHERE id = ?", (agent_id,)).fetchone()
                gate.wait()
                try:
                    claim_ticket(user, ticket_id, 0)
                    return "claimed"
                except TicketActionError as error:
                    return error.status

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, (self.users["Budi"], self.users["Doni"])))
        self.assertCountEqual(outcomes, ("claimed", 409))
        with self.app.app_context():
            connection = get_db()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ticket_events WHERE ticket_id = ? AND event_type = 'claimed'", (ticket_id,)).fetchone()[0], 1)
            self.assertIn(connection.execute("SELECT assigned_agent_id FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0], (self.users["Budi"], self.users["Doni"]))
