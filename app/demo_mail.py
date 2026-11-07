import json
import os
from pathlib import Path

from flask import current_app


def deliver_reset_code(email, code, created_at):
    path = Path(current_app.config["RESET_OUTBOX"])
    path.parent.mkdir(parents=True, exist_ok=True)
    message = json.dumps(
        {"email": email, "code": code, "created_at": created_at},
        ensure_ascii=False,
    ) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as outbox:
        outbox.write(message)
