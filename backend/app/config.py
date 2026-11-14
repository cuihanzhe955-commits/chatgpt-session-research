"""Explicit configuration shared by the API and cleanup worker."""

import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./research.db"
    vault_key: str = ""
    payment_mode: str = "offline"
    stripe_secret_key: str = ""
    webhook_secret: str = ""
    site_url: str = "http://localhost:5173"
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    root_path: str = ""

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            vault_key=os.getenv("CREDENTIAL_VAULT_KEY", ""),
            payment_mode=os.getenv("PAYMENT_MODE", "offline"),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY", ""),
            webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
            site_url=os.getenv("SITE_URL", cls.site_url).rstrip("/"),
            cors_origins=tuple(
                x.strip()
                for x in os.getenv("CORS_ORIGINS", cls.site_url).split(",")
                if x.strip()
            ),
            root_path=os.getenv("ROOT_PATH", "").rstrip("/"),
        )

    def validate(self):
        if not self.vault_key:
            raise ValueError(
                "CREDENTIAL_VAULT_KEY is required; generate a persistent Fernet key"
            )
        if self.payment_mode not in {"offline", "stripe_test"}:
            raise ValueError("PAYMENT_MODE must be offline or stripe_test")
        if self.stripe_secret_key and not self.stripe_secret_key.startswith("sk_test_"):
            raise ValueError("Only Stripe test secret keys are accepted")
        if self.payment_mode == "stripe_test" and (
            not self.stripe_secret_key or not self.webhook_secret.startswith("whsec_")
        ):
            raise ValueError(
                "stripe_test requires STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET"
            )
        url = urlparse(self.site_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.netloc
            or url.username
            or url.fragment
            or url.query
        ):
            raise ValueError(
                "SITE_URL must be an HTTP(S) base URL without credentials, query or fragment"
            )
        if "*" in self.cors_origins:
            raise ValueError("Use explicit CORS_ORIGINS, not a wildcard")
        if self.root_path and not self.root_path.startswith("/"):
            raise ValueError("ROOT_PATH must start with /")
