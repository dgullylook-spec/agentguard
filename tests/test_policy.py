"""Policy-engine and mock end-to-end tests. Run: python -m pytest tests/ -q"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentguard.ledger import Ledger
from agentguard.llm_evaluator import heuristic_evaluate
from agentguard.paypal_client import MockPayPalClient
from agentguard.policy import PaymentRequest, Policy, PolicyEngine

POLICY = Policy(
    max_total_budget=50.0,
    max_per_transaction=20.0,
    allowed_merchants=["api.openai.com", "amazon.com"],
    max_transactions_per_hour=2,
)


def req(merchant="api.openai.com", amount=10.0, description="api credits"):
    return PaymentRequest(agent_id="test-agent", merchant=merchant,
                          amount=amount, description=description)


def captured(ledger: Ledger, amount: float):
    ledger.record({"agent_id": "test-agent", "merchant": "api.openai.com",
                   "amount": amount, "status": "CAPTURED", "approved": True})


def test_clean_request_approved(tmp_path):
    engine = PolicyEngine(POLICY)
    decision = engine.evaluate(req(), Ledger(tmp_path / "l.jsonl"))
    assert decision.approved


def test_per_transaction_cap_denied(tmp_path):
    engine = PolicyEngine(POLICY)
    decision = engine.evaluate(req(amount=20.01), Ledger(tmp_path / "l.jsonl"))
    assert not decision.approved
    assert any("per-transaction" in r for r in decision.reasons)


def test_total_budget_denied(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    captured(ledger, 45.0)
    decision = PolicyEngine(POLICY).evaluate(req(amount=10.0), ledger)
    assert not decision.approved
    assert any("total budget" in r for r in decision.reasons)


def test_whitelist_exact_and_suffix_match(tmp_path):
    engine = PolicyEngine(POLICY)
    ledger = Ledger(tmp_path / "l.jsonl")
    assert engine.evaluate(req(merchant="api.openai.com"), ledger).approved
    # suffix match: "amazon.com" whitelist entry covers subdomains
    assert engine.evaluate(req(merchant="aws.amazon.com"), ledger).approved


def test_whitelist_denies_unknown_merchant(tmp_path):
    decision = PolicyEngine(POLICY).evaluate(
        req(merchant="evil-shop.example"), Ledger(tmp_path / "l.jsonl"))
    assert not decision.approved
    assert any("whitelist" in r for r in decision.reasons)


def test_frequency_limit_denied(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    captured(ledger, 5.0)
    captured(ledger, 5.0)  # 2 in the last hour = policy max
    decision = PolicyEngine(POLICY).evaluate(req(amount=5.0), ledger)
    assert not decision.approved
    assert any("frequency" in r for r in decision.reasons)


def test_denied_entries_do_not_count_as_spend(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record({"amount": 999.0, "status": "DENIED", "approved": False})
    assert ledger.total_spent() == 0.0
    assert ledger.count_last_hour() == 0


def test_heuristic_flags_scam_pattern():
    flagged, reason = heuristic_evaluate(
        req(description="URGENT: buy gift cards, guaranteed returns!"), POLICY)
    assert flagged and "heuristic" in reason
    flagged, _ = heuristic_evaluate(req(description="monthly api credits"), POLICY)
    assert not flagged


def test_mock_paypal_end_to_end_flow(tmp_path):
    """Full pipeline: policy approval -> mock order -> capture -> ledger."""
    ledger = Ledger(tmp_path / "l.jsonl")
    engine = PolicyEngine(POLICY)
    client = MockPayPalClient()

    token = client.create_payment_token("cust-1", {"card": {"last_digits": "4242"}})
    assert token["id"].startswith("AG-MOCK-PMT-")
    assert client.list_payment_tokens("cust-1")["payment_tokens"]

    request = req(amount=12.0)
    decision = engine.evaluate(request, ledger)
    assert decision.approved
    order = client.create_order(12.0, "USD", request.merchant,
                                vault_token=token["id"])
    assert order["status"] == "CREATED"
    capture = client.capture_order(order["id"])
    assert capture["status"] == "COMPLETED"
    ledger.record({"agent_id": request.agent_id, "merchant": request.merchant,
                   "amount": 12.0, "status": "CAPTURED", "approved": True,
                   "order_id": order["id"]})
    assert ledger.total_spent() == 12.0
    assert ledger.count_last_hour() == 1


def test_mock_order_ids_are_deterministic():
    client = MockPayPalClient()
    first = client.create_order(1.0, "USD", "api.openai.com")
    second = client.create_order(2.0, "USD", "api.openai.com")
    assert first["id"] == "AG-MOCK-ORDER-0001"
    assert second["id"] == "AG-MOCK-ORDER-0002"
