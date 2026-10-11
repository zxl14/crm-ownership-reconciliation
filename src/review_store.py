"""Local review decisions. This module never calls the CRM."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISIONS_PATH = ROOT / "data" / "review" / "decisions.json"
DECISIONS = {"approve", "reject", "needs_investigation"}


def load_store(path: Path = DECISIONS_PATH) -> dict:
    if not path.exists():
        return {"decisions": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), dict):
        raise RuntimeError(f"Review decision file is unreadable: {path}")
    return payload


def decision_view(finding: dict, store: dict) -> dict:
    saved = store.get("decisions", {}).get(finding["finding_id"])
    if saved and saved.get("evidence_hash") == finding["evidence_hash"]:
        return {
            "decision": saved.get("decision", "undecided"),
            "decided_at": saved.get("decided_at", ""),
            "reviewer": saved.get("reviewer", ""),
            "note": saved.get("note", ""),
            "stale_decision": None,
        }
    stale = None
    if saved:
        stale = {
            "decision": saved.get("decision", ""),
            "decided_at": saved.get("decided_at", ""),
            "evidence_hash": saved.get("evidence_hash", ""),
        }
    return {
        "decision": "undecided",
        "decided_at": "",
        "reviewer": "",
        "note": "",
        "stale_decision": stale,
    }


def save_decision(
    finding: dict,
    decision: str,
    reviewer: str,
    note: str,
    path: Path = DECISIONS_PATH,
) -> None:
    if decision not in DECISIONS:
        raise RuntimeError(f"Unknown review decision: {decision}")
    if finding.get("executable"):
        raise RuntimeError("This phase cannot store an executable CRM change")
    store = load_store(path)
    current = store["decisions"].get(finding["finding_id"])
    history = list(current.get("history", [])) if current else []
    if current and current.get("evidence_hash") != finding["evidence_hash"]:
        history.append(
            {
                "evidence_hash": current.get("evidence_hash", ""),
                "decision": current.get("decision", ""),
                "reviewer": current.get("reviewer", ""),
                "note": current.get("note", ""),
                "decided_at": current.get("decided_at", ""),
            }
        )
    store["decisions"][finding["finding_id"]] = {
        "finding_id": finding["finding_id"],
        "evidence_hash": finding["evidence_hash"],
        "decision": decision,
        "reviewer": reviewer.strip() or "local reviewer",
        "note": note.strip(),
        "decided_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "crm_request_sent": False,
        "history": history,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(store, indent=2), encoding="utf-8")
    temporary.replace(path)
