from app import create_app
from app.error_logging import reconcile_fallback


def main():
    app = create_app()
    with app.app_context():
        count = reconcile_fallback()
    print(f"{count} kejadian baru dimasukkan dari log lokal.")


if __name__ == "__main__":
    main()
