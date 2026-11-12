"""AgentGuard Flask server.

Endpoints:
  POST /agent/purchase   — the tool call an LLM agent makes to spend
  GET  /dashboard        — policies, spend totals, full audit table
  POST /webhooks/paypal  — PayPal webhook events -> audit ledger
  GET  /health

Run:  AGENTGUARD_MODE=mock python -m agentguard.server   (port 8080)
"""

from __future__ import annotations

import os
from dataclasses import asdict

from flask import Flask, jsonify, request

from .ledger import Ledger
from .llm_evaluator import evaluate_risk
from .paypal_client import get_client
from .policy import PaymentRequest, Policy, PolicyEngine

# Demo-sized default policy; a real deployment loads this from config.
POLICY = Policy(
    max_total_budget=float(os.environ.get("AG_MAX_TOTAL_BUDGET", "100")),
    max_per_transaction=float(os.environ.get("AG_MAX_PER_TX", "25")),
    allowed_merchants=[
        m.strip() for m in os.environ.get(
            "AG_ALLOWED_MERCHANTS",
            "api.openai.com,aws.amazon.com,data.provider.com",
        ).split(",") if m.strip()
    ],
    max_transactions_per_hour=int(os.environ.get("AG_MAX_PER_HOUR", "5")),
)

app = Flask(__name__)
ledger = Ledger(os.environ.get("AG_LEDGER_PATH", "agentguard_ledger.jsonl"))
engine = PolicyEngine(POLICY)
paypal = get_client()


def process_purchase(req: PaymentRequest) -> dict:
    """The full AgentGuard pipeline for one agent tool call:
    policy check -> AI risk second opinion -> PayPal create+capture
    -> audit. Shared by the HTTP endpoint and easily unit-testable."""
    decision = engine.evaluate(req, ledger)
    if decision.approved:
        flagged, risk_reason = evaluate_risk(req, POLICY)
        if flagged:
            decision.approved = False
            decision.reasons = [risk_reason]

    base = {
        "agent_id": req.agent_id, "merchant": req.merchant,
        "amount": req.amount, "currency": req.currency,
        "description": req.description,
        "approved": decision.approved, "reasons": decision.reasons,
    }
    if not decision.approved:
        ledger.record({**base, "status": "DENIED"})
        return {**base, "status": "DENIED"}

    order = paypal.create_order(req.amount, req.currency, req.merchant)
    capture = paypal.capture_order(order["id"])
    status = "CAPTURED" if capture.get("status") == "COMPLETED" else "APPROVED"
    ledger.record({**base, "status": status, "order_id": order["id"]})
    return {**base, "status": status, "order_id": order["id"]}


@app.post("/agent/purchase")
def agent_purchase():
    data = request.get_json(force=True)
    req = PaymentRequest(
        agent_id=str(data.get("agent_id", "unknown")),
        merchant=str(data.get("merchant", "")),
        amount=float(data.get("amount", 0)),
        currency=str(data.get("currency", "USD")),
        description=str(data.get("description", "")),
    )
    result = process_purchase(req)
    code = 200 if result["approved"] else 403
    return jsonify(result), code


@app.post("/webhooks/paypal")
def paypal_webhook():
    """Record PayPal lifecycle events into the same audit ledger.
    (Production deployments must also verify the webhook signature
    via /v1/notifications/verify-webhook-signature — omitted here for
    demo brevity, noted honestly.)"""
    event = request.get_json(force=True, silent=True) or {}
    event_type = event.get("event_type", "UNKNOWN")
    resource = event.get("resource", {}) or {}
    amount = resource.get("amount", {}) or {}
    ledger.record({
        "status": "WEBHOOK",
        "event_type": event_type,
        "order_id": resource.get("id")
        or (resource.get("supplementary_data", {}) or {}).get("related_ids", {}).get("order_id"),
        "amount": float(amount.get("value", 0) or 0),
        "currency": amount.get("currency_code", ""),
        "approved": event_type in ("PAYMENT.CAPTURE.COMPLETED",
                                   "CHECKOUT.ORDER.APPROVED"),
        "reasons": [f"paypal webhook event {event_type}"],
    })
    return jsonify({"received": True}), 200


@app.get("/health")
def health():
    return jsonify({"status": "ok", "mode": type(paypal).__name__})


@app.get("/dashboard")
def dashboard():
    rows = "".join(
        "<tr><td>{timestamp}</td><td>{agent}</td><td>{merchant}</td>"
        "<td>${amount:.2f}</td><td>{status}</td><td>{reasons}</td></tr>".format(
            timestamp=e.get("timestamp", "")[:19],
            agent=e.get("agent_id", e.get("event_type", "")),
            merchant=e.get("merchant", ""),
            amount=float(e.get("amount", 0) or 0),
            status=e.get("status", ""),
            reasons="; ".join(e.get("reasons", [])),
        )
        for e in reversed(ledger.entries())
    )
    policy = asdict(POLICY)
    return f"""<!doctype html>
<html><head><title>AgentGuard Dashboard</title><style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #14213d; }}
h1 {{ font-size: 1.4rem; }} table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border: 1px solid #ccd; padding: .4rem .6rem; text-align: left;
          font-size: .9rem; }} th {{ background: #eef; }}
.card {{ background: #f6f7fb; padding: 1rem; border-radius: 8px;
         margin-bottom: 1rem; }}
</style></head><body>
<h1>🛡️ AgentGuard — Agent Payment Dashboard</h1>
<div class="card"><strong>Policy</strong><br>
Total budget cap: ${policy['max_total_budget']:.2f} ·
Per-transaction cap: ${policy['max_per_transaction']:.2f} ·
Max transactions/hour: {policy['max_transactions_per_hour']}<br>
Whitelist: {", ".join(policy['allowed_merchants'])}<br>
<strong>Spent so far: ${ledger.total_spent():.2f}</strong> ·
Transactions in last hour: {ledger.count_last_hour()} ·
PayPal client: {type(paypal).__name__}</div>
<table><tr><th>Time (UTC)</th><th>Agent / Event</th><th>Merchant</th>
<th>Amount</th><th>Status</th><th>Reasons</th></tr>{rows}</table>
</body></html>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
