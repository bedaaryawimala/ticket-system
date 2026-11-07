import sqlite3
from pathlib import Path
from unittest.mock import patch

from app.db import get_db
from app.error_logging import reconcile_fallback, record_error

from tests.fixtures import TicketTestCase


class ErrorLoggingTests(TicketTestCase):
    def test_admin_only_review_and_append_notes(self):
        with self.app.app_context():
            event_id = record_error("DEMO_ERROR", "demo", "Kejadian contoh untuk dashboard.", 500)
        self.login_as("Rina")
        self.assertEqual(self.client.get("/admin/errors").status_code, 403)
        self.login_as("Sari")
        self.assertIn(event_id[:12], self.client.get("/admin/errors?q=DEMO_ERROR&status=new").get_data(as_text=True))
        self.assertEqual(self.client.post(f"/admin/errors/{event_id}/notes", data={"body": "Sudah dicek"}).status_code, 303)
        self.assertEqual(self.client.post(f"/admin/errors/{event_id}/review").status_code, 303)
        self.assertIn("Ditinjau", self.client.get(f"/admin/errors/{event_id}").get_data(as_text=True))
        self.assertEqual(self.client.post(f"/admin/errors/{event_id}").status_code, 405)
        with self.app.app_context():
            connection = get_db()
            self.assertEqual(connection.execute("SELECT safe_message FROM error_events WHERE id = ?", (event_id,)).fetchone()[0], "Kejadian contoh untuk dashboard.")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM error_review_notes WHERE error_id = ?", (event_id,)).fetchone()[0], 1)

    def test_db_failure_keeps_safe_file_and_reconcile_is_idempotent(self):
        with self.app.app_context():
            with patch("app.error_logging.get_db", side_effect=sqlite3.OperationalError("token=private")):
                event_id = record_error("DB_UNAVAILABLE", "database", "Penyimpanan sedang tidak tersedia.", 503)
            log_text = Path(self.app.config["ERROR_LOG"]).read_text(encoding="utf-8")
            self.assertIn(event_id, log_text)
            self.assertNotIn("token=private", log_text)
            self.assertEqual(reconcile_fallback(), 1)
            self.assertEqual(reconcile_fallback(), 0)
            connection = get_db()
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM error_events WHERE id = ?", (event_id,)).fetchone()[0], 1)

    def test_catalog_rejects_unsanitized_message(self):
        with self.app.app_context():
            with self.assertRaises(ValueError):
                record_error("APP_ERROR", "application", "token=private", 500)

    def test_reconcile_reads_rotated_file(self):
        with self.app.app_context():
            with patch("app.error_logging.get_db", side_effect=sqlite3.OperationalError("offline")):
                event_id = record_error("DB_UNAVAILABLE", "database", "Penyimpanan sedang tidak tersedia.", 503)
            path = Path(self.app.config["ERROR_LOG"])
            path.replace(path.with_name(f"{path.name}.1"))
            self.assertEqual(reconcile_fallback(), 1)
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM error_events WHERE id = ?", (event_id,)).fetchone()[0], 1)

    def test_reconcile_reports_database_failure(self):
        with self.app.app_context():
            with patch("app.error_logging.get_db", side_effect=sqlite3.OperationalError("offline")):
                record_error("DB_UNAVAILABLE", "database", "Penyimpanan sedang tidak tersedia.", 503)
                with self.assertRaises(sqlite3.OperationalError):
                    reconcile_fallback()
