"""Policy engine: pure decision logic, no I/O.

A developer configures a Policy once; every agent purchase request is
evaluated against it, using the Ledger only for aggregate facts
(how much has been spent, how many transactions in the last hour).
Keeping this module side-effect free makes every rule unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PaymentRequest:
    """One purchase an AI agent wants to make (one tool call)."""

    agent_id: str
    merchant: str  # domain or merchant id, e.g. "api.openai.com"
    amount: float
    currency: str = "USD"
    description: str = ""


@dataclass
class Decision:
    approved: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class Policy:
    max_total_budget: float = 100.0        # lifetime cap across all agents
    max_per_transaction: float = 25.0      # single-purchase cap
    allowed_merchants: list[str] = field(default_factory=list)  # whitelist
    max_transactions_per_hour: int = 5     # frequency / runaway-loop guard

    def merchant_allowed(self, merchant: str) -> bool:
        """Exact match, or suffix match so "amazon.com" allows
        "aws.amazon.com" and "www.amazon.com". An empty whitelist
        denies everything (fail-closed for a payment system)."""
        m = merchant.strip().lower()
        for entry in self.allowed_merchants:
            e = entry.strip().lower()
            if m == e or m.endswith(e):
                return True
        return False


class PolicyEngine:
    def __init__(self, policy: Policy):
        self.policy = policy

    def evaluate(self, request: PaymentRequest, ledger) -> Decision:
        """Return a Decision listing EVERY rule that fired, so the
        audit log explains denials completely, not just the first."""
        p = self.policy
        reasons: list[str] = []

        if request.amount <= 0:
            reasons.append(f"amount {request.amount} is not positive")

        if request.amount > p.max_per_transaction:
            reasons.append(
                f"amount ${request.amount:.2f} exceeds per-transaction "
                f"cap of ${p.max_per_transaction:.2f}"
            )

        spent = ledger.total_spent()
        if spent + request.amount > p.max_total_budget:
            reasons.append(
                f"total budget exceeded: ${spent:.2f} already spent + "
                f"${request.amount:.2f} requested > ${p.max_total_budget:.2f} cap"
            )

        if not p.merchant_allowed(request.merchant):
            reasons.append(
                f"merchant '{request.merchant}' is not on the whitelist "
                f"{p.allowed_merchants}"
            )

        recent = ledger.count_last_hour()
        if recent >= p.max_transactions_per_hour:
            reasons.append(
                f"frequency limit hit: {recent} approved transactions in "
                f"the last hour (max {p.max_transactions_per_hour})"
            )

        if reasons:
            return Decision(approved=False, reasons=reasons)
        return Decision(approved=True, reasons=["all policy checks passed"])
