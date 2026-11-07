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
            raise ValueError("Admin sudah ada. Buat akun tambahan dari halaman Akun tim.")
        create_user(connection, name, email, password, "admin")


def main():
    app = create_app()
    with app.app_context():
        if get_db().execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            raise SystemExit("Admin sudah ada. Buat akun tambahan dari halaman Akun tim.")
        name = input("Nama admin pertama: ")
        email = input("Email admin pertama: ")
        password = getpass.getpass("Kata sandi awal (minimal 12 karakter): ")
        confirmation = getpass.getpass("Ulangi kata sandi: ")
        if password != confirmation:
            raise SystemExit("Kata sandi yang diulang tidak sama.")
        try:
            create_first_admin(name, email, password)
        except ValueError as error:
            raise SystemExit(str(error)) from None
        except sqlite3.IntegrityError:
            raise SystemExit("Email sudah digunakan.") from None
    print("Admin pertama dibuat. Masuk melalui halaman login.")


if __name__ == "__main__":
    main()
