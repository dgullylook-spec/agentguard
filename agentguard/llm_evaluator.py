"""Optional second-opinion risk evaluator ("Agent A").

The deterministic policy engine is the hard gate. This evaluator adds
an AI second look at *intent*: even a request that fits the budget and
whitelist can smell like a scam ("URGENT gift cards, guaranteed
returns"). If GEMINI_API_KEY is configured, Gemini Flash judges the
request; otherwise — and on ANY error — a transparent keyword
heuristic runs instead. This module never raises: a flaky evaluator
must never crash or silently approve the payment flow, so failures
fall back to the heuristic result.
"""

from __future__ import annotations

import json
import os

import requests

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)

# Patterns strongly associated with agent manipulation / scam spend.
SUSPICIOUS_PATTERNS = [
    "urgent", "act now", "limited time", "gift card", "gift cards",
    "crypto", "wire transfer", "guaranteed return", "double your",
    "no questions asked", "untraceable", "prepaid card",
]


def heuristic_evaluate(request, policy) -> tuple[bool, str]:
    """Return (flagged, reason). Keyword screen over the description."""
    text = (request.description or "").lower()
    hits = [p for p in SUSPICIOUS_PATTERNS if p in text]
    if hits:
        return True, f"heuristic risk flag: description matches {hits}"
    return False, "heuristic screen: no risk patterns found"


def _gemini_evaluate(request, policy) -> tuple[bool, str]:
    api_key = os.environ["GEMINI_API_KEY"]
    prompt = (
        "You are a payment risk reviewer for an autonomous AI agent.\n"
        f"Request: agent={request.agent_id} merchant={request.merchant} "
        f"amount={request.amount} {request.currency} "
        f"description={request.description!r}\n"
        f"Policy: max_per_transaction={policy.max_per_transaction}, "
        f"whitelist={policy.allowed_merchants}\n"
        "Reply with ONLY a JSON object: "
        '{"decision": "approve" or "deny", "reason": "short reason"}'
    )
    resp = requests.post(
        GEMINI_URL,
        params={"key": api_key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=20,
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    verdict = json.loads(text.strip().strip("`").replace("json", "", 1)
                         if text.strip().startswith("`") else text)
    flagged = verdict.get("decision", "approve").lower() == "deny"
    return flagged, f"gemini risk review: {verdict.get('reason', '')}"


def evaluate_risk(request, policy) -> tuple[bool, str]:
    """(flagged, reason). Gemini if configured, heuristic otherwise;
    any Gemini failure degrades to the heuristic, never to a crash."""
    if os.environ.get("GEMINI_API_KEY"):
        try:
            return _gemini_evaluate(request, policy)
        except Exception as exc:  # evaluator must never break payments
            flagged, reason = heuristic_evaluate(request, policy)
            return flagged, f"{reason} (gemini unavailable: {exc.__class__.__name__})"
    return heuristic_evaluate(request, policy)
