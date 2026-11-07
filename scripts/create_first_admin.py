import getpass
import sqlite3

from app import create_app
from app.auth import create_user, new_user_error
from app.db import get_db
from app.ticket_ops import write_transaction


def create_first_admin(name, email, password):
    name = name.strip()
    email = email.strip().lower()
    error = new_user_error(name, email, password, password)
    if error:
        raise ValueError(error)
    with write_transaction() as connection:
        if connection.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            raise ValueError("An admin already exists. Create more accounts from Team accounts.")
        create_user(connection, name, email, password, "admin")


def main():
    app = create_app()
    with app.app_context():
        if get_db().execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            raise SystemExit("An admin already exists. Create more accounts from Team accounts.")
        name = input("First admin name: ")
        email = input("First admin email: ")
        password = getpass.getpass("Initial password (at least 12 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")
        try:
            create_first_admin(name, email, password)
        except ValueError as error:
            raise SystemExit(str(error)) from None
        except sqlite3.IntegrityError:
            raise SystemExit("This email address is already in use.") from None
    print("First admin created. Sign in through the login page.")


if __name__ == "__main__":
    main()
