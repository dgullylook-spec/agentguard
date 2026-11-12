"""Append-only JSONL audit ledger.

Every policy decision and every PayPal lifecycle event lands here.
Aggregates (total spent, transactions in the last hour) are computed
from the same entries the policy engine reads, so the audit trail and
the enforcement numbers can never drift apart.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Ledger:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._entries: list[dict] = []
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    self._entries.append(json.loads(line))

    def record(self, entry: dict) -> dict:
        """Append one entry (stamps UTC time if the caller didn't)."""
        entry = dict(entry)
        entry.setdefault("timestamp", _now().isoformat())
        with self._lock:
            self._entries.append(entry)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as fh:
                    fh.write(json.dumps(entry) + "\n")
        return entry

    def entries(self) -> list[dict]:
        with self._lock:
            return list(self._entries)

    # -- aggregates used by the policy engine -------------------------------
    # Only entries with status "CAPTURED" count as money actually spent;
    # approvals that failed capture and all denials are audit-only.

    def total_spent(self) -> float:
        with self._lock:
            return sum(
                float(e.get("amount", 0))
                for e in self._entries
                if e.get("status") == "CAPTURED"
            )

    def count_last_hour(self) -> int:
        cutoff = _now() - timedelta(hours=1)
        count = 0
        with self._lock:
            for e in self._entries:
                if e.get("status") != "CAPTURED":
                    continue
                ts = e.get("timestamp", "")
                try:
                    when = datetime.fromisoformat(ts)
                except ValueError:
                    continue
                if when >= cutoff:
                    count += 1
        return count
