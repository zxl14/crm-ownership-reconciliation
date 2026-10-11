"""Local checks for generalized proposals and the financial rule.

These checks use in-memory accounts and a fake HTTP session. They do not call
the CRM.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.findings import NAME_DISCREPANCY, build_review_model
from src.operations import (
    FINANCIAL_FIELDS,
    apply_steps,
    approval_for,
    create_facility_plan,
    name_plan,
    ownership_plan,
    status_plan,
)
from src.ownership import (
    BELLHAVEN_PARENT_ID,
    FINDLAY_ACCOUNT,
    build_findlay_change,
    findlay_proposal,
)
from src.proposals import assess_finding
from src.writeback import apply_findlay_change, build_change, save_executable_approval

ROOT = Path(__file__).resolve().parents[1]
OWOSSO_IDS = {"001EGU7BMJ942ZTRE6", "001QU150PM4Z15UA71"}
OLD_PARENT = "001OLDPARENT0001"
NEW_PARENT = "001NEWPARENT0001"


def _account(**overrides) -> dict:
    base = {
        "account_id": "001FACILITY00001",
        "name": "Example Care",
        "parent_id": OLD_PARENT,
        "parent_name": "Old Parent",
        "billing_street": "10 Main St",
        "billing_city": "Owosso",
        "billing_state": "MI",
        "billing_zip": "48867",
        "care_type": "Assisted Living",
        "phone": "(734) 555-0100",
        "status": "Active",
        "lifetime_revenue": 0,
        "outstanding_ar": 0,
        "chow_current_account": "",
        "duplicate_of_account": "",
        "note": "",
        "updated_at": "2026-10-11 14:11:53Z",
    }
    base.update(overrides)
    return base


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _World:
    def __init__(self, account: dict) -> None:
        self.accounts = {account["account_id"]: dict(account)}
        self.sent: list[tuple[str, str, dict]] = []
        self.fail_next_patch = False

    def post(self, url, json=None, timeout=None):
        new_id = "001CREATED0000001"
        self.accounts[new_id] = {
            "account_id": new_id,
            "name": json["name"],
            "parent_id": json["parent_id"],
            "status": json.get("status", "Active"),
            "lifetime_revenue": 0,
            "outstanding_ar": 0,
            "chow_current_account": "",
            "duplicate_of_account": "",
        }
        self.sent.append(("POST", url, json))
        return _Response(201, {"account_id": new_id})

    def patch(self, url, json=None, timeout=None):
        if self.fail_next_patch:
            self.fail_next_patch = False
            self.sent.append(("PATCH-FAIL", url, json))
            return _Response(500, {})
        account_id = url.rstrip("/").rsplit("/", 1)[-1]
        self.accounts[account_id].update(json)
        self.sent.append(("PATCH", url, json))
        return _Response(200, dict(self.accounts[account_id]))

    def snapshot(self) -> dict:
        return {"accounts": {key: dict(value) for key, value in self.accounts.items()}}


class _Boom:
    def post(self, url, json=None, timeout=None):
        raise AssertionError("POST was called")

    def patch(self, url, json=None, timeout=None):
        raise AssertionError("PATCH was called")


def _audit(name: str) -> Path:
    path = ROOT / "data" / "audit" / f"check-phase4-{name}.jsonl"
    if path.exists():
        path.unlink()
    return path


def _run(plan: dict, account: dict, world: _World, path: Path, **kwargs) -> dict:
    approval = approval_for(plan, account)
    return apply_steps(
        approval,
        world,
        world.snapshot,
        writes_enabled_flag=kwargs.get("writes_enabled_flag", True),
        allow_unverified=kwargs.get("allow_unverified", True),
        audit_path=path,
    )


def _assert_no_financial_bodies(plan: dict) -> None:
    for step in plan["steps"]:
        assert FINANCIAL_FIELDS.isdisjoint(step["body"])


def _check_plan_selection() -> None:
    held = _account(lifetime_revenue=120000, outstanding_ar=4800)
    revenue_only = _account(lifetime_revenue=5000, outstanding_ar=0)
    neither = _account(lifetime_revenue=0, outstanding_ar=0)
    receivables_only = _account(lifetime_revenue=0, outstanding_ar=100)
    chow = ownership_plan(held, NEW_PARENT, verified=True)
    direct = ownership_plan(revenue_only, NEW_PARENT, verified=True)
    assert chow["operation"] == "change_of_ownership"
    assert direct["operation"] == "reparent"
    assert ownership_plan(neither, NEW_PARENT, verified=True)["operation"] == "reparent"
    assert ownership_plan(receivables_only, NEW_PARENT, verified=True)["operation"] == "reparent"
    assert ownership_plan(held, NEW_PARENT, verified=False)["actionable"] is False
    assert ownership_plan(held, "", verified=True)["actionable"] is False
    _assert_no_financial_bodies(chow)
    _assert_no_financial_bodies(direct)
    assert chow["steps"][0]["method"] == "POST"
    assert set(chow["steps"][1]["body"]) == {"chow_current_account"}
    assert "parent_id" not in chow["steps"][1]["body"]
    assert direct["steps"] == [
        {"method": "PATCH", "path": "/api/v1/accounts/" + revenue_only["account_id"], "body": {"parent_id": NEW_PARENT}}
    ]
    assert name_plan(held, "Example Care Center", address_matches=True, phone_matches=False)["actionable"] is False
    verified_name = name_plan(held, "Example Care Center", address_matches=True, phone_matches=True)
    assert verified_name["operation"] == "update_name"
    assert verified_name["live_send"] == "blocked_schema"
    assert verified_name["steps"][0]["body"] == {"name": "Example Care Center"}
    assert create_facility_plan(
        name="New Facility",
        parent_id="",
        street="1 Main St",
        city="Findlay",
        state="OH",
        zip_code="45840",
        verified_parent=False,
    )["actionable"] is False
    created = create_facility_plan(
        name="New Facility",
        parent_id=NEW_PARENT,
        street="1 Main St",
        city="Findlay",
        state="OH",
        zip_code="45840",
        verified_parent=True,
    )
    assert created["live_send"] == "blocked_schema"
    _assert_no_financial_bodies(created)
    assert status_plan(listed_on_website=True)["actionable"] is False
    assert status_plan(listed_on_website=False)["actionable"] is False


def _check_change_of_ownership() -> None:
    account = _account(lifetime_revenue=120000, outstanding_ar=4800)
    world = _World(account)
    path = _audit("chow")
    plan = ownership_plan(account, NEW_PARENT, verified=True)
    first = _run(plan, account, world, path)
    assert first["result"] == "applied"
    assert first["request_sent"] is True
    old = world.accounts[account["account_id"]]
    created = world.accounts["001CREATED0000001"]
    assert old["parent_id"] == OLD_PARENT
    assert old["lifetime_revenue"] == 120000
    assert old["outstanding_ar"] == 4800
    assert old["chow_current_account"] == "001CREATED0000001"
    assert created["parent_id"] == NEW_PARENT
    assert created["lifetime_revenue"] == 0
    assert created["outstanding_ar"] == 0
    assert [item[0] for item in world.sent] == ["POST", "PATCH"]
    assert FINANCIAL_FIELDS.isdisjoint(world.sent[0][2])
    assert set(world.sent[1][2]) == {"chow_current_account"}
    again = _run(plan, account, world, path)
    assert again["result"] == "already_applied"
    assert again["request_sent"] is False
    assert [item[0] for item in world.sent] == ["POST", "PATCH"]


def _check_partial_then_rerun() -> None:
    account = _account(lifetime_revenue=84000, outstanding_ar=12400)
    world = _World(account)
    world.fail_next_patch = True
    path = _audit("partial")
    plan = ownership_plan(account, NEW_PARENT, verified=True)
    approval = approval_for(plan, account)
    partial = apply_steps(approval, world, world.snapshot, writes_enabled_flag=True, allow_unverified=True, audit_path=path)
    assert partial["result"] == "partial"
    assert partial["created_account_id"] == "001CREATED0000001"
    assert [item[0] for item in world.sent] == ["POST", "PATCH-FAIL"]
    assert world.accounts[account["account_id"]]["parent_id"] == OLD_PARENT
    assert world.accounts[account["account_id"]]["chow_current_account"] == ""
    resumed = apply_steps(
        approval_for(plan, account),
        world,
        world.snapshot,
        writes_enabled_flag=True,
        allow_unverified=True,
        audit_path=path,
    )
    assert resumed["result"] == "applied"
    assert [item[0] for item in world.sent] == ["POST", "PATCH-FAIL", "PATCH"]
    assert world.accounts[account["account_id"]]["chow_current_account"] == "001CREATED0000001"
    assert world.accounts[account["account_id"]]["lifetime_revenue"] == 84000
    third = apply_steps(
        approval_for(plan, account),
        world,
        world.snapshot,
        writes_enabled_flag=True,
        allow_unverified=True,
        audit_path=path,
    )
    assert third["result"] == "already_applied"
    assert third["request_sent"] is False
    assert [item[0] for item in world.sent] == ["POST", "PATCH-FAIL", "PATCH"]


def _check_direct_reparent() -> None:
    account = _account(lifetime_revenue=5000, outstanding_ar=0)
    world = _World(account)
    path = _audit("reparent")
    plan = ownership_plan(account, NEW_PARENT, verified=True)
    first = _run(plan, account, world, path)
    assert first["result"] == "applied"
    assert [item[0] for item in world.sent] == ["PATCH"]
    assert world.sent[0][2] == {"parent_id": NEW_PARENT}
    updated = world.accounts[account["account_id"]]
    assert updated["parent_id"] == NEW_PARENT
    assert updated["lifetime_revenue"] == 5000
    assert updated["outstanding_ar"] == 0
    assert updated["chow_current_account"] == ""
    second = _run(plan, account, world, path)
    assert second["result"] == "already_applied"
    assert second["request_sent"] is False
    assert [item[0] for item in world.sent] == ["PATCH"]


def _check_refusals() -> None:
    account = _account(lifetime_revenue=120000, outstanding_ar=4800)
    world = _World(account)
    path = _audit("refusals")
    plan = ownership_plan(account, NEW_PARENT, verified=True)
    disabled = _run(plan, account, world, path, writes_enabled_flag=False)
    assert disabled["result"] == "blocked"
    assert disabled["request_sent"] is False
    assert world.sent == []
    unpublished = apply_steps(
        approval_for(plan, account),
        _Boom(),
        world.snapshot,
        writes_enabled_flag=True,
        allow_unverified=False,
        audit_path=path,
    )
    assert unpublished["result"] == "refused"
    assert unpublished["request_sent"] is False
    finding = apply_steps(
        {"decision": "approve", "finding_id": "abc"},
        _Boom(),
        world.snapshot,
        writes_enabled_flag=True,
        allow_unverified=True,
        audit_path=path,
    )
    assert finding["result"] == "refused"
    assert finding["request_sent"] is False
    unsafe = approval_for(plan, account)
    unsafe["steps"][0]["body"]["lifetime_revenue"] = 1
    rejected = apply_steps(unsafe, _Boom(), world.snapshot, writes_enabled_flag=True, allow_unverified=True, audit_path=path)
    assert rejected["result"] == "refused"
    assert "financial" in rejected["reason"].lower()
    drifted = _account(lifetime_revenue=1, outstanding_ar=4800)
    drift_world = _World(drifted)
    refused = apply_steps(
        approval_for(plan, account),
        drift_world,
        drift_world.snapshot,
        writes_enabled_flag=True,
        allow_unverified=True,
        audit_path=_audit("drift"),
    )
    assert refused["result"] == "refused"
    assert drift_world.sent == []


def _check_live_findings_stay_conservative() -> None:
    findings = build_review_model()["findings"]
    actionable = [finding for finding in findings if assess_finding(finding)["actionable"]]
    assert actionable == []
    assert not any(assess_finding(finding)["kind"] == "reparent" for finding in findings)
    owosso = next(
        finding
        for finding in findings
        if {account["account_id"] for account in finding["crm_accounts"]} == OWOSSO_IDS
    )
    applied = assess_finding(owosso)
    assert applied["kind"] == "already_applied"
    assert applied["actionable"] is False
    verified_name = assess_finding(
        {
            "categories": [NAME_DISCREPANCY],
            "website": {"name": "Example Care Center", "phone": "(734) 555-0100"},
            "crm_accounts": [_account(name="Example Care")],
        }
    )
    assert verified_name["kind"] == "update_name"
    assert verified_name["live_send"] == "blocked_schema"
    assert verified_name["actionable"] is False
    assert verified_name["steps"][0]["body"] == {"name": "Example Care Center"}


def _check_findlay_writeback() -> None:
    from src.ownership import BELLHAVEN_PARENT_NAME

    account = _account(
        account_id=FINDLAY_ACCOUNT,
        name="Bellhaven Meadows of Findlay",
        parent_id="",
        lifetime_revenue=22000,
        outstanding_ar=0,
        street="1800 N Blanchard St",
        city="Findlay",
        state="OH",
        zip="45840",
        status="Active",
    )
    website = {"full_address": "1800 N Blanchard St, Findlay, OH 45840"}
    welcome = (
        "New this year: we're delighted to welcome Bellhaven Meadows of Findlay "
        "to the Bellhaven family, expanding our services in Findlay, OH."
    )
    proposal = findlay_proposal(account, website, welcome)
    assert proposal is not None
    assert proposal["patch"] == {"parent_id": BELLHAVEN_PARENT_ID}
    assert proposal["lifetime_revenue"] == 22000
    assert proposal["outstanding_ar"] == 0
    assert "Inference" in proposal["inference"]
    assert findlay_proposal(account, website, "") is None
    held = {**account, "lifetime_revenue": 22000, "outstanding_ar": 100, "financial_hold": True}
    assert findlay_proposal(held, website, welcome) is None
    change = build_findlay_change(account, proposal)
    assert change["preview"]["request_will_be_sent"] is False
    assert change["preview"]["body"] == {"parent_id": BELLHAVEN_PARENT_ID}
    path = _audit("findlay")
    approvals = ROOT / "data" / "review" / "check-findlay-approvals.json"
    if approvals.exists():
        approvals.unlink()

    facility = dict(account)
    parent = {
        "account_id": BELLHAVEN_PARENT_ID,
        "name": BELLHAVEN_PARENT_NAME,
        "parent_id": "",
        "status": "Active",
        "lifetime_revenue": 0,
        "outstanding_ar": 0,
    }

    class _FindlaySession:
        def __init__(self) -> None:
            self.patches = []
            self.status_code = 200

        def patch(self, url, json=None, timeout=None):
            self.patches.append({"url": url, "json": json, "timeout": timeout})
            if self.status_code == 200:
                facility["parent_id"] = json["parent_id"]
            return _Response(self.status_code, {})

    reads = {"count": 0}

    def loader():
        reads["count"] += 1
        return {
            "accounts": {
                FINDLAY_ACCOUNT: dict(facility),
                BELLHAVEN_PARENT_ID: dict(parent),
            }
        }

    def boom_loader():
        raise AssertionError("loader")

    refused = apply_findlay_change(
        {"decision": "approve"},
        boom_loader,
        writes_enabled_flag=True,
        session=_Boom(),
        audit_path=path,
    )
    assert refused["result"] == "refused"
    assert refused["request_sent"] is False
    saved = save_executable_approval(change, "check", "findlay dry run", approvals)
    assert saved["finding_decision_authorizes_write"] is False
    assert saved["crm_request_sent"] is False
    blocked = apply_findlay_change(
        saved,
        boom_loader,
        writes_enabled_flag=False,
        session=_Boom(),
        audit_path=path,
    )
    assert blocked["result"] == "blocked"
    assert blocked["request_sent"] is False

    drifted = dict(facility)
    drifted["lifetime_revenue"] = 1

    def drifted_loader():
        return {"accounts": {FINDLAY_ACCOUNT: drifted, BELLHAVEN_PARENT_ID: dict(parent)}}

    drift = apply_findlay_change(saved, drifted_loader, writes_enabled_flag=True, session=_Boom(), audit_path=path)
    assert drift["result"] == "refused"
    assert drift["request_sent"] is False

    bad_parent = dict(parent)
    bad_parent["name"] = "Other Parent"

    def bad_parent_loader():
        return {"accounts": {FINDLAY_ACCOUNT: dict(facility), BELLHAVEN_PARENT_ID: bad_parent}}

    parent_refusal = apply_findlay_change(
        saved,
        bad_parent_loader,
        writes_enabled_flag=True,
        session=_Boom(),
        audit_path=path,
    )
    assert parent_refusal["result"] == "refused"
    assert parent_refusal["request_sent"] is False

    session = _FindlaySession()
    session.status_code = 422
    failed = apply_findlay_change(saved, loader, writes_enabled_flag=True, session=session, audit_path=path)
    assert failed["result"] == "error"
    assert failed["request_sent"] is True
    assert len(session.patches) == 1
    assert session.patches[0]["json"] == {"parent_id": BELLHAVEN_PARENT_ID}
    assert facility["parent_id"] == ""
    assert facility["lifetime_revenue"] == 22000

    session.status_code = 200
    applied = apply_findlay_change(saved, loader, writes_enabled_flag=True, session=session, audit_path=path)
    assert applied["result"] == "applied"
    assert applied["verified_parent_id"] == BELLHAVEN_PARENT_ID
    assert applied["verified_lifetime_revenue"] == 22000
    assert applied["verified_outstanding_ar"] == 0
    assert applied["verified_status"] == "Active"
    assert len(session.patches) == 2
    assert session.patches[1]["url"].endswith("/api/v1/accounts/" + FINDLAY_ACCOUNT)
    assert facility["lifetime_revenue"] == 22000
    assert facility["outstanding_ar"] == 0
    again = apply_findlay_change(saved, loader, writes_enabled_flag=True, session=session, audit_path=path)
    assert again["result"] == "already_applied"
    assert again["request_sent"] is False
    assert len(session.patches) == 2
    approvals.unlink()


def _check_duplicate_plan_is_not_owosso_only() -> None:
    account = _account()
    left = {**account, "account_id": "001LEFTACCOUNT001"}
    right = {**account, "account_id": "001RIGHTACCOUNT01", "billing_street": "10 West Main Street"}
    contact = {
        "contact_id": "003CONTACT0000001",
        "account_id": "",
        "name": "Alex Admin",
        "title": "Administrator",
        "email": "",
        "phone": "",
        "is_active": True,
    }
    snapshot = {
        "accounts": {left["account_id"]: left, right["account_id"]: right},
        "contacts": {
            left["account_id"]: [{**contact, "account_id": left["account_id"]}],
            right["account_id"]: [{**contact, "contact_id": "003CONTACT0000002", "account_id": right["account_id"]}],
        },
    }
    change = build_change(snapshot, left["account_id"], right["account_id"])
    assert change["operation"] == "mark_duplicate"
    assert change["patch"] == {"status": "Inactive", "duplicate_of_account": left["account_id"]}
    assert change["preview"]["request_will_be_sent"] is False
    assert FINANCIAL_FIELDS.isdisjoint(change["patch"])


def _cleanup() -> None:
    folder = ROOT / "data" / "audit"
    for path in folder.glob("check-phase4-*.jsonl"):
        path.unlink()
    approvals = ROOT / "data" / "review" / "check-findlay-approvals.json"
    if approvals.exists():
        approvals.unlink()


def main() -> None:
    if os.environ.get("CRM_WRITES_ENABLED", "").strip().lower() == "true":
        raise RuntimeError("CRM_WRITES_ENABLED is set. Phase 4 checks refuse to run while writes are enabled.")
    _check_plan_selection()
    _check_change_of_ownership()
    _check_partial_then_rerun()
    _check_direct_reparent()
    _check_refusals()
    _check_live_findings_stay_conservative()
    _check_findlay_writeback()
    _check_duplicate_plan_is_not_owosso_only()
    _cleanup()
    print("checks=ok")


if __name__ == "__main__":
    main()
