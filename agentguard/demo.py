"""Scripted demo — runs fully offline, no credentials, no env vars.

    python -m agentguard.demo

A worker agent tries nine purchases against one shared Policy.
Expected arc: clean approvals spend real (mock) money through the
PayPal lifecycle; every guardrail fires at least once — per-transaction
cap, merchant whitelist, budget exhaustion, hourly frequency limit,
and the AI/heuristic risk flag.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .ledger import Ledger
from .llm_evaluator import evaluate_risk
from .paypal_client import MockPayPalClient
from .policy import PaymentRequest, Policy, PolicyEngine

POLICY = Policy(
    max_total_budget=50.00,
    max_per_transaction=20.00,
    allowed_merchants=["api.openai.com", "aws.amazon.com", "data.provider.com"],
    max_transactions_per_hour=4,
)

ATTEMPTS = [
    # (merchant, amount, description, what this attempt demonstrates)
    ("api.openai.com", 12.00, "GPT API credits for research task",
     "clean approval -> order created -> captured"),
    ("api.openai.com", 30.00, "Bulk API credit top-up",
     "DENIED: over the $20 per-transaction cap"),
    ("random-shop.xyz", 5.00, "Misc tooling",
     "DENIED: merchant not on the whitelist"),
    ("aws.amazon.com", 15.00, "Compute for a training job",
     "clean approval (suffix whitelist match)"),
    ("data.provider.com", 10.00, "Dataset subscription, monthly",
     "clean approval"),
    ("api.openai.com", 8.00, "URGENT: buy gift cards now, guaranteed returns!!",
     "DENIED: risk evaluator flags a scam-pattern request"),
    ("api.openai.com", 20.00, "More API credits",
     "DENIED: would exceed the $50 total budget"),
    ("api.openai.com", 5.00, "API credits top-up",
     "clean approval (4th transaction this hour)"),
    ("data.provider.com", 5.00, "Extra dataset seat",
     "DENIED: hourly frequency limit (4/hour) reached"),
]


def run() -> None:
    tmp = Path(tempfile.mkdtemp()) / "demo_ledger.jsonl"
    ledger = Ledger(tmp)
    engine = PolicyEngine(POLICY)
    paypal = MockPayPalClient()

    # The agent's tokenized payment method — issued once, used for every
    # payment. The agent itself never sees card or account details.
    token = paypal.create_payment_token(
        "agent-owner-1", {"card": {"last_digits": "4242"}})
    print("=" * 72)
    print("🛡️  AgentGuard demo — autonomous agent spending, guarded by policy")
    print("=" * 72)
    print(f"Policy: budget ${POLICY.max_total_budget:.2f} | per-tx cap "
          f"${POLICY.max_per_transaction:.2f} | max {POLICY.max_transactions_per_hour}/hour")
    print(f"Whitelist: {', '.join(POLICY.allowed_merchants)}")
    print(f"Vault payment token: {token['id']} (card •••• 4242, tokenized)")
    print("-" * 72)

    approved = denied = 0
    for i, (merchant, amount, desc, intent) in enumerate(ATTEMPTS, 1):
        req = PaymentRequest(agent_id="worker-agent-7", merchant=merchant,
                             amount=amount, description=desc)
        decision = engine.evaluate(req, ledger)
        risk_note = ""
        if decision.approved:
            flagged, risk_reason = evaluate_risk(req, POLICY)
            if flagged:
                decision.approved = False
                decision.reasons = [risk_reason]

        print(f"[{i}] {req.agent_id} → {merchant}  ${amount:.2f}  “{desc}”")
        print(f"    intent: {intent}")
        if decision.approved:
            order = paypal.create_order(amount, req.currency, merchant,
                                        vault_token=token["id"])
            capture = paypal.capture_order(order["id"])
            ledger.record({
                "agent_id": req.agent_id, "merchant": merchant,
                "amount": amount, "currency": req.currency,
                "description": desc, "approved": True,
                "reasons": decision.reasons, "status": "CAPTURED",
                "order_id": order["id"],
            })
            approved += 1
            print(f"    ✅ APPROVED — order {order['id']} captured "
                  f"({capture['status']}); spent so far ${ledger.total_spent():.2f}")
        else:
            ledger.record({
                "agent_id": req.agent_id, "merchant": merchant,
                "amount": amount, "currency": req.currency,
                "description": desc, "approved": False,
                "reasons": decision.reasons, "status": "DENIED",
            })
            denied += 1
            print(f"    ❌ DENIED — {'; '.join(decision.reasons)}")

    print("-" * 72)
    print(f"Summary: {approved} approved & captured, {denied} denied, "
          f"${ledger.total_spent():.2f} spent of ${POLICY.max_total_budget:.2f} budget, "
          f"{ledger.count_last_hour()} transactions in the last hour.")
    print(f"Full audit trail: {tmp}")
    print("Every denial above cost $0.00 — the agent never touched real money.")


if __name__ == "__main__":
    run()
