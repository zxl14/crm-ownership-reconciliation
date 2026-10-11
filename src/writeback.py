"""Dry-run and gated write-back for a confirmed duplicate pair.

OpenAPI publishes PATCH /api/v1/accounts/{account_id} but does not publish a
request body. The only body this module sends is `status` and
`duplicate_of_account`. Those names are on the GET account resource, and the
Owosso update confirmed that this body is accepted. No other field is sent
from here.

The Owosso pair still has a survivor recommendation. Any other pair must be
present in the snapshot and designated by a person. The Findlay parent update
can be saved separately. When CRM_WRITES_ENABLED is exactly true, the only
Findlay body sent is {"parent_id": "0015QAPLGS3FVYEEEM"}. This module does not
turn that flag on. A saved finding decision is not an executable approval.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPROVALS_PATH = ROOT / "data" / "review" / "executable_approvals.json"
AUDIT_PATH = ROOT / "data" / "audit" / "writeback.jsonl"
OWOSSO_IDS = frozenset({"001EGU7BMJ942ZTRE6", "001QU150PM4Z15UA71"})
RECOMMENDED_SURVIVOR = "001EGU7BMJ942ZTRE6"
RECOMMENDED_DUPLICATE = "001QU150PM4Z15UA71"
PATCH_PATH = "/api/v1/accounts/{account_id}"
ACCOUNT_FIELDS = (
    "account_id",
    "name",
    "parent_id",
    "parent_name",
    "billing_street",
    "billing_city",
    "billing_state",
    "billing_zip",
    "care_type",
    "status",
    "phone",
    "lifetime_revenue",
    "outstanding_ar",
    "chow_current_account",
    "duplicate_of_account",
    "note",
    "updated_at",
)
CONTACT_FIELDS = ("contact_id", "account_id", "name", "title", "email", "phone", "is_active")

RECOMMENDATION = (
    "Recommended survivor: 001EGU7BMJ942ZTRE6. Its street is 1120 W Main St, "
    "its phone is (734) 503-1363, and its active contact is Gloria Lambert, Administrator. "
    "Those three values match the Owosso website. "
    "001QU150PM4Z15UA71 uses the expanded street 1120 West Main Street, a different phone, "
    "and a different active contact, Tricia Lindqvist, Admissions Director. "
    "Both accounts have the same parent, name, care type, and zero revenue and receivables. "
    "The records are duplicate facility accounts, not two different communities. "
    "Tricia Lindqvist would remain linked to the inactive account; this change does not move contacts. "
    "The recommendation is not selected automatically and does not approve a write."
)


def writes_enabled() -> bool:
    return os.environ.get("CRM_WRITES_ENABLED", "").strip().lower() == "true"


def _digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _require_pair(survivor_id: str, duplicate_id: str) -> None:
    if not survivor_id or not duplicate_id or survivor_id == duplicate_id:
        raise RuntimeError("A duplicate change needs two different account IDs")


def _account_slice(account: dict) -> dict:
    missing = [field for field in ACCOUNT_FIELDS if field not in account]
    if missing:
        raise RuntimeError(f"Account {account.get('account_id', '')} is missing {', '.join(missing)}")
    return {field: account[field] for field in ACCOUNT_FIELDS}


def _contact_slice(contact: dict) -> dict:
    missing = [field for field in CONTACT_FIELDS if field not in contact]
    if missing:
        raise RuntimeError(f"Contact {contact.get('contact_id', '')} is missing {', '.join(missing)}")
    return {field: contact[field] for field in CONTACT_FIELDS}


def relevant_state(snapshot: dict, survivor_id: str, duplicate_id: str) -> dict:
    _require_pair(survivor_id, duplicate_id)
    accounts = snapshot.get("accounts") if isinstance(snapshot, dict) else None
    contacts = snapshot.get("contacts") if isinstance(snapshot, dict) else None
    if not isinstance(accounts, dict) or survivor_id not in accounts or duplicate_id not in accounts:
        raise RuntimeError("Both accounts must be present in the snapshot")
    if not isinstance(contacts, dict) or survivor_id not in contacts or duplicate_id not in contacts:
        raise RuntimeError("Both accounts must have a contact list in the snapshot")
    state = {}
    for account_id in (survivor_id, duplicate_id):
        contacts = snapshot["contacts"][account_id]
        state[account_id] = {
            "account": _account_slice(snapshot["accounts"][account_id]),
            "contacts": sorted((_contact_slice(contact) for contact in contacts), key=lambda item: item["contact_id"]),
        }
    return state


def state_hash(snapshot: dict, survivor_id: str, duplicate_id: str) -> str:
    return _digest(relevant_state(snapshot, survivor_id, duplicate_id))


def intended_patch(survivor_id: str) -> dict:
    return {"status": "Inactive", "duplicate_of_account": survivor_id}


def build_change(snapshot: dict, survivor_id: str, duplicate_id: str) -> dict:
    _require_pair(survivor_id, duplicate_id)
    current = relevant_state(snapshot, survivor_id, duplicate_id)
    digest = _digest(current)
    patch = intended_patch(survivor_id)
    duplicate = current[duplicate_id]["account"]
    survivor = current[survivor_id]["account"]
    return {
        "change_id": _digest(
            {
                "operation": "mark_duplicate",
                "survivor_account_id": survivor_id,
                "duplicate_account_id": duplicate_id,
                "state_hash": digest,
                "patch": patch,
            }
        ),
        "operation": "mark_duplicate",
        "survivor_account_id": survivor_id,
        "duplicate_account_id": duplicate_id,
        "state_hash": digest,
        "patch": patch,
        "matches_recommendation": (
            survivor_id == RECOMMENDED_SURVIVOR
            if {survivor_id, duplicate_id} == OWOSSO_IDS
            else True
        ),
        "before": {
            "status": duplicate["status"],
            "duplicate_of_account": duplicate["duplicate_of_account"],
            "lifetime_revenue": duplicate["lifetime_revenue"],
            "outstanding_ar": duplicate["outstanding_ar"],
            "parent_id": duplicate["parent_id"],
        },
        "survivor_before": {
            "status": survivor["status"],
            "duplicate_of_account": survivor["duplicate_of_account"],
            "parent_id": survivor["parent_id"],
        },
        "preview": {
            "writes_enabled": writes_enabled(),
            "request_will_be_sent": False,
            "openapi": {
                "method": "PATCH",
                "path": PATCH_PATH,
                "request_body_schema": None,
                "documented_responses": [200, 422],
            },
            "method": "PATCH",
            "path": "/api/v1/accounts/" + duplicate_id,
            "body": patch,
            "body_fields": sorted(patch),
            "body_basis": (
                "status and duplicate_of_account are fields on the GET account resource "
                "and are the duplicate fields named by the assessment. "
                "OpenAPI does not publish a PATCH body. No test write was sent."
            ),
            "survivor_method": None,
            "contact_methods": [],
            "parent_id_changed": False,
        },
    }


def load_approvals(path: Path = APPROVALS_PATH) -> dict:
    if not path.exists():
        return {"approvals": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("approvals"), dict):
        raise RuntimeError("Executable approval file is unreadable")
    return payload


def _store_approval(record: dict, path: Path) -> dict:
    store = load_approvals(path)
    store["approvals"][record["change_id"]] = record
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(store, indent=2), encoding="utf-8")
    temporary.replace(path)
    return record


def save_executable_approval(
    change: dict,
    reviewer: str,
    note: str,
    path: Path = APPROVALS_PATH,
) -> dict:
    operation = change.get("operation")
    if operation == "reparent":
        return _save_findlay_approval(change, reviewer, note, path)
    if operation != "mark_duplicate":
        raise RuntimeError("Only an explicit duplicate change or the Findlay parent update can be approved")
    _require_pair(change["survivor_account_id"], change["duplicate_account_id"])
    record = {
        "change_id": change["change_id"],
        "operation": change["operation"],
        "survivor_account_id": change["survivor_account_id"],
        "duplicate_account_id": change["duplicate_account_id"],
        "state_hash": change["state_hash"],
        "patch": change["patch"],
        "before": change["before"],
        "reviewer": reviewer.strip() or "local reviewer",
        "note": note.strip(),
        "approved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "finding_decision_authorizes_write": False,
        "crm_request_sent": False,
    }
    return _store_approval(record, path)


def _save_findlay_approval(change: dict, reviewer: str, note: str, path: Path) -> dict:
    from src.ownership import BELLHAVEN_PARENT_ID, FINDLAY_ACCOUNT

    if change.get("account_id") != FINDLAY_ACCOUNT:
        raise RuntimeError("The only parent update that can be approved is the Findlay account")
    if change.get("patch") != {"parent_id": BELLHAVEN_PARENT_ID}:
        raise RuntimeError("The approved body is not the Findlay parent update")
    required = ("change_id", "state_hash", "before", "website_evidence", "inference")
    if any(field not in change for field in required):
        raise RuntimeError("The Findlay approval is missing the exact change")
    record = {
        "change_id": change["change_id"],
        "operation": "reparent",
        "account_id": FINDLAY_ACCOUNT,
        "proposed_parent_id": BELLHAVEN_PARENT_ID,
        "state_hash": change["state_hash"],
        "patch": {"parent_id": BELLHAVEN_PARENT_ID},
        "before": change["before"],
        "website_evidence": change["website_evidence"],
        "inference": change["inference"],
        "reviewer": reviewer.strip() or "local reviewer",
        "note": note.strip(),
        "approved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "finding_decision_authorizes_write": False,
        "crm_request_sent": False,
    }
    return _store_approval(record, path)


def _audit(entry: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _already_applied(account: dict, survivor_id: str) -> bool:
    return account.get("status") == "Inactive" and account.get("duplicate_of_account") == survivor_id


def apply_approved_change(
    approval: dict,
    snapshot_loader,
    *,
    writes_enabled_flag: bool | None = None,
    session=None,
    audit_path: Path = AUDIT_PATH,
) -> dict:
    """Apply one approved duplicate change, or refuse before any write.

    snapshot_loader() must perform reads only. session.patch is reached only
    when write-back is enabled, the approval matches the current CRM state,
    and the duplicate is not already marked.
    """
    enabled = writes_enabled() if writes_enabled_flag is None else writes_enabled_flag
    entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "change_id": approval.get("change_id", ""),
        "request_sent": False,
        "crm_request_sent": False,
    }
    required = ("change_id", "survivor_account_id", "duplicate_account_id", "state_hash", "patch")
    if any(field not in approval for field in required):
        entry["result"] = "refused"
        entry["reason"] = "A finding decision is not an executable CRM approval"
        _audit(entry, audit_path)
        return entry
    try:
        _require_pair(approval["survivor_account_id"], approval["duplicate_account_id"])
    except RuntimeError as exc:
        entry["result"] = "refused"
        entry["reason"] = str(exc)
        _audit(entry, audit_path)
        return entry
    if approval.get("patch") != intended_patch(approval["survivor_account_id"]):
        entry["result"] = "refused"
        entry["reason"] = "The approved body is not the duplicate status change"
        _audit(entry, audit_path)
        return entry
    if not enabled:
        entry["result"] = "blocked"
        entry["reason"] = "CRM write-back is disabled"
        _audit(entry, audit_path)
        return entry

    fresh = snapshot_loader()
    duplicate_id = approval["duplicate_account_id"]
    survivor_id = approval["survivor_account_id"]
    accounts = fresh.get("accounts") if isinstance(fresh, dict) else None
    if not isinstance(accounts, dict) or duplicate_id not in accounts or survivor_id not in accounts:
        entry["result"] = "refused"
        entry["reason"] = "The current snapshot does not contain both accounts"
        _audit(entry, audit_path)
        return entry
    duplicate = accounts[duplicate_id]
    if _already_applied(duplicate, survivor_id):
        entry["result"] = "already_applied"
        before = approval.get("before") or {}
        comparable = json.loads(json.dumps(fresh))
        comparable["accounts"][duplicate_id]["status"] = before.get("status", duplicate["status"])
        comparable["accounts"][duplicate_id]["duplicate_of_account"] = before.get(
            "duplicate_of_account", duplicate["duplicate_of_account"]
        )
        if "status" in before and state_hash(comparable, survivor_id, duplicate_id) != approval["state_hash"]:
            entry["reason"] = "The duplicate was already marked. Other CRM fields differ, and no second request was sent."
        else:
            entry["reason"] = "The duplicate account already points at the survivor and is Inactive"
        _audit(entry, audit_path)
        return entry
    current_hash = state_hash(fresh, approval["survivor_account_id"], approval["duplicate_account_id"])
    if current_hash != approval["state_hash"]:
        entry["result"] = "refused"
        entry["reason"] = "CRM state changed since this exact change was approved"
        entry["current_state_hash"] = current_hash
        _audit(entry, audit_path)
        return entry
    if session is None:
        from src.crm_read import authenticated_session

        session = authenticated_session()
    response = session.patch(
        "https://analyst-assessment-production.up.railway.app/api/v1/accounts/" + approval["duplicate_account_id"],
        json=approval["patch"],
        timeout=60,
    )
    entry["request_sent"] = True
    entry["crm_request_sent"] = True
    entry["http_status"] = getattr(response, "status_code", None)
    if getattr(response, "status_code", None) != 200:
        entry["result"] = "error"
        entry["reason"] = "PATCH did not return HTTP 200"
        _audit(entry, audit_path)
        return entry
    verified = snapshot_loader()
    verified_account = verified["accounts"][approval["duplicate_account_id"]]
    if _already_applied(verified_account, approval["survivor_account_id"]):
        entry["result"] = "applied"
        entry["verified_status"] = verified_account["status"]
        entry["verified_duplicate_of_account"] = verified_account["duplicate_of_account"]
    else:
        entry["result"] = "verify_failed"
        entry["reason"] = "GET after PATCH did not show the approved duplicate state"
    _audit(entry, audit_path)
    return entry


def apply_findlay_change(
    approval: dict,
    snapshot_loader,
    *,
    writes_enabled_flag: bool | None = None,
    session=None,
    audit_path: Path = AUDIT_PATH,
) -> dict:
    """Apply the one approved Findlay parent update, or refuse before any write.

    snapshot_loader() must read the facility and the parent account. The only
    body sent is {"parent_id": "0015QAPLGS3FVYEEEM"}. Any other response stops
    without a second request.
    """
    from src.ownership import (
        BELLHAVEN_PARENT_ID,
        BELLHAVEN_PARENT_NAME,
        FINDLAY_ACCOUNT,
        findlay_state,
        findlay_state_hash,
    )

    enabled = writes_enabled() if writes_enabled_flag is None else writes_enabled_flag
    entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "change_id": approval.get("change_id", ""),
        "operation": "reparent",
        "request_sent": False,
        "crm_request_sent": False,
    }
    exact_patch = {"parent_id": BELLHAVEN_PARENT_ID}
    exact = (
        approval.get("operation") == "reparent"
        and approval.get("account_id") == FINDLAY_ACCOUNT
        and approval.get("proposed_parent_id") == BELLHAVEN_PARENT_ID
        and approval.get("patch") == exact_patch
        and approval.get("change_id")
        and approval.get("state_hash")
        and isinstance(approval.get("before"), dict)
    )
    if not exact:
        entry["result"] = "refused"
        entry["reason"] = "A finding decision is not an executable CRM approval"
        _audit(entry, audit_path)
        return entry
    before = approval["before"]
    if int(before.get("lifetime_revenue") or 0) != 22000 or int(before.get("outstanding_ar") or 0) != 0:
        entry["result"] = "refused"
        entry["reason"] = "The approved Findlay financial values are not revenue 22000 and receivables 0"
        _audit(entry, audit_path)
        return entry
    if not enabled:
        entry["result"] = "blocked"
        entry["reason"] = "CRM write-back is disabled"
        _audit(entry, audit_path)
        return entry

    fresh = snapshot_loader()
    accounts = fresh.get("accounts") if isinstance(fresh, dict) else None
    if not isinstance(accounts, dict) or FINDLAY_ACCOUNT not in accounts or BELLHAVEN_PARENT_ID not in accounts:
        entry["result"] = "refused"
        entry["reason"] = "The current snapshot does not contain the Findlay account and the Bellhaven parent"
        _audit(entry, audit_path)
        return entry
    parent = accounts[BELLHAVEN_PARENT_ID]
    if (
        parent.get("name") != BELLHAVEN_PARENT_NAME
        or (parent.get("parent_id") or "") != ""
        or parent.get("status") != "Active"
    ):
        entry["result"] = "refused"
        entry["reason"] = "The parent account is not the active Bellhaven parent with no parent of its own"
        _audit(entry, audit_path)
        return entry
    facility = accounts[FINDLAY_ACCOUNT]
    state = findlay_state(facility)
    if state["lifetime_revenue"] != 22000 or state["outstanding_ar"] != 0:
        entry["result"] = "refused"
        entry["reason"] = "Findlay financial values changed. No parent update was sent."
        _audit(entry, audit_path)
        return entry
    if state["status"] != before.get("status"):
        entry["result"] = "refused"
        entry["reason"] = "Findlay status changed. No parent update was sent."
        _audit(entry, audit_path)
        return entry
    if state["parent_id"] == BELLHAVEN_PARENT_ID:
        entry["result"] = "already_applied"
        entry["reason"] = "Findlay already uses the approved parent. No second request was sent."
        _audit(entry, audit_path)
        return entry
    if findlay_state_hash(facility) != approval["state_hash"]:
        entry["result"] = "refused"
        entry["reason"] = "CRM state changed since this exact change was approved"
        entry["current_state_hash"] = findlay_state_hash(facility)
        _audit(entry, audit_path)
        return entry
    if session is None:
        from src.crm_read import authenticated_session

        session = authenticated_session()
    response = session.patch(
        "https://analyst-assessment-production.up.railway.app/api/v1/accounts/" + FINDLAY_ACCOUNT,
        json=exact_patch,
        timeout=60,
    )
    entry["request_sent"] = True
    entry["crm_request_sent"] = True
    entry["http_status"] = getattr(response, "status_code", None)
    if getattr(response, "status_code", None) != 200:
        entry["result"] = "error"
        entry["reason"] = "PATCH did not return HTTP 200. No alternative body was sent."
        _audit(entry, audit_path)
        return entry
    verified = snapshot_loader()
    verified_accounts = verified.get("accounts") if isinstance(verified, dict) else None
    verified_facility = verified_accounts.get(FINDLAY_ACCOUNT) if isinstance(verified_accounts, dict) else None
    verified_state = findlay_state(verified_facility or {})
    if (
        verified_state["parent_id"] == BELLHAVEN_PARENT_ID
        and verified_state["lifetime_revenue"] == 22000
        and verified_state["outstanding_ar"] == 0
        and verified_state["status"] == before.get("status")
    ):
        entry["result"] = "applied"
        entry["verified_parent_id"] = verified_state["parent_id"]
        entry["verified_status"] = verified_state["status"]
        entry["verified_lifetime_revenue"] = verified_state["lifetime_revenue"]
        entry["verified_outstanding_ar"] = verified_state["outstanding_ar"]
    else:
        entry["result"] = "verify_failed"
        entry["reason"] = "GET after PATCH did not show the approved parent and unchanged financial values"
    _audit(entry, audit_path)
    return entry
