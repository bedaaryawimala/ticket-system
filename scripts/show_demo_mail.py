import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Show the latest active reset code from the local demo outbox.")
    parser.add_argument("--email", required=True, help="Email address of the account requesting a reset")
    args = parser.parse_args()

    instance = Path(__file__).resolve().parents[1] / "instance"
    outbox = instance / "demo-reset-mail.jsonl"
    database = instance / "tickets.sqlite3"
    if not outbox.exists() or not database.exists():
        raise SystemExit("The demo outbox has no reset messages yet.")
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
                print("Open the 'Enter reset code' page in the running application.")
                print(f"Reset code: {message['code']}")
                print("The code expires 30 minutes after the request and can be used once.")
                return
    raise SystemExit("No active reset code was found for that email address.")


if __name__ == "__main__":
    main()
