from app import create_app
from app.db import init_db


def main():
    app = create_app()
    with app.app_context():
        init_db()
    print("Local database schema is ready.")


if __name__ == "__main__":
    main()
