# 🛡️ AgentGuard — Autonomous AI Agent Payment Proxy & Budget Controller

**Built for the PayPal AI Hackathon.** Giving an autonomous AI agent a credit
card is terrifying: one runaway loop and it can drain the account in minutes.
AgentGuard sits between the agent and the money. The agent asks to spend;
AgentGuard's policy engine decides; PayPal executes with tokenized Vault
credentials the agent never sees; every decision lands in an audit ledger.

## The problem

LLM agents increasingly need to *buy* things mid-task — API credits, compute,
datasets, subscriptions. Platform dashboards cap token spend, but nothing
governs an agent's **external** purchases across merchants and tools. Raw card
numbers in prompts/tool results are a security incident waiting to happen.

## The solution

```
                ┌──────────────┐   tool call    ┌───────────────────────┐
                │   AI Agent   │ ─────────────▶ │      AgentGuard       │
                │ (any LLM fw) │                │                       │
                └──────────────┘                │  1. Policy Engine     │
                                                │     budget cap · per- │
                                                │     tx cap · merchant │
                                                │     whitelist · rate  │
                                                │  2. AI risk review    │
                                                │     (Gemini / heur.)  │
                                                │  3. PayPal executor   │
                                                └──────────┬────────────┘
                                                           │ Vault token
                                                ┌──────────▼────────────┐
                                                │   PayPal Sandbox      │
                                                │   Orders API v2       │
                                                │   create → capture    │
                                                └──────────┬────────────┘
                                                           │ webhooks
                                                ┌──────────▼────────────┐
                                                │  Audit Ledger (JSONL) │
                                                │  + Dashboard          │
                                                └───────────────────────┘
```

1. **Policy Engine** (`agentguard/policy.py`) — pure, testable logic:
   total budget cap, per-transaction cap, merchant whitelist (exact +
   suffix match), and an hourly frequency limit that stops runaway loops.
2. **AI risk second opinion** (`agentguard/llm_evaluator.py`) — Gemini
   Flash reviews intent (optional, via `GEMINI_API_KEY`); without a key, a
   documented keyword heuristic flags scam-pattern requests. It can only
   *deny*, never override the policy engine, and it never crashes the flow.
3. **PayPal executor** (`agentguard/paypal_client.py`) — OAuth2
   client-credentials against the PayPal Sandbox, Orders API v2
   create → capture, and Vault API v3 payment-method tokens, so the agent
   pays with an opaque token instead of raw credentials.
4. **Audit + dashboard** (`agentguard/ledger.py`, `server.py`) — every
   approval, denial (with all reasons), capture, and PayPal webhook event
   (`PAYMENT.CAPTURE.COMPLETED`, `CHECKOUT.ORDER.APPROVED`) is recorded;
   `/dashboard` renders policy, spend totals, and the full audit table.

## PayPal APIs used

| API | Use |
|---|---|
| Developer Sandbox | Full lifecycle testing with sandbox accounts |
| Vault API v3 (`/v3/vault/payment-tokens`) | Tokenize the owner's payment method once; agents spend via token |
| Orders API v2 (`/v2/checkout/orders`) | Programmatic order create + capture per approved request |
| Webhooks | Capture/order events written to the audit ledger |
| OAuth2 (`/v1/oauth2/token`) | Client-credentials auth for all calls |

## Quickstart

### Mode 1 — Mock (default, zero credentials, offline)

```bash
pip install -r requirements.txt
python -m agentguard.demo        # or: ./run_demo.sh
```

Runs a scripted 9-attempt session: clean approvals with mock
create → capture, and one denial per guardrail (per-tx cap, whitelist,
risk flag, budget exhaustion, hourly frequency). Then:

```bash
python -m agentguard.server      # http://localhost:8080/dashboard
curl -X POST localhost:8080/agent/purchase -H 'Content-Type: application/json' \
  -d '{"agent_id":"demo","merchant":"api.openai.com","amount":10,"description":"API credits"}'
```

### Mode 2 — Live PayPal Sandbox

1. Create an app at <https://developer.paypal.com> and copy its sandbox
   Client ID / Secret.
2. Configure env (see `.env.example`):

```bash
export PAYPAL_CLIENT_ID=... PAYPAL_CLIENT_SECRET=...
export PAYPAL_ENV=sandbox AGENTGUARD_MODE=live
# optional AI risk review:
export GEMINI_API_KEY=...
python -m agentguard.server
```

Same endpoints — approvals now execute real Sandbox Orders API calls.

## Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

Covers every policy rule, the heuristic evaluator, ledger aggregates,
and the mock order → capture end-to-end flow.

## Project layout

```
agentguard/
  policy.py         policy dataclass + engine (pure logic)
  paypal_client.py  live Sandbox client + deterministic mock
  llm_evaluator.py  Gemini / heuristic risk second opinion
  ledger.py         append-only JSONL audit ledger + aggregates
  server.py         Flask: /agent/purchase, /dashboard, /webhooks/paypal, /health
  demo.py           scripted offline demo
tests/test_policy.py
```

## Honest demo notes

Mock mode is the default so anyone can run this with no accounts.
Webhook signature verification is noted in `server.py` as the required
production step. Budget/policy values are demo-sized defaults, tunable
via env (`AG_MAX_TOTAL_BUDGET`, `AG_MAX_PER_TX`, `AG_ALLOWED_MERCHANTS`,
`AG_MAX_PER_HOUR`).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
