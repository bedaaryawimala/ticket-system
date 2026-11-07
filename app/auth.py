import hashlib
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from .db import get_db
from .demo_mail import deliver_reset_code
from .ticket_ops import utc_now, write_transaction

bp = Blueprint("auth", __name__)
password_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def valid_email(email):
    return 3 <= len(email) <= 254 and EMAIL_PATTERN.fullmatch(email) is not None


def valid_password(password):
    return 12 <= len(password) <= 128 and not password.isspace()


def new_user_error(name, email, password, confirmation):
    if not 1 <= len(name) <= 80:
        return "Name must be 1 to 80 characters long."
    if not valid_email(email):
        return "Enter a valid email address."
    if not valid_password(password):
        return "Password must be 12 to 128 characters long."
    if password != confirmation:
        return "Passwords do not match."
    return None


def create_user(connection, name, email, password, role):
    cursor = connection.execute(
        "INSERT INTO users (email, display_name, password_hash, role, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (email, name, hash_password(password), role, utc_now()),
    )
    return cursor.lastrowid


def hash_password(password):
    return password_hasher.hash(password)


def check_password(stored_hash, password):
    try:
        return password_hasher.verify(stored_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def login_required(view):
    @wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return redirect(url_for("auth.login"))
        return view(**kwargs)

    return wrapped


def role_required(*roles):
    def decorate(view):
        @wraps(view)
        @login_required
        def wrapped(**kwargs):
            if g.user["role"] not in roles:
                abort(403)
            return view(**kwargs)

        return wrapped

    return decorate


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user is not None:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if len(email) <= 254 and len(password) <= 1024:
            user = get_db().execute(
                "SELECT id, password_hash, active, session_version "
                "FROM users WHERE email = ?", (email,)
            ).fetchone()
            if user is not None and user["active"] and check_password(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session["auth_version"] = user["session_version"]
                session.permanent = True
                return redirect(url_for("index"), code=303)
        flash("Email or password is incorrect.", "error")
    return render_template("login.html")


@bp.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"), code=303)


@bp.route("/register", methods=("GET", "POST"))
def register():
    if g.user is not None:
        return redirect(url_for("index"))
    if request.method == "GET":
        return render_template("register.html")

    name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirmation = request.form.get("password_confirm", "")
    error = new_user_error(name, email, password, confirmation)

    if error is None:
        try:
            with write_transaction() as connection:
                user_id = create_user(connection, name, email, password, "requester")
        except sqlite3.IntegrityError:
            error = "This email address is already in use. Sign in or reset your password."
        else:
            session.clear()
            session["user_id"] = user_id
            session["auth_version"] = 0
            session.permanent = True
            return redirect(url_for("tickets.ticket_list"), code=303)
    return render_template("register.html", error=error, name=name, email=email), 400


@bp.route("/forgot-password", methods=("GET", "POST"))
def forgot_password():
    if request.method == "GET":
        return render_template("forgot_password.html")

    email = request.form.get("email", "").strip().lower()
    if valid_email(email):
        with write_transaction() as connection:
            user = connection.execute(
                "SELECT id FROM users WHERE email = ? AND active = 1", (email,)
            ).fetchone()
            if user is not None:
                now = datetime.now(timezone.utc)
                cutoff = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
                recent = connection.execute(
                    "SELECT 1 FROM password_reset_tokens "
                    "WHERE user_id = ? AND created_at >= ? LIMIT 1",
                    (user["id"], cutoff),
                ).fetchone()
                if recent is None:
                    code = secrets.token_urlsafe(32)
                    created_at = now.isoformat(timespec="seconds")
                    expires_at = (now + timedelta(minutes=30)).isoformat(timespec="seconds")
                    connection.execute(
                        "INSERT INTO password_reset_tokens "
                        "(token_hash, user_id, created_at, expires_at) "
                        "VALUES (?, ?, ?, ?)",
                        (hashlib.sha256(code.encode()).hexdigest(), user["id"], created_at, expires_at),
                    )
                    deliver_reset_code(email, code, created_at)
    return redirect(url_for("auth.reset_sent"), code=303)


@bp.get("/forgot-password/sent")
def reset_sent():
    return render_template("reset_sent.html")


@bp.route("/reset-password", methods=("GET", "POST"))
def reset_password():
    if request.method == "GET":
        return render_template("reset_password.html")

    code = request.form.get("code", "").strip()
    password = request.form.get("password", "")
    confirmation = request.form.get("password_confirm", "")
    error = None
    if not 1 <= len(code) <= 128:
        error = "The reset code is invalid or has expired."
    elif not valid_password(password):
        error = "Password must be 12 to 128 characters long."
    elif password != confirmation:
        error = "Passwords do not match."
    else:
        token_hash = hashlib.sha256(code.encode()).hexdigest()
        with write_transaction() as connection:
            token = connection.execute(
                "SELECT p.user_id FROM password_reset_tokens p "
                "JOIN users u ON u.id = p.user_id "
                "WHERE p.token_hash = ? AND p.used_at IS NULL "
                "AND p.expires_at > ? AND u.active = 1",
                (token_hash, utc_now()),
            ).fetchone()
            if token is None:
                error = "The reset code is invalid or has expired."
            else:
                connection.execute(
                    "UPDATE users SET password_hash = ?, session_version = session_version + 1 "
                    "WHERE id = ?",
                    (hash_password(password), token["user_id"]),
                )
                connection.execute(
                    "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ?",
                    (utc_now(), token_hash),
                )
                connection.execute(
                    "UPDATE password_reset_tokens SET used_at = ? "
                    "WHERE user_id = ? AND used_at IS NULL",
                    (utc_now(), token["user_id"]),
                )
        if error is None:
            session.clear()
            flash("Password changed. Please sign in.")
            return redirect(url_for("auth.login"), code=303)
    return render_template("reset_password.html", error=error, code=code), 400
