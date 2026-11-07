from app import create_app
from app.db import init_db


def main():
    app = create_app()
    with app.app_context():
        init_db()
    print("Skema database lokal siap.")


if __name__ == "__main__":
    main()
