from dataclasses import dataclass
from uuid import uuid4


@dataclass
class CheckoutResult:
    session_id: str
    checkout_url: str


class ResearchCheckoutAdapter:
    """Protocol-only adapter. It intentionally cannot call OpenAI or ChatGPT."""

    def create(self, order_id: str, amount_cents: int) -> CheckoutResult:
        return CheckoutResult(
            session_id=f"cs_test_research_{uuid4().hex}",
            checkout_url=f"https://checkout.stripe.test/session/{order_id}",
        )


class UnpublishedOpenAICheckoutAdapter:
    def create(self, *_args, **_kwargs):
        raise NotImplementedError(
            "OpenAI private payment endpoints are deliberately not implemented"
        )
