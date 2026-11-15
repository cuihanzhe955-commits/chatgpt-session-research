import hashlib
from dataclasses import dataclass

import stripe

from .config import Settings


@dataclass(frozen=True)
class CheckoutResult:
    session_id: str
    checkout_url: str | None


class ResearchCheckoutAdapter:
    """Official Stripe test Checkout or an explicitly identified offline simulation."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def create(
        self, order_id: str, amount_cents: int, currency: str, name: str
    ) -> CheckoutResult:
        if self.settings.payment_mode == "offline":
            return CheckoutResult(
                "cs_test_offline_" + hashlib.sha256(order_id.encode()).hexdigest(), None
            )
        session = stripe.checkout.Session.create(
            api_key=self.settings.stripe_secret_key,
            idempotency_key=f"research-checkout-{order_id}",
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": currency,
                        "product_data": {"name": name},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }
            ],
            client_reference_id=order_id,
            metadata={"order_id": order_id, "research_only": "true"},
            success_url=f"{self.settings.site_url}/?checkout=success&order_id={order_id}",
            cancel_url=f"{self.settings.site_url}/?checkout=cancel&order_id={order_id}",
        )
        return CheckoutResult(session.id, session.url)


class UnpublishedOpenAICheckoutAdapter:
    def create(self, *_args, **_kwargs):
        raise NotImplementedError(
            "OpenAI private payment endpoints are deliberately not implemented"
        )
