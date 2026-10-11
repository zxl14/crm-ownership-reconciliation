"""Turn the saved reconciliation into stable review findings.

A finding describes evidence. It is not a CRM request. Name differences, a
missing website listing, and a Bellhaven page without a named parent do not
become executable updates.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.normalize import normalize_city, normalize_state, normalize_street, normalize_zip
from src.ownership import acquisition_context, findlay_proposal
from src.reconcile import load_crm_accounts, reconcile
from src.site_context import load_site_context

ROOT = Path(__file__).resolve().parents[1]
WEBSITE_PATH = ROOT / "data" / "website" / "facilities.json"
CRM_PATH = ROOT / "data" / "crm" / "accounts.json"

POSSIBLE_DUPLICATE = "Possible duplicate"
NAME_DISCREPANCY = "Name discrepancy"
ADDRESS_DISCREPANCY = "Address discrepancy"
OWNERSHIP = "Possible ownership discrepancy"
UNMATCHED_WEBSITE = "Unmatched website facility"
UNMATCHED_CRM = "Unmatched CRM facility"
INVESTIGATE = "Requires investigation"

CATEGORIES = [
    POSSIBLE_DUPLICATE,
    NAME_DISCREPANCY,
    ADDRESS_DISCREPANCY,
    OWNERSHIP,
    UNMATCHED_WEBSITE,
    UNMATCHED_CRM,
    INVESTIGATE,
]

BELLHAVEN_PARENT = "Bellhaven Senior Living (Parent Account)"
NO_CHANGE = "No CRM change is proposed."


def load_website_facilities(path: Path = WEBSITE_PATH) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    facilities = payload["facilities"] if isinstance(payload, dict) else payload
    if not isinstance(facilities, list):
        raise RuntimeError("Saved website file does not contain a facility list")
    return facilities


def _stable_id(kind: str, parts: list[str]) -> str:
    text = kind + "\n" + "\n".join(parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _evidence_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _parent_label(account: dict) -> str:
    return account.get("parent_name") or "(blank)"


def _is_bellhaven_parent(account: dict) -> bool:
    return account.get("parent_name") == BELLHAVEN_PARENT


def _crm_public(account: dict) -> dict:
    public = {
        "account_id": account.get("account_id", ""),
        "name": account.get("name", ""),
        "street": account.get("street", ""),
        "city": account.get("city", ""),
        "state": account.get("state", ""),
        "zip": account.get("zip", ""),
        "normalized_address": account.get("normalized_address", ""),
        "care_type": account.get("care_type", ""),
        "status": account.get("status", ""),
        "parent_id": account.get("parent_id", ""),
        "parent_name": account.get("parent_name", ""),
        "lifetime_revenue": account.get("lifetime_revenue", 0),
        "outstanding_ar": account.get("outstanding_ar", 0),
        "financial_hold": bool(account.get("financial_hold")),
    }
    duplicate_of = account.get("duplicate_of_account") or ""
    if duplicate_of:
        public["duplicate_of_account"] = duplicate_of
    return public


def _website_public(facility: dict | None) -> dict | None:
    if facility is None:
        return None
    offerings = facility.get("care_offerings") or []
    return {
        "name": facility.get("name", ""),
        "street": facility.get("street", ""),
        "city": facility.get("city", ""),
        "state": facility.get("state", ""),
        "zip": facility.get("zip", ""),
        "full_address": facility.get("full_address", ""),
        "normalized_address": facility.get("normalized_address", ""),
        "care_offerings": list(offerings),
        "source_url": facility.get("source_url", ""),
        "listed_in_directory": bool(facility.get("listed_in_directory")),
    }


def _financial_lines(accounts: list[dict]) -> list[str]:
    lines = []
    for account in accounts:
        lines.append(
            f"{account['account_id']} lifetime revenue {account['lifetime_revenue']}, "
            f"outstanding AR {account['outstanding_ar']}."
        )
        if account["financial_hold"]:
            lines.append(
                f"{account['account_id']} has both amounts above zero. "
                "If a parent change is proposed later, keep this account and its parent, "
                "create a new facility account, and set chow_current_account. "
                "No parent change is proposed here."
            )
    if not lines:
        lines.append("No CRM facility account is attached, so revenue and AR were not compared.")
    return lines


def _care_line(website: dict | None, accounts: list[dict]) -> str | None:
    if website is None or not accounts:
        return None
    offered = "; ".join(website.get("care_offerings") or [])
    stored = "; ".join(account.get("care_type") or "(blank)" for account in accounts)
    if not offered:
        return None
    if offered == stored:
        return None
    return (
        f"Care labels differ. Website: {offered}. CRM: {stored}. "
        "No crosswalk was provided, so no care-type change is proposed."
    )


def _make_finding(
    kind: str,
    id_parts: list[str],
    categories: list[str],
    title: str,
    confidence: str,
    uncertainty: str,
    website: dict | None,
    accounts: list[dict],
    evidence: list[str],
    proposal: str,
    proposal_justified: bool,
) -> dict:
    public_accounts = [_crm_public(account) for account in accounts]
    public_website = _website_public(website)
    ordered = [category for category in CATEGORIES if category in categories]
    evidence = [line for line in evidence if line]
    care = _care_line(public_website, public_accounts)
    if care:
        evidence.append(care)
    evidence.extend(_financial_lines(public_accounts))
    basis = {
        "categories": ordered,
        "website": public_website,
        "crm_accounts": public_accounts,
        "evidence": evidence,
        "proposal": proposal,
        "proposal_justified": proposal_justified,
    }
    return {
        "finding_id": _stable_id(kind, id_parts),
        "evidence_hash": _evidence_hash(basis),
        "categories": ordered,
        "title": title,
        "confidence": confidence,
        "uncertainty": uncertainty,
        "website": public_website,
        "crm_accounts": public_accounts,
        "evidence": evidence,
        "proposal": proposal,
        "proposal_justified": proposal_justified,
        "executable": False,
        "financial_hold": any(account["financial_hold"] for account in public_accounts),
    }


def _index_accounts(result: dict) -> dict[str, dict]:
    indexed = {}
    for account in result["unmatched_crm"]:
        indexed[account["account_id"]] = account
    for match in result["high_matches"] + result["low_matches"]:
        for account in match["crm_accounts"]:
            indexed[account["account_id"]] = account
    for group in result["duplicate_groups"]:
        for account in group["crm_accounts"]:
            indexed[account["account_id"]] = account
    return indexed


def _index_websites(result: dict, facilities: list[dict]) -> dict[str, dict]:
    indexed = {}
    for facility in facilities:
        view = {
            **facility,
            "normalized_address": "",
            "source_url": facility.get("source_url", ""),
        }
        indexed[view["source_url"]] = view
    for bucket in ("unmatched_website",):
        for facility in result[bucket]:
            indexed[facility["source_url"]] = facility
    for match in result["high_matches"] + result["low_matches"]:
        indexed[match["website"]["source_url"]] = match["website"]
    return indexed


def _applied_duplicate(accounts: list[dict]) -> tuple[str, str] | None:
    ids = {account["account_id"] for account in accounts}
    for account in accounts:
        target = account.get("duplicate_of_account") or ""
        if account.get("status") == "Inactive" and target in ids and target != account["account_id"]:
            return account["account_id"], target
    return None


def _duplicate_finding(group: dict) -> dict:
    accounts = group["crm_accounts"]
    websites = group["website"]
    website = websites[0] if websites else None
    categories = [POSSIBLE_DUPLICATE]
    parents = {_parent_label(account) for account in accounts}
    non_bellhaven_parent = any(not _is_bellhaven_parent(account) for account in accounts)
    applied = _applied_duplicate(accounts) if group["same_normalized_name"] else None
    if applied is None and (
        not group["same_normalized_name"] or any(account["financial_hold"] for account in accounts)
    ):
        categories.append(INVESTIGATE)
    if len(parents) > 1 or non_bellhaven_parent:
        categories.append(OWNERSHIP)
    same_name = group["same_normalized_name"]
    if applied is not None:
        loser_id, survivor_id = applied
        proposal = (
            f"Already applied: {loser_id} is Inactive and duplicate_of_account points at {survivor_id}. "
            "No further duplicate change is proposed."
        )
        justified = False
        confidence = "high"
        uncertainty = "The duplicate link is already present in the CRM."
    elif same_name:
        proposal = (
            "Described only: these accounts share a normalized name and address. "
            "A later review can choose the surviving account, mark the other Inactive, "
            "and set duplicate_of_account to the survivor. The survivor is not chosen here."
        )
        justified = True
        confidence = "high"
        uncertainty = "The duplicate relationship is well supported. Which account survives is undecided."
    else:
        proposal = NO_CHANGE + " Different names at one address may be separate care programs."
        justified = False
        confidence = "not confirmed"
        uncertainty = "The shared address does not confirm that these CRM accounts are one facility."
    title_name = website["name"] if website else accounts[0]["name"]
    evidence = [
        line
        for line in group["evidence"]
        if not (applied is not None and line.startswith("No surviving account"))
    ]
    if applied is not None:
        loser_id, survivor_id = applied
        evidence.append(
            f"Already applied: {loser_id} is Inactive and duplicate_of_account is {survivor_id}."
        )
    evidence.append(
        "Current parents: " + "; ".join(f"{account['account_id']} {_parent_label(account)}" for account in accounts) + "."
    )
    if OWNERSHIP in categories:
        evidence.append(
            "Parent names differ, or one parent is not Bellhaven Senior Living. "
            "That is a CRM fact, not a confirmed reason to change parent_id."
        )
    return _make_finding(
        "duplicate",
        [group["normalized_address"], *[account["account_id"] for account in sorted(accounts, key=lambda item: item["account_id"])]],
        categories,
        f"{title_name} — {len(accounts)} CRM accounts at one address",
        confidence,
        uncertainty,
        website,
        accounts,
        evidence,
        proposal,
        justified,
    )


def _name_finding(match: dict) -> dict:
    website = match["website"]
    account = match["crm_accounts"][0]
    categories = [NAME_DISCREPANCY]
    if not _is_bellhaven_parent(account):
        categories.append(OWNERSHIP)
    evidence = list(match["evidence"])
    evidence.append("A name difference by itself is not a rename instruction.")
    if OWNERSHIP in categories:
        evidence.append(
            f"CRM parent is {_parent_label(account)}. The website does not name a parent, so parent_id is not changed."
        )
    return _make_finding(
        "name",
        [website["source_url"], account["account_id"]],
        categories,
        f"{website['name']} — CRM name differs",
        "high",
        "The address match is high confidence. No rename is proposed from the name difference alone.",
        website,
        [account],
        evidence,
        NO_CHANGE + " A name difference alone does not authorize a rename.",
        False,
    )


def _same_street_different_zip(website: dict, account: dict) -> bool:
    return (
        normalize_street(website.get("street", "")) == normalize_street(account.get("street", ""))
        and normalize_city(website.get("city", "")) == normalize_city(account.get("city", ""))
        and normalize_state(website.get("state", "")) == normalize_state(account.get("state", ""))
        and normalize_zip(website.get("zip", "")) != normalize_zip(account.get("zip", ""))
    )


def _address_conflict_finding(conflict: dict, websites: dict[str, dict], accounts: dict[str, dict]) -> dict:
    website = websites[conflict["website_url"]]
    account = accounts[conflict["crm_account_id"]]
    categories = [ADDRESS_DISCREPANCY]
    close = _same_street_different_zip(website, account)
    if close:
        categories.append(INVESTIGATE)
        uncertainty = "The name and street agree, and the ZIP codes do not. These records were not matched."
    else:
        uncertainty = "The normalized names agree and the addresses do not. These records were not treated as the same facility."
    return _make_finding(
        "address",
        [website["source_url"], account["account_id"]],
        categories,
        f"{website['name']} — address does not match {account['name']}",
        "not confirmed",
        uncertainty,
        website,
        [account],
        [
            conflict["evidence"],
            f"Website address: {website.get('full_address', '')}.",
            f"CRM address: {account.get('street', '')}, {account.get('city', '')}, {account.get('state', '')} {account.get('zip', '')}.",
        ],
        NO_CHANGE + " Differing addresses do not authorize an address overwrite.",
        False,
    )


def _low_address_finding(match: dict) -> dict:
    website = match["website"]
    accounts = match["crm_accounts"]
    return _make_finding(
        "address",
        [website["source_url"], *[account["account_id"] for account in accounts]],
        [ADDRESS_DISCREPANCY, INVESTIGATE],
        f"{website['name']} — street does not match the CRM street",
        "low",
        "Name, city, state, and ZIP agree, but the street does not. This is not a confident match.",
        website,
        accounts,
        list(match["evidence"]) + list(match.get("assumptions") or []),
        NO_CHANGE + " A low-confidence street disagreement does not authorize an address change.",
        False,
    )


def _findlay_finding(match: dict, proposal: dict) -> dict:
    website = match["website"]
    account = match["crm_accounts"][0]
    finding = _make_finding(
        "ownership",
        [website["source_url"], account["account_id"]],
        [OWNERSHIP],
        f"{website['name']} — proposed parent {proposal['proposed_parent_id']}",
        "high",
        (
            "The facility match and the homepage sentence are website evidence. "
            "The CRM parent id is an inference and still needs a separate executable approval."
        ),
        website,
        [account],
        [
            "Website evidence: " + proposal["website_evidence"],
            f"Matched facility address: {proposal['matched_address']}.",
            f"CRM account {proposal['account_id']} current parent_id is blank.",
            f"Proposed parent_id: {proposal['proposed_parent_id']} ({proposal['proposed_parent_name']}).",
            (
                f"lifetime_revenue={proposal['lifetime_revenue']}, "
                f"outstanding_ar={proposal['outstanding_ar']}."
            ),
            proposal["financial_rule"],
            proposal["inference"],
        ],
        proposal["proposal"],
        True,
    )
    finding["ownership_change"] = proposal
    return finding


def _ownership_finding(match: dict, welcome_text: str) -> dict:
    account = match["crm_accounts"][0]
    proposal = findlay_proposal(account, match["website"], welcome_text)
    if proposal is not None:
        return _findlay_finding(match, proposal)
    website = match["website"]
    categories = [OWNERSHIP]
    if account["financial_hold"]:
        categories.append(INVESTIGATE)
    return _make_finding(
        "ownership",
        [website["source_url"], account["account_id"]],
        categories,
        f"{website['name']} — parent is {_parent_label(account)}",
        "not confirmed",
        "The address match is high confidence. The website does not say which parent should own the facility.",
        website,
        [account],
        [
            f"Website page {website['source_url']} lists {website['name']} and does not name a parent.",
            f"CRM parent is {_parent_label(account)}.",
            "A listing on the Bellhaven website is not evidence that parent_id should change.",
        ],
        NO_CHANGE + " Ownership is not inferred from the website listing.",
        False,
    )


def _unmatched_website_finding(facility: dict) -> dict:
    return _make_finding(
        "unmatched-website",
        [facility["source_url"]],
        [UNMATCHED_WEBSITE],
        f"{facility['name']} — no CRM address match",
        "confirmed gap",
        "No CRM facility account shares this normalized address. That does not by itself mean a new account should be created.",
        facility,
        [],
        [f"No saved CRM facility account normalizes to {facility.get('normalized_address') or facility.get('full_address')}."],
        NO_CHANGE + " A website listing alone does not authorize a new CRM account.",
        False,
    )


def _unmatched_crm_finding(account: dict) -> dict:
    return _make_finding(
        "unmatched-crm",
        [account["account_id"]],
        [UNMATCHED_CRM],
        f"{account['name']} — no website address match",
        "confirmed gap",
        "No current website facility shares this normalized address. Absence from the website does not mean the account should be inactivated.",
        None,
        [account],
        [
            f"CRM address {account['street']}, {account['city']}, {account['state']} {account['zip']} has no website address match.",
            f"Current parent is {_parent_label(account)}. Status is {account['status']}.",
        ],
        NO_CHANGE + " A missing website listing does not authorize an Inactive status.",
        False,
    )


def build_review_model(
    facilities: list[dict] | None = None,
    accounts: list[dict] | None = None,
) -> dict:
    facilities = facilities if facilities is not None else load_website_facilities()
    accounts = accounts if accounts is not None else load_crm_accounts(CRM_PATH)
    context = load_site_context()
    welcome_text = context.get("welcome_text", "")
    result = reconcile(facilities, accounts)
    account_index = _index_accounts(result)
    website_index = _index_websites(result, facilities)
    findings = []
    covered_crm: set[str] = set()
    covered_web: set[str] = set()

    for group in result["duplicate_groups"]:
        findings.append(_duplicate_finding(group))
        covered_crm.update(account["account_id"] for account in group["crm_accounts"])
        covered_web.update(facility["source_url"] for facility in group["website"])

    for match in result["outdated_names"]:
        findings.append(_name_finding(match))
        covered_crm.update(account["account_id"] for account in match["crm_accounts"])
        covered_web.add(match["website"]["source_url"])

    for match in result["low_matches"]:
        findings.append(_low_address_finding(match))
        covered_crm.update(account["account_id"] for account in match["crm_accounts"])
        covered_web.add(match["website"]["source_url"])

    for conflict in result["name_conflicts"]:
        findings.append(_address_conflict_finding(conflict, website_index, account_index))
        covered_crm.add(conflict["crm_account_id"])
        covered_web.add(conflict["website_url"])

    for match in result["one_to_one"]:
        account = match["crm_accounts"][0]
        if match["outdated_name"] or _is_bellhaven_parent(account):
            continue
        findings.append(_ownership_finding(match, welcome_text))
        covered_crm.add(account["account_id"])
        covered_web.add(match["website"]["source_url"])

    for facility in result["unmatched_website"]:
        if facility["source_url"] in covered_web:
            continue
        findings.append(_unmatched_website_finding(facility))

    for account in result["unmatched_crm"]:
        if account["account_id"] in covered_crm:
            continue
        findings.append(_unmatched_crm_finding(account))

    ids = [finding["finding_id"] for finding in findings]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Review finding IDs are not unique")
    findings.sort(key=lambda finding: (finding["categories"][0], finding["title"], finding["finding_id"]))

    confirmed = []
    for match in result["one_to_one"]:
        account = match["crm_accounts"][0]
        if match["outdated_name"]:
            continue
        confirmed.append(account)

    return {
        "findings": findings,
        "summary": {
            "website_facilities": result["counts"]["website_facilities"],
            "crm_accounts": result["counts"]["crm_accounts_loaded"],
            "confirmed_matches": len(confirmed),
            "ambiguous_matches": sum(INVESTIGATE in finding["categories"] for finding in findings),
            "potential_duplicates": result["counts"]["possible_duplicate_groups"],
            "confirmed_financial_holds": [
                _crm_public(account)
                for account in confirmed
                if account["financial_hold"] and _is_bellhaven_parent(account)
            ],
        },
        "acquisition": acquisition_context(result, context.get("about_text", "")),
    }
