import base64
import json
import os
import time
from cryptography.fernet import Fernet


class CredentialVault:
    """Encrypts simulated credentials; plaintext is never returned by API routes."""

    def __init__(self, key: bytes | None = None):
        self._fernet = Fernet(key or os.getenv("CREDENTIAL_VAULT_KEY", Fernet.generate_key()))

    def seal(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode())

    def destroy(self, sealed: bytes) -> None:
        del sealed

    def inspect_jwt_metadata(self, token: str, now: int | None = None) -> dict:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("token is not a JWT-shaped value")
        try:
            payload = json.loads(base64.urlsafe_b64decode(parts[1] + "==="))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("JWT payload is not valid JSON") from exc
        current = int(time.time() if now is None else now)
        exp = payload.get("exp")
        return {
            "algorithm_unverified": True,
            "subject_present": bool(payload.get("sub")),
            "issued_at": payload.get("iat"),
            "expires_at": exp,
            "expired": isinstance(exp, int) and exp <= current,
        }
