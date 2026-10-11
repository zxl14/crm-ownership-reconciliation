"""Findlay parent proposal and the acquisition context shown beside it.

The homepage names Bellhaven Meadows of Findlay. It does not print a CRM
parent id. The proposed parent id is an inference from the Bellhaven parent
account, and it stays separate from the website sentence.
"""

from __future__ import annotations

import hashlib
import json

FINDLAY_ACCOUNT = "001UKEFGADQ8YCZ4YM"
BELLHAVEN_PARENT_ID = "0015QAPLGS3FVYEEEM"
BELLHAVEN_PARENT_NAME = "Bellhaven Senior Living (Parent Account)"
MARIETTA_ACCOUNT = "001A34WFSUYHCRBLFT"
TIFFIN_ACCOUNT = "001U6RW32TY0WSXZZB"
FINDLAY_ADDRESS = "1800 N Blanchard St, Findlay, OH 45840"

_WELCOME_NAME = "Bellhaven Meadows of Findlay"
_WELCOME_FAMILY = "Bellhaven family"


def _digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _welcome_is_specific(welcome_text: str) -> bool:
    text = welcome_text or ""
    return _WELCOME_NAME in text and _WELCOME_FAMILY in text


def findlay_state(account: dict) -> dict:
    return {
        "account_id": account.get("account_id", ""),
        "name": account.get("name", ""),
        "parent_id": account.get("parent_id") or "",
        "status": account.get("status", ""),
        "lifetime_revenue": int(account.get("lifetime_revenue") or 0),
        "outstanding_ar": int(account.get("outstanding_ar") or 0),
        "street": account.get("street", account.get("billing_street", "")),
        "city": account.get("city", account.get("billing_city", "")),
        "state": account.get("state", account.get("billing_state", "")),
        "zip": account.get("zip", account.get("billing_zip", "")),
    }


def findlay_state_hash(account: dict) -> str:
    return _digest(findlay_state(account))


def findlay_proposal(account: dict, website: dict, welcome_text: str) -> dict | None:
    """Return the direct parent proposal, or None when the evidence is not this case."""
    if account.get("account_id") != FINDLAY_ACCOUNT:
        return None
    if (account.get("parent_id") or "") != "":
        return None
    if int(account.get("lifetime_revenue") or 0) <= 0 or int(account.get("outstanding_ar") or 0) != 0:
        return None
    if account.get("financial_hold"):
        return None
    address = website.get("full_address") or ""
    if address != FINDLAY_ADDRESS:
        return None
    if not _welcome_is_specific(welcome_text):
        return None
    revenue = int(account["lifetime_revenue"])
    receivables = int(account["outstanding_ar"])
    inference = (
        "Inference, separate from the website sentence: the homepage does not print a CRM parent id. "
        f"The proposed parent_id {BELLHAVEN_PARENT_ID} is the account named {BELLHAVEN_PARENT_NAME}."
    )
    financial_rule = (
        f"Direct parent update, not a change-of-ownership split. "
        f"lifetime_revenue is {revenue} and outstanding_ar is {receivables}. "
        "Both are not greater than zero, so the existing account is updated in place "
        "and no second facility account is created."
    )
    proposal = (
        f"Proposed direct parent update for {FINDLAY_ACCOUNT}: set parent_id from blank to "
        f"{BELLHAVEN_PARENT_ID}. Current lifetime_revenue is {revenue} and outstanding_ar is {receivables}. "
        "This exact CRM change needs a separate executable approval. "
        "Approving the finding does not send it."
    )
    return {
        "account_id": FINDLAY_ACCOUNT,
        "current_parent_id": "",
        "proposed_parent_id": BELLHAVEN_PARENT_ID,
        "proposed_parent_name": BELLHAVEN_PARENT_NAME,
        "operation": "reparent",
        "matched_address": FINDLAY_ADDRESS,
        "lifetime_revenue": revenue,
        "outstanding_ar": receivables,
        "website_evidence": welcome_text.strip(),
        "inference": inference,
        "financial_rule": financial_rule,
        "proposal": proposal,
        "patch": {"parent_id": BELLHAVEN_PARENT_ID},
    }


def build_findlay_change(account: dict, proposal: dict) -> dict:
    state = findlay_state(account)
    digest = findlay_state_hash(account)
    patch = dict(proposal["patch"])
    return {
        "change_id": _digest(
            {
                "operation": "reparent",
                "account_id": FINDLAY_ACCOUNT,
                "state_hash": digest,
                "patch": patch,
            }
        ),
        "operation": "reparent",
        "account_id": FINDLAY_ACCOUNT,
        "proposed_parent_id": BELLHAVEN_PARENT_ID,
        "state_hash": digest,
        "patch": patch,
        "before": state,
        "website_evidence": proposal["website_evidence"],
        "inference": proposal["inference"],
        "preview": {
            "writes_enabled": False,
            "request_will_be_sent": False,
            "method": "PATCH",
            "path": "/api/v1/accounts/" + FINDLAY_ACCOUNT,
            "body": patch,
            "openapi_request_body_schema": None,
            "matched_address": proposal["matched_address"],
            "current_parent_id": "",
            "proposed_parent_id": BELLHAVEN_PARENT_ID,
            "lifetime_revenue": proposal["lifetime_revenue"],
            "outstanding_ar": proposal["outstanding_ar"],
            "financial_rule": proposal["financial_rule"],
            "website_evidence": proposal["website_evidence"],
            "inference": proposal["inference"],
            "body_basis": (
                "The only permitted body is parent_id. "
                "It is sent only after this exact change is approved and CRM_WRITES_ENABLED is true. "
                "This preview itself sends nothing."
            ),
        },
    }


def acquisition_context(result: dict, about_text: str) -> dict:
    holds = []
    for match in result["one_to_one"]:
        account = match["crm_accounts"][0]
        if account["account_id"] not in {MARIETTA_ACCOUNT, TIFFIN_ACCOUNT}:
            continue
        holds.append(
            {
                "account_id": account["account_id"],
                "name": account["name"],
                "parent_name": account.get("parent_name") or "",
                "lifetime_revenue": account["lifetime_revenue"],
                "outstanding_ar": account["outstanding_ar"],
                "financial_hold": account["financial_hold"],
            }
        )
    holds.sort(key=lambda item: item["name"])
    investigations = []
    for group in result["duplicate_groups"]:
        if group["same_normalized_name"]:
            continue
        investigations.append(group["normalized_address"])
    about = about_text.strip()
    if "Harborview Care Group family of communities" in about:
        harborview = (
            "The About page says the Harborview Care Group family of communities was welcomed in 2025. "
            "It does not name the communities or a CRM parent id, so no Harborview account is re-parented."
        )
    else:
        harborview = (
            "The saved About text does not name the Harborview Care Group family, "
            "so no Harborview account is re-parented."
        )
    if "select communities joining us from Cedar Trail" in about:
        cedar = (
            "The About page says select communities joined from Cedar Trail in 2026. "
            "Select does not identify Marietta, Tiffin, or any other facility, so no Cedar Trail parent change is proposed."
        )
    else:
        cedar = (
            "The saved About text does not say that select Cedar Trail communities joined, "
            "so no Cedar Trail parent change is proposed."
        )
    return {
        "about_text": about,
        "harborview": harborview,
        "cedar_trail": cedar,
        "financial_holds": holds,
        "financial_hold_note": (
            "Marietta and Tiffin are potential financial-hold cases. "
            "Each is a one-to-one name and address match whose current parent is Cedar Trail, "
            "and both lifetime revenue and outstanding receivables are above zero. "
            "They are not confirmed ownership changes. "
            "A later verified parent change would preserve the old account and its parent, "
            "create a new facility account, and set chow_current_account."
        ),
        "investigation_addresses": investigations,
        "investigation_note": (
            "Locations with more than one CRM name at the same address stay investigation-only. "
            "A shared address does not choose a survivor or a parent."
        ),
    }
