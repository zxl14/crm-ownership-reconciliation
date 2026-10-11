"""Local checks for the disabled Owosso write-back. No CRM write is sent."""

from __future__ import annotations

import json
from pathlib import Path

from src.writeback import (
    RECOMMENDED_DUPLICATE,
    RECOMMENDED_SURVIVOR,
    apply_approved_change,
    build_change,
    intended_patch,
    save_executable_approval,
)

ROOT = Path(__file__).resolve().parents[1]


def _snapshot() -> dict:
    account = {
        "account_id": "",
        "name": "Bellhaven of Owosso",
        "parent_id": "0015QAPLGS3FVYEEEM",
        "parent_name": "Bellhaven Senior Living (Parent Account)",
        "billing_street": "1120 W Main St",
        "billing_city": "Owosso",
        "billing_state": "MI",
        "billing_zip": "48867",
        "care_type": "Assisted Living",
        "status": "Active",
        "phone": "(734) 503-1363",
        "lifetime_revenue": 0,
        "outstanding_ar": 0,
        "chow_current_account": "",
        "duplicate_of_account": "",
        "note": "",
        "updated_at": "2026-10-11 14:11:53Z",
    }
    survivor = {**account, "account_id": RECOMMENDED_SURVIVOR}
    duplicate = {
        **account,
        "account_id": RECOMMENDED_DUPLICATE,
        "billing_street": "1120 West Main Street",
        "phone": "(330) 383-5991",
    }
    return {
        "accounts": {RECOMMENDED_SURVIVOR: survivor, RECOMMENDED_DUPLICATE: duplicate},
        "contacts": {
            RECOMMENDED_SURVIVOR: [
                {
                    "contact_id": "003VEEW4P875R8G3U4",
                    "account_id": RECOMMENDED_SURVIVOR,
                    "name": "Gloria Lambert",
                    "title": "Administrator",
                    "email": "",
                    "phone": "(517) 226-4961",
                    "is_active": True,
                }
            ],
            RECOMMENDED_DUPLICATE: [
                {
                    "contact_id": "003BT8QV7PS7S0XM0P",
                    "account_id": RECOMMENDED_DUPLICATE,
                    "name": "Tricia Lindqvist",
                    "title": "Admissions Director",
                    "email": "tlindqvist@bellhavenliving.com",
                    "phone": "(330) 955-9503",
                    "is_active": True,
                }
            ],
        },
    }


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeSession:
    def __init__(self) -> None:
        self.patches = []

    def patch(self, url, json=None, timeout=None):
        self.patches.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResponse(200)


def main() -> None:
    snapshot = _snapshot()
    change = build_change(snapshot, RECOMMENDED_SURVIVOR, RECOMMENDED_DUPLICATE)
    assert change["patch"] == {"status": "Inactive", "duplicate_of_account": RECOMMENDED_SURVIVOR}
    assert change["preview"]["request_will_be_sent"] is False
    assert change["preview"]["openapi"]["request_body_schema"] is None
    assert change["preview"]["survivor_method"] is None
    assert set(change["patch"]) == {"status", "duplicate_of_account"}
    try:
        build_change(snapshot, RECOMMENDED_SURVIVOR, "001NOTOWOSSOACCOUNT")
    except RuntimeError:
        pass
    else:
        raise AssertionError("non-Owosso pair was accepted")

    audit = ROOT / "data" / "audit" / "check-writeback.jsonl"
    approvals = ROOT / "data" / "review" / "check-executable-approvals.json"
    if audit.exists():
        audit.unlink()
    finding_decision = {"decision": "approve", "finding_id": "abc", "evidence_hash": "def"}
    blocked_finding = apply_approved_change(
        finding_decision,
        lambda: snapshot,
        writes_enabled_flag=True,
        session=_FakeSession(),
        audit_path=audit,
    )
    assert blocked_finding["result"] == "refused"
    assert blocked_finding["request_sent"] is False

    saved = save_executable_approval(change, "check", "dry run", approvals)
    assert saved["finding_decision_authorizes_write"] is False
    session = _FakeSession()
    blocked = apply_approved_change(
        saved,
        lambda: snapshot,
        writes_enabled_flag=False,
        session=session,
        audit_path=audit,
    )
    assert blocked["result"] == "blocked"
    assert blocked["request_sent"] is False
    assert session.patches == []

    drifted = json.loads(json.dumps(snapshot))
    drifted["accounts"][RECOMMENDED_DUPLICATE]["phone"] = "(000) 000-0000"
    refused = apply_approved_change(
        saved,
        lambda: drifted,
        writes_enabled_flag=True,
        session=session,
        audit_path=audit,
    )
    assert refused["result"] == "refused"
    assert session.patches == []

    applied_state = json.loads(json.dumps(snapshot))
    applied_state["accounts"][RECOMMENDED_DUPLICATE]["status"] = "Inactive"
    applied_state["accounts"][RECOMMENDED_DUPLICATE]["duplicate_of_account"] = RECOMMENDED_SURVIVOR
    skipped = apply_approved_change(
        saved,
        lambda: applied_state,
        writes_enabled_flag=True,
        session=session,
        audit_path=audit,
    )
    assert skipped["result"] == "already_applied"
    assert session.patches == []

    calls = {"reads": 0}

    def loader():
        calls["reads"] += 1
        if calls["reads"] == 1:
            return snapshot
        verified = json.loads(json.dumps(snapshot))
        verified["accounts"][RECOMMENDED_DUPLICATE]["status"] = "Inactive"
        verified["accounts"][RECOMMENDED_DUPLICATE]["duplicate_of_account"] = RECOMMENDED_SURVIVOR
        return verified

    live = _FakeSession()
    result = apply_approved_change(
        saved,
        loader,
        writes_enabled_flag=True,
        session=live,
        audit_path=audit,
    )
    assert result["result"] == "applied"
    assert len(live.patches) == 1
    assert live.patches[0]["json"] == intended_patch(RECOMMENDED_SURVIVOR)
    assert live.patches[0]["url"].endswith("/api/v1/accounts/" + RECOMMENDED_DUPLICATE)
    assert calls["reads"] == 2
    print("change_id=" + change["change_id"])
    print("preview_path=" + change["preview"]["path"])
    print("preview_body=" + json.dumps(change["patch"], sort_keys=True))
    print("checks=ok")


if __name__ == "__main__":
    main()
