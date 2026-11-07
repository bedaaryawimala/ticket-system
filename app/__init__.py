import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

from . import db

csrf = CSRFProtect()


def local_time(timestamp):
    jakarta = timezone(timedelta(hours=7))
    return datetime.fromisoformat(timestamp).astimezone(jakarta).strftime("%d-%m-%Y %H:%M WIB")


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("TICKET_SECRET_KEY"),
        DATABASE=str(Path(app.instance_path) / "tickets.sqlite3"),
        ERROR_LOG=str(Path(app.instance_path) / "error-events.jsonl"),
        RESET_OUTBOX=str(Path(app.instance_path) / "demo-reset-mail.jsonl"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("TICKET_HTTPS_ONLY") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("Set TICKET_SECRET_KEY before starting the app.")

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    csrf.init_app(app)
    db.init_app(app)

    from . import admin, auth, tickets
    from .ticket_ops import TicketActionError

    app.register_blueprint(auth.bp)
    app.register_blueprint(tickets.bp)
    app.register_blueprint(admin.bp)
    app.add_template_filter(local_time)
    app.jinja_env.globals["status_labels"] = {
        "open": "Terbuka", "in_progress": "Dikerjakan",
        "resolved": "Selesai", "closed": "Ditutup",
    }
    app.jinja_env.globals["urgency_labels"] = {
        "low": "Rendah", "normal": "Normal", "high": "Tinggi",
    }
    app.jinja_env.globals["event_labels"] = {
        "created": "Tiket dibuat", "claimed": "Tiket diambil", "assigned": "Petugas ditugaskan",
        "reassigned": "Petugas dipindahkan", "unassigned": "Petugas dilepas",
        "started": "Pekerjaan dimulai", "resolved": "Ditandai selesai",
        "closed": "Ditutup pelapor", "reopened": "Dibuka kembali", "commented": "Komentar ditambahkan",
        "urgency_changed": "Urgensi diubah",
    }

    @app.before_request
    def load_user():
        g.user = None
        user_id = session.get("user_id")
        if user_id is not None:
            g.user = db.get_db().execute(
                "SELECT id, email, display_name, role, active, session_version "
                "FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if (
                g.user is None
                or not g.user["active"]
                or session.get("auth_version", 0) != g.user["session_version"]
            ):
                session.clear()
                g.user = None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.blueprint == "auth":
            response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; img-src 'self'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    def index():
        if g.user is None:
            return redirect(url_for("auth.login"))
        if g.user["role"] == "admin":
            return redirect(url_for("admin.ticket_list"))
        return redirect(url_for("tickets.ticket_list"))

    @app.errorhandler(CSRFError)
    def csrf_failed(_error):
        return render_template("error.html", title="Form kedaluwarsa", message="Muat ulang halaman lalu kirim lagi."), 400

    @app.errorhandler(TicketActionError)
    def ticket_action_failed(error):
        return render_template("error.html", title=f"Kesalahan {error.status}", message=error.message), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {
            400: "Periksa isian lalu coba lagi.",
            403: "Akun ini tidak boleh membuka halaman tersebut.",
            404: "Halaman atau tiket tidak ditemukan.",
            409: "Tiket sudah berubah. Muat ulang halaman lalu coba lagi.",
            413: "Isian terlalu besar.",
        }
        return render_template("error.html", title=f"Kesalahan {error.code}", message=messages.get(error.code, "Permintaan tidak dapat diproses.")), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if isinstance(error, HTTPException):
            return http_error(error)
        from .error_logging import record_error

        if isinstance(error, sqlite3.Error):
            incident_id = record_error("DB_UNAVAILABLE", "database", "Penyimpanan sedang tidak tersedia.", 503)
            status = 503
        else:
            incident_id = record_error("APP_ERROR", "application", "Aplikasi tidak dapat memproses permintaan.", 500)
            status = 500
        return render_template(
            "error.html",
            title="Permintaan belum berhasil",
            message="Coba lagi nanti. Jika masalah berlanjut, sampaikan ID kejadian kepada admin.",
            incident_id=incident_id,
        ), status

    return app
