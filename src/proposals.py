"""Turn a review finding into an executable plan, or leave it as an observation.

Findings stay non-executable. This module reads the same finding and decides
whether the saved evidence is enough for a separate CRM change. Website
presence, website absence, and an unnamed parent do not become updates.
"""

from __future__ import annotations

from src.findings import (
    ADDRESS_DISCREPANCY,
    NAME_DISCREPANCY,
    OWNERSHIP,
    POSSIBLE_DUPLICATE,
    UNMATCHED_CRM,
    UNMATCHED_WEBSITE,
)
from src.normalize import normalize_name, phone_digits
from src.operations import create_facility_plan, name_plan, status_plan


def _merge(finding: dict, live_accounts: dict | None) -> list[dict]:
    rows = []
    live_accounts = live_accounts or {}
    for account in finding.get("crm_accounts") or []:
        current = dict(account)
        fresh = live_accounts.get(account.get("account_id", ""))
        if isinstance(fresh, dict):
            for field in (
                "status",
                "duplicate_of_account",
                "parent_id",
                "parent_name",
                "lifetime_revenue",
                "outstanding_ar",
                "name",
                "phone",
                "chow_current_account",
            ):
                if field in fresh:
                    current[field] = fresh[field]
        rows.append(current)
    return rows


def _duplicate_applied(accounts: list[dict]) -> bool:
    ids = {account.get("account_id", "") for account in accounts}
    for account in accounts:
        target = account.get("duplicate_of_account") or ""
        if account.get("status") == "Inactive" and target in ids and target != account.get("account_id"):
            return True
    return False


def _observation(reason: str) -> dict:
    return {
        "kind": "observation",
        "actionable": False,
        "live_send": "blocked_evidence",
        "reason": reason,
        "steps": [],
        "candidate_ids": [],
    }


def assess_finding(finding: dict, live_accounts: dict | None = None) -> dict:
    change = finding.get("ownership_change")
    if isinstance(change, dict) and change.get("operation") == "reparent":
        return {
            "kind": "reparent",
            "actionable": True,
            "live_send": "ready",
            "reason": change["financial_rule"] + " " + change["inference"],
            "steps": [
                {
                    "method": "PATCH",
                    "path": "/api/v1/accounts/" + change["account_id"],
                    "body": dict(change["patch"]),
                }
            ],
            "candidate_ids": [change["account_id"]],
            "website_evidence": change["website_evidence"],
            "inference": change["inference"],
        }
    accounts = _merge(finding, live_accounts)
    categories = set(finding.get("categories") or [])
    if POSSIBLE_DUPLICATE in categories and len(accounts) >= 2:
        names = {normalize_name(account.get("name", "")) for account in accounts}
        if len(names) == 1 and _duplicate_applied(accounts):
            return {
                "kind": "already_applied",
                "actionable": False,
                "live_send": "already_applied",
                "reason": (
                    "This confirmed duplicate is already marked. The inactive account points at the "
                    "surviving account. No further CRM change is proposed."
                ),
                "steps": [],
                "candidate_ids": [account["account_id"] for account in accounts],
            }
        if len(names) == 1 and len(accounts) == 2:
            return {
                "kind": "mark_duplicate",
                "actionable": True,
                "live_send": "ready",
                "reason": (
                    "These accounts share a normalized name and address. Designate a survivor before any write. "
                    "The only verified live body sets status to Inactive and duplicate_of_account to the survivor."
                ),
                "steps": [],
                "candidate_ids": [account["account_id"] for account in accounts],
            }
        return _observation(
            "These accounts share an address but not a normalized name. "
            "That is not enough to mark a duplicate or change a parent."
        )

    notes = []
    if NAME_DISCREPANCY in categories and len(accounts) == 1:
        website = finding.get("website") or {}
        account = accounts[0]
        website_phone = phone_digits(str(website.get("phone") or ""))
        account_phone = phone_digits(str(account.get("phone") or ""))
        plan = name_plan(
            account,
            str(website.get("name") or ""),
            address_matches=True,
            phone_matches=bool(website_phone) and website_phone == account_phone,
        )
        if plan["actionable"]:
            return {
                "kind": plan["operation"],
                "actionable": False,
                "live_send": plan["live_send"],
                "reason": plan["reason"],
                "steps": plan["steps"],
                "candidate_ids": [account["account_id"]],
            }
        notes.append(plan["reason"])
    if OWNERSHIP in categories:
        notes.append("The website does not name a parent, so no ownership change is proposed.")
    if UNMATCHED_WEBSITE in categories:
        notes.append(create_facility_plan(
            name="",
            parent_id="",
            street="",
            city="",
            state="",
            zip_code="",
            verified_parent=False,
        )["reason"])
    if UNMATCHED_CRM in categories:
        notes.append(status_plan(listed_on_website=False)["reason"])
    if ADDRESS_DISCREPANCY in categories and not notes:
        notes.append("The address disagreement is not a confirmed account match, so no CRM change is proposed.")
    if not notes:
        notes.append("This finding stays an observation. The saved evidence does not justify an account change.")
    return _observation(" ".join(notes))
