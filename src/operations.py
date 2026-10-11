"""Plans and local execution for CRM changes that are not a duplicate mark.

A plan describes an exact operation. It does not send anything. The executor
below runs only against a session the caller supplies, and only after an
approval record for that operation. Historical revenue and receivables are
never placed in a request body.

POST /api/v1/accounts and PATCH fields other than status and
duplicate_of_account are not in the published OpenAPI body. Those plans stay
blocked unless a local test explicitly allows an unverified body. This module
does not open a network session.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from src.writeback import writes_enabled

ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "data" / "audit" / "operations.jsonl"
BASE_URL = "https://analyst-assessment-production.up.railway.app"
CREATED = "$created"
FINANCIAL_FIELDS = frozenset({"lifetime_revenue", "outstanding_ar"})


def _digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _observation(reason: str) -> dict:
    return {
        "operation": "none",
        "actionable": False,
        "live_send": "blocked_evidence",
        "reason": reason,
        "steps": [],
    }


def _money(account: dict, field: str) -> int:
    return int(account.get(field) or 0)


def financial_hold(account: dict) -> bool:
    return _money(account, "lifetime_revenue") > 0 and _money(account, "outstanding_ar") > 0


def account_state(account: dict) -> dict:
    return {
        "account_id": account.get("account_id", ""),
        "name": account.get("name", ""),
        "parent_id": account.get("parent_id", ""),
        "status": account.get("status", ""),
        "lifetime_revenue": _money(account, "lifetime_revenue"),
        "outstanding_ar": _money(account, "outstanding_ar"),
        "chow_current_account": account.get("chow_current_account") or "",
        "duplicate_of_account": account.get("duplicate_of_account") or "",
    }


def _new_facility_body(account: dict, new_parent_id: str) -> dict:
    return {
        "name": account.get("name", ""),
        "parent_id": new_parent_id,
        "billing_street": account.get("billing_street", account.get("street", "")),
        "billing_city": account.get("billing_city", account.get("city", "")),
        "billing_state": account.get("billing_state", account.get("state", "")),
        "billing_zip": account.get("billing_zip", account.get("zip", "")),
        "care_type": account.get("care_type", ""),
        "phone": account.get("phone", ""),
        "status": "Active",
    }


def ownership_plan(account: dict, new_parent_id: str, *, verified: bool) -> dict:
    """Choose change-of-ownership or a direct parent update.

    The change-of-ownership branch keeps the old account and its parent, creates
    a new facility account, and points chow_current_account at that new id.
    Direct re-parenting is a single parent_id change. Neither branch copies
    lifetime_revenue or outstanding_ar into the request.
    """
    if not verified or not new_parent_id:
        return _observation(
            "No verified new parent was supplied, so no ownership change is proposed."
        )
    old_parent = account.get("parent_id") or ""
    if old_parent == new_parent_id:
        return {
            "operation": "none",
            "actionable": False,
            "live_send": "already_applied",
            "reason": "The account is already under the verified parent.",
            "steps": [],
        }
    if financial_hold(account):
        steps = [
            {"method": "POST", "path": "/api/v1/accounts", "body": _new_facility_body(account, new_parent_id)},
            {
                "method": "PATCH",
                "path": "/api/v1/accounts/" + account["account_id"],
                "body": {"chow_current_account": CREATED},
            },
        ]
        reason = (
            "Lifetime revenue and outstanding receivables are both greater than zero. "
            "The old account and its parent stay in place. A new facility account is created "
            "under the verified parent, then chow_current_account on the old account is set to the new id. "
            "The POST body and chow_current_account are not in the published request schema, so this plan is not sent."
        )
        operation = "change_of_ownership"
    else:
        steps = [
            {
                "method": "PATCH",
                "path": "/api/v1/accounts/" + account["account_id"],
                "body": {"parent_id": new_parent_id},
            }
        ]
        reason = (
            "A verified parent change is planned as one parent_id update because the account does not have "
            "both lifetime revenue and outstanding receivables above zero. "
            "parent_id was not part of the verified duplicate PATCH, so this plan is not sent."
        )
        operation = "reparent"
    return {
        "operation": operation,
        "actionable": True,
        "live_send": "blocked_schema",
        "reason": reason,
        "steps": steps,
        "new_parent_id": new_parent_id,
        "preserve_parent_id": old_parent,
    }


def name_plan(account: dict, new_name: str, *, address_matches: bool, phone_matches: bool) -> dict:
    if not address_matches:
        return _observation("The address does not match, so the name is not changed.")
    if not phone_matches:
        return _observation(
            "The address matches, but the phone does not confirm it is the same facility. The name is not changed."
        )
    if not new_name or new_name.strip() == (account.get("name") or "").strip():
        return _observation("The names already agree, so no name change is proposed.")
    return {
        "operation": "update_name",
        "actionable": True,
        "live_send": "blocked_schema",
        "reason": (
            "The address and phone match, so the account name can be updated to the verified name. "
            "The PATCH field name is not in the published schema, so this plan is not sent."
        ),
        "steps": [
            {
                "method": "PATCH",
                "path": "/api/v1/accounts/" + account["account_id"],
                "body": {"name": new_name},
            }
        ],
    }


def create_facility_plan(
    *,
    name: str,
    parent_id: str,
    street: str,
    city: str,
    state: str,
    zip_code: str,
    care_type: str = "",
    verified_parent: bool,
) -> dict:
    if not verified_parent or not parent_id:
        return _observation(
            "A missing facility is not created because no parent was verified for it."
        )
    if not all((name, street, city, state, zip_code)):
        return _observation(
            "A missing facility is not created because its name or address is incomplete."
        )
    return {
        "operation": "create_facility",
        "actionable": True,
        "live_send": "blocked_schema",
        "reason": (
            "The facility and its parent are both verified, so a new account could be created. "
            "The POST body is not in the published schema, so this plan is not sent."
        ),
        "steps": [
            {
                "method": "POST",
                "path": "/api/v1/accounts",
                "body": {
                    "name": name,
                    "parent_id": parent_id,
                    "billing_street": street,
                    "billing_city": city,
                    "billing_state": state,
                    "billing_zip": zip_code,
                    "care_type": care_type,
                    "status": "Active",
                },
            }
        ],
        "new_parent_id": parent_id,
    }


def status_plan(*, listed_on_website: bool) -> dict:
    if listed_on_website:
        return _observation(
            "A facility appearing on the website does not by itself justify a status change."
        )
    return _observation(
        "A facility missing from the website does not by itself justify a status change."
    )


def approval_for(plan: dict, account: dict) -> dict:
    if not plan.get("actionable"):
        raise RuntimeError("An observation cannot be approved as a CRM change")
    before = account_state(account)
    new_parent_id = plan.get("new_parent_id", "")
    record = {
        "change_id": _digest(
            {
                "operation": plan["operation"],
                "account_id": account["account_id"],
                "steps": plan["steps"],
                "state": before,
            }
        ),
        "operation": plan["operation"],
        "account_id": account["account_id"],
        "new_parent_id": new_parent_id,
        "preserve_parent_id": plan.get("preserve_parent_id", before["parent_id"]),
        "steps": plan["steps"],
        "state_hash": _digest(before),
        "before": before,
        "completed_steps": [],
        "created_account_id": "",
        "finding_decision_authorizes_write": False,
    }
    return record


def _audit(entry: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def _latest_progress(change_id: str, path: Path) -> dict:
    if not path.exists():
        return {}
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("change_id") != change_id:
            continue
        if row.get("completed_steps") or row.get("created_account_id"):
            latest = row
    return latest


def _response_body(response) -> dict:
    reader = getattr(response, "json", None)
    if not callable(reader):
        return {}
    try:
        payload = reader()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _terminal(approval: dict, snapshot: dict) -> bool:
    accounts = snapshot.get("accounts") if isinstance(snapshot, dict) else None
    if not isinstance(accounts, dict) or approval["account_id"] not in accounts:
        return False
    account = accounts[approval["account_id"]]
    before = approval["before"]
    if _money(account, "lifetime_revenue") != int(before["lifetime_revenue"]):
        return False
    if _money(account, "outstanding_ar") != int(before["outstanding_ar"]):
        return False
    operation = approval["operation"]
    if operation == "change_of_ownership":
        created = approval.get("created_account_id") or account.get("chow_current_account") or ""
        if not created or created not in accounts:
            return False
        if (account.get("parent_id") or "") != approval["preserve_parent_id"]:
            return False
        if (account.get("chow_current_account") or "") != created:
            return False
        return (accounts[created].get("parent_id") or "") == approval["new_parent_id"]
    if operation == "reparent":
        same_chow = (account.get("chow_current_account") or "") == (before.get("chow_current_account") or "")
        return (account.get("parent_id") or "") == approval["new_parent_id"] and same_chow
    if operation == "update_name":
        return account.get("name") == approval["steps"][0]["body"]["name"]
    if operation == "create_facility":
        created = approval.get("created_account_id") or ""
        return bool(created and created in accounts and (accounts[created].get("parent_id") or "") == approval["new_parent_id"])
    return False


def _unsafe_body(body: dict) -> str:
    if not isinstance(body, dict):
        return "A change step needs a JSON object body"
    if FINANCIAL_FIELDS & set(body):
        return "Historical financial fields cannot be changed"
    return ""


def apply_steps(
    approval: dict,
    session,
    loader,
    *,
    writes_enabled_flag: bool | None = None,
    allow_unverified: bool = False,
    audit_path: Path = AUDIT_PATH,
) -> dict:
    """Run the remaining steps of one approved plan.

    Completed steps are not repeated. A failed later step is audited as a
    partial result, and the next run continues from the audit record. Live
    unverified bodies are refused unless allow_unverified is set for a local mock.
    """
    enabled = writes_enabled() if writes_enabled_flag is None else writes_enabled_flag
    entry = {
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "change_id": approval.get("change_id", ""),
        "operation": approval.get("operation", ""),
        "request_sent": False,
        "crm_request_sent": False,
        "completed_steps": [],
        "created_account_id": "",
    }
    required = ("change_id", "operation", "account_id", "steps", "state_hash", "before")
    if any(field not in approval for field in required) or not approval.get("steps"):
        entry["result"] = "refused"
        entry["reason"] = "A finding decision is not an executable CRM approval"
        _audit(entry, audit_path)
        return entry
    for step in approval["steps"]:
        problem = _unsafe_body(step.get("body"))
        if problem:
            entry["result"] = "refused"
            entry["reason"] = problem
            _audit(entry, audit_path)
            return entry
        if step.get("method") not in {"POST", "PATCH"}:
            entry["result"] = "refused"
            entry["reason"] = "Only POST and PATCH steps are defined"
            _audit(entry, audit_path)
            return entry
    if not enabled:
        entry["result"] = "blocked"
        entry["reason"] = "CRM write-back is disabled"
        _audit(entry, audit_path)
        return entry

    progress = _latest_progress(approval["change_id"], audit_path)
    if not approval.get("completed_steps") and progress.get("completed_steps"):
        approval["completed_steps"] = list(progress["completed_steps"])
        approval["created_account_id"] = progress.get("created_account_id") or approval.get("created_account_id") or ""
    completed = list(approval.get("completed_steps") or [])
    entry["completed_steps"] = completed
    entry["created_account_id"] = approval.get("created_account_id") or ""
    if len(completed) >= len(approval["steps"]):
        entry["result"] = "already_applied"
        entry["reason"] = "Every step of this change was already completed"
        _audit(entry, audit_path)
        return entry

    fresh = loader()
    accounts = fresh.get("accounts") if isinstance(fresh, dict) else None
    if not isinstance(accounts, dict) or approval["account_id"] not in accounts:
        entry["result"] = "refused"
        entry["reason"] = "The current snapshot does not contain the account"
        _audit(entry, audit_path)
        return entry
    if _terminal(approval, fresh):
        entry["result"] = "already_applied"
        entry["reason"] = "The CRM already shows the approved end state"
        _audit(entry, audit_path)
        return entry
    if not completed and _digest(account_state(accounts[approval["account_id"]])) != approval["state_hash"]:
        entry["result"] = "refused"
        entry["reason"] = "CRM state changed since this exact change was approved"
        _audit(entry, audit_path)
        return entry
    if not allow_unverified:
        entry["result"] = "refused"
        entry["reason"] = (
            "This operation uses a request body that OpenAPI does not publish. It was not sent."
        )
        _audit(entry, audit_path)
        return entry
    if session is None:
        entry["result"] = "refused"
        entry["reason"] = "No local session was provided, so the unverified plan was not sent."
        _audit(entry, audit_path)
        return entry

    for index, step in enumerate(approval["steps"]):
        if index < len(completed):
            continue
        body = dict(step["body"])
        if body.get("chow_current_account") == CREATED:
            created = approval.get("created_account_id") or ""
            if not created:
                entry["result"] = "partial" if completed else "error"
                entry["reason"] = "The new account id is missing, so chow_current_account was not set"
                entry["completed_steps"] = completed
                _audit(entry, audit_path)
                return entry
            body["chow_current_account"] = created
        problem = _unsafe_body(body)
        if problem:
            entry["result"] = "refused"
            entry["reason"] = problem
            entry["completed_steps"] = completed
            _audit(entry, audit_path)
            return entry
        url = BASE_URL + step["path"]
        if step["method"] == "POST":
            response = session.post(url, json=body, timeout=60)
            status = getattr(response, "status_code", None)
            payload = _response_body(response)
            new_id = str(payload.get("account_id") or "")
            if status not in {200, 201} or not new_id:
                entry["result"] = "partial" if completed else "error"
                entry["reason"] = "The account create did not return a new account id"
                entry["http_status"] = status
                entry["request_sent"] = True
                entry["crm_request_sent"] = True
                entry["completed_steps"] = completed
                _audit(entry, audit_path)
                return entry
            approval["created_account_id"] = new_id
        else:
            response = session.patch(url, json=body, timeout=60)
            status = getattr(response, "status_code", None)
            if status != 200:
                entry["result"] = "partial" if completed else "error"
                entry["reason"] = "PATCH did not return HTTP 200"
                entry["http_status"] = status
                entry["request_sent"] = True
                entry["crm_request_sent"] = True
                entry["completed_steps"] = completed
                entry["created_account_id"] = approval.get("created_account_id") or ""
                _audit(entry, audit_path)
                return entry
        completed.append({"index": index, "method": step["method"], "path": step["path"]})
        approval["completed_steps"] = completed
        entry["request_sent"] = True
        entry["crm_request_sent"] = True

    verified = loader()
    entry["completed_steps"] = completed
    entry["created_account_id"] = approval.get("created_account_id") or ""
    if _terminal(approval, verified):
        entry["result"] = "applied"
        entry["reason"] = "GET after the last step shows the approved end state"
    else:
        entry["result"] = "verify_failed"
        entry["reason"] = "GET after the last step did not show the approved end state"
    _audit(entry, audit_path)
    return entry
