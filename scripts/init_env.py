"""Create local development settings once, without overwriting an existing .env."""

import base64
import secrets
from pathlib import Path

root = Path(__file__).resolve().parent.parent
template = (root / ".env.example").read_text(encoding="utf-8")
template = template.replace(
    "CREDENTIAL_VAULT_KEY=\n",
    "CREDENTIAL_VAULT_KEY="
    + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    + "\n",
)
template = template.replace(
    "POSTGRES_PASSWORD=\n", "POSTGRES_PASSWORD=" + secrets.token_hex(24) + "\n"
)
try:
    with (root / ".env").open("x", encoding="utf-8") as handle:
        handle.write(template)
except FileExistsError:
    raise SystemExit(".env already exists; no keys or settings were changed.")
print(
    "Created .env for offline mode. Keep this file private and preserve its vault key."
)
