import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Tampilkan kode reset terbaru dari kotak surat demo lokal.")
    parser.add_argument("--email", required=True, help="Email akun yang meminta reset")
    args = parser.parse_args()

    instance = Path(__file__).resolve().parents[1] / "instance"
    outbox = instance / "demo-reset-mail.jsonl"
    database = instance / "tickets.sqlite3"
    if not outbox.exists() or not database.exists():
        raise SystemExit("Kotak surat demo belum berisi pesan reset.")
    with sqlite3.connect(database) as connection:
        for line in reversed(outbox.read_text(encoding="utf-8").splitlines()):
            message = json.loads(line)
            if message["email"] != args.email.strip().lower():
                continue
            token_hash = hashlib.sha256(message["code"].encode()).hexdigest()
            active = connection.execute(
                "SELECT 1 FROM password_reset_tokens "
                "WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                (token_hash, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            ).fetchone()
            if active:
                print("Buka halaman 'Masukkan kode reset' pada aplikasi yang sedang berjalan.")
                print(f"Kode reset: {message['code']}")
                print("Kode berlaku 30 menit sejak diminta dan hanya bisa dipakai sekali.")
                return
    raise SystemExit("Tidak ada kode reset aktif untuk email tersebut.")


if __name__ == "__main__":
    main()
