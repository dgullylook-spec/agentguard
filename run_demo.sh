#!/usr/bin/env bash
# AgentGuard scripted demo — runs fully offline in mock mode.
set -e
cd "$(dirname "$0")"
python -m agentguard.demo
