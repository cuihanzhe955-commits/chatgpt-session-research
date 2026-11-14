from dataclasses import dataclass
import os
import stripe
from uuid import uuid4


@dataclass
class CheckoutResult:
    session_id: str
    checkout_url: str


class ResearchCheckoutAdapter:
    """Protocol-only adapter. It intentionally cannot call OpenAI or ChatGPT."""

    def create(self, order_id: str, amount_cents: int) -> CheckoutResult:
        if os.getenv("STRIPE_SECRET_KEY"):
            stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
            session = stripe.checkout.Session.create(
                mode="payment",
                line_items=[{
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": "Research test payment"},
                        "unit_amount": amount_cents,
                    },
                    "quantity": 1,
                }],
                metadata={"order_id": order_id, "research_only": "true"},
                success_url=os.getenv("STRIPE_SUCCESS_URL", "http://localhost:5173/success"),
                cancel_url=os.getenv("STRIPE_CANCEL_URL", "http://localhost:5173/cancel"),
            )
            return CheckoutResult(session_id=session.id, checkout_url=session.url)
        return CheckoutResult(
            session_id=f"cs_test_research_{uuid4().hex}",
            checkout_url=f"https://checkout.stripe.test/session/{order_id}",
        )


class UnpublishedOpenAICheckoutAdapter:
    def create(self, *_args, **_kwargs):
        raise NotImplementedError(
            "OpenAI private payment endpoints are deliberately not implemented"
        )
