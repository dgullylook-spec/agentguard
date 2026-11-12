"""PayPal integration: live Sandbox client + offline mock.

Both classes expose the same interface, so the server and demo never
care which one they hold:

    client = get_client()            # env AGENTGUARD_MODE picks live|mock
    order  = client.create_order(amount, currency, merchant)
    result = client.capture_order(order["id"])

Live mode talks to the real PayPal Sandbox:
  * OAuth2 client-credentials  POST /v1/oauth2/token
  * Orders API v2              POST /v2/checkout/orders (+ /capture)
  * Vault API v3               POST /v3/vault/payment-tokens (tokenized
                               payment methods — the agent never sees a
                               raw card or account credential)
Mock mode simulates the identical lifecycle deterministically, offline,
with zero credentials, so the hackathon demo always runs.
"""

from __future__ import annotations

import base64
import os
import itertools

import requests

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
LIVE_BASE = "https://api-m.paypal.com"


class PayPalClient:
    """Live PayPal REST client (Sandbox by default)."""

    def __init__(self, client_id: str, client_secret: str, env: str = "sandbox"):
        self.base = SANDBOX_BASE if env != "live" else LIVE_BASE
        self._auth = base64.b64encode(
            f"{client_id}:{client_secret}".encode()
        ).decode()
        self._token: str | None = None

    # -- auth ----------------------------------------------------------------
    def _access_token(self) -> str:
        if self._token:
            return self._token
        resp = requests.post(
            f"{self.base}/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {self._auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=30,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
        }

    # -- Orders API v2 ---------------------------------------------------------
    def create_order(self, amount: float, currency: str, merchant: str,
                     vault_token: str | None = None) -> dict:
        body: dict = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {"currency_code": currency,
                           "value": f"{amount:.2f}"},
                "description": f"AgentGuard payment to {merchant}",
            }],
        }
        # Pay with a Vault token instead of redirecting a human to checkout:
        # this is what lets an autonomous agent pay without credentials.
        if vault_token:
            body["payment_source"] = {
                "token": {"id": vault_token, "type": "PAYMENT_METHOD_TOKEN"}
            }
        resp = requests.post(f"{self.base}/v2/checkout/orders",
                             headers=self._headers(), json=body, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def capture_order(self, order_id: str) -> dict:
        resp = requests.post(
            f"{self.base}/v2/checkout/orders/{order_id}/capture",
            headers=self._headers(), json={}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # -- Vault API v3: payment-method tokens ----------------------------------
    def create_payment_token(self, customer_id: str,
                             payment_source: dict) -> dict:
        """Tokenize a payment method for a customer. In production the
        payment_source comes from a one-time human-approved setup flow;
        afterwards the agent only ever holds this opaque token."""
        resp = requests.post(
            f"{self.base}/v3/vault/payment-tokens",
            headers=self._headers(),
            json={"customer": {"id": customer_id},
                  "payment_source": payment_source},
            timeout=30)
        resp.raise_for_status()
        return resp.json()

    def list_payment_tokens(self, customer_id: str) -> dict:
        resp = requests.get(
            f"{self.base}/v3/vault/payment-tokens",
            headers=self._headers(),
            params={"customer_id": customer_id}, timeout=30)
        resp.raise_for_status()
        return resp.json()


class MockPayPalClient:
    """Deterministic, offline stand-in with the same interface.

    Simulates the full lifecycle: order CREATED -> capture COMPLETED,
    plus Vault token issue/list. Fake IDs are sequential so demo
    transcripts and tests are reproducible.
    """

    def __init__(self):
        self._order_seq = itertools.count(1)
        self._token_seq = itertools.count(1)
        self._orders: dict[str, dict] = {}
        self._tokens: dict[str, list[dict]] = {}

    def create_order(self, amount: float, currency: str, merchant: str,
                     vault_token: str | None = None) -> dict:
        order_id = f"AG-MOCK-ORDER-{next(self._order_seq):04d}"
        order = {
            "id": order_id,
            "status": "CREATED",
            "intent": "CAPTURE",
            "purchase_units": [{
                "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
                "description": f"AgentGuard payment to {merchant}",
            }],
            "payment_source_token": vault_token,
            "mock": True,
        }
        self._orders[order_id] = order
        return order

    def capture_order(self, order_id: str) -> dict:
        order = self._orders[order_id]
        order["status"] = "COMPLETED"
        return {
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [{
                "payments": {"captures": [{
                    "id": f"AG-MOCK-CAPTURE-{order_id[-4:]}",
                    "status": "COMPLETED",
                    "amount": order["purchase_units"][0]["amount"],
                }]},
            }],
            "mock": True,
        }

    def create_payment_token(self, customer_id: str,
                             payment_source: dict) -> dict:
        token = {
            "id": f"AG-MOCK-PMT-{next(self._token_seq):04d}",
            "customer": {"id": customer_id},
            "payment_source": payment_source,
            "mock": True,
        }
        self._tokens.setdefault(customer_id, []).append(token)
        return token

    def list_payment_tokens(self, customer_id: str) -> dict:
        return {"payment_tokens": self._tokens.get(customer_id, []),
                "mock": True}


def get_client():
    """Mode from env: AGENTGUARD_MODE=live + credentials -> real Sandbox
    client; anything else -> mock (the safe default for a demo)."""
    mode = os.environ.get("AGENTGUARD_MODE", "mock").lower()
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "")
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET", "")
    if mode == "live" and client_id and client_secret:
        return PayPalClient(client_id, client_secret,
                            env=os.environ.get("PAYPAL_ENV", "sandbox"))
    return MockPayPalClient()
