"""Match website facilities to CRM facility accounts.

A confident match requires the same normalized street, city, state, and ZIP.
The same normalized name is supporting evidence only. It never identifies a
facility by itself, and a Bellhaven website listing is not evidence that the
CRM parent should change.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from src.normalize import (
    address_key,
    format_address_key,
    normalize_city,
    normalize_name,
    normalize_state,
    normalize_zip,
    phone_digits,
)

PARENT_SUFFIX = "(Parent Account)"


def load_crm_accounts(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("CRM account file must be a list saved by the read-only fetch")
    return payload


def is_parent_account(account: dict) -> bool:
    return str(account.get("name", "")).endswith(PARENT_SUFFIX)


def _amount(account: dict, field: str) -> int:
    value = account.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"{account.get('account_id')} has a non-numeric {field}")
    return int(value)


def financial_hold(account: dict) -> bool:
    return _amount(account, "lifetime_revenue") > 0 and _amount(account, "outstanding_ar") > 0


def _crm_view(account: dict) -> dict:
    key = address_key(
        account.get("billing_street", ""),
        account.get("billing_city", ""),
        account.get("billing_state", ""),
        account.get("billing_zip", ""),
    )
    return {
        "account_id": account["account_id"],
        "name": account.get("name", ""),
        "normalized_name": normalize_name(account.get("name", "")),
        "street": account.get("billing_street", ""),
        "city": account.get("billing_city", ""),
        "state": account.get("billing_state", ""),
        "zip": account.get("billing_zip", ""),
        "address_key": key,
        "normalized_address": format_address_key(key),
        "care_type": account.get("care_type", ""),
        "status": account.get("status", ""),
        "parent_id": account.get("parent_id", ""),
        "parent_name": account.get("parent_name", ""),
        "phone": account.get("phone", ""),
        "lifetime_revenue": _amount(account, "lifetime_revenue"),
        "outstanding_ar": _amount(account, "outstanding_ar"),
        "financial_hold": financial_hold(account),
        "duplicate_of_account": account.get("duplicate_of_account", ""),
        "chow_current_account": account.get("chow_current_account", ""),
    }


def _website_view(facility: dict) -> dict:
    key = address_key(
        facility.get("street", ""),
        facility.get("city", ""),
        facility.get("state", ""),
        facility.get("zip", ""),
    )
    return {
        **facility,
        "normalized_name": normalize_name(facility.get("name", "")),
        "address_key": key,
        "normalized_address": format_address_key(key),
    }


def _names_agree(website: dict, crm: dict) -> bool:
    return bool(website["normalized_name"]) and website["normalized_name"] == crm["normalized_name"]


def _phone_note(website: dict, crm: dict) -> str:
    left = phone_digits(website.get("phone", ""))
    right = phone_digits(crm.get("phone", ""))
    if not left or not right:
        return "Phone is missing on one side, so it was not compared."
    if left == right:
        return "Phone digits match. This supports the address match and was not required for it."
    return "Phone digits differ. The address match is unchanged; the phone difference is unresolved."


def _pair_evidence(website: dict, crm: dict, confidence: str) -> list[str]:
    evidence = [
        (
            f"{confidence} confidence: website {website['full_address']} and CRM "
            f"{crm['street']}, {crm['city']}, {crm['state']} {crm['zip']} "
            f"normalize to {crm['normalized_address'] or website['normalized_address']}."
        )
    ]
    if _names_agree(website, crm):
        evidence.append(f"Normalized names agree: {website['name']}.")
    else:
        evidence.append(
            f"Normalized names differ. Website: {website['name']}. CRM: {crm['name']}."
        )
    evidence.append(_phone_note(website, crm))
    evidence.append(
        "The website page does not name a parent organization. "
        f"Current CRM parent is {crm['parent_name'] or '(blank)'}."
    )
    return evidence


def reconcile(facilities: list[dict], accounts: list[dict]) -> dict:
    parents = [_crm_view(account) for account in accounts if is_parent_account(account)]
    crm_rows = [_crm_view(account) for account in accounts if not is_parent_account(account)]
    website_rows = [_website_view(facility) for facility in facilities]
    if len(parents) != 6:
        raise RuntimeError(f"Expected 6 parent accounts excluded from matching, found {len(parents)}")

    by_address: dict[tuple, list[dict]] = defaultdict(list)
    for row in crm_rows:
        if row["address_key"] is not None:
            by_address[row["address_key"]].append(row)

    claimed: set[str] = set()
    high_matches = []
    for website in website_rows:
        group = by_address.get(website["address_key"], []) if website["address_key"] else []
        if not group:
            continue
        for crm in group:
            claimed.add(crm["account_id"])
        same_name = sum(_names_agree(website, crm) for crm in group)
        high_matches.append(
            {
                "confidence": "high",
                "website": website,
                "crm_accounts": group,
                "names_agree_count": same_name,
                "outdated_name": len(group) == 1 and same_name == 0,
                "possible_duplicate": len(group) > 1,
                "evidence": [
                    item
                    for crm in group
                    for item in _pair_evidence(website, crm, "High")
                ],
            }
        )

    duplicate_groups = []
    for key, group in sorted(by_address.items(), key=lambda item: format_address_key(item[0])):
        if len(group) < 2:
            continue
        names = {row["normalized_name"] for row in group}
        website_hits = [
            match["website"]
            for match in high_matches
            if match["website"]["address_key"] == key
        ]
        duplicate_groups.append(
            {
                "confidence": "high" if len(names) == 1 else "review",
                "normalized_address": format_address_key(key),
                "crm_accounts": group,
                "website": website_hits,
                "same_normalized_name": len(names) == 1,
                "evidence": [
                    (
                        f"{len(group)} CRM facility accounts share normalized address "
                        f"{format_address_key(key)}."
                    ),
                    (
                        "The normalized names are the same."
                        if len(names) == 1
                        else "The normalized names differ, so these records are not confirmed to be one facility. They may be distinct care programs at one campus."
                    ),
                    "No surviving account is selected in this read-only pass.",
                ],
            }
        )

    remaining_crm = [row for row in crm_rows if row["account_id"] not in claimed]
    remaining_web = [
        row
        for row in website_rows
        if row["address_key"] not in {match["website"]["address_key"] for match in high_matches}
    ]
    low_candidates: dict[str, list[dict]] = defaultdict(list)
    for website in remaining_web:
        if not website["normalized_name"] or not website.get("zip"):
            continue
        for crm in remaining_crm:
            locality_agrees = (
                website["normalized_name"] == crm["normalized_name"]
                and normalize_city(website["city"]) == normalize_city(crm["city"])
                and normalize_state(website["state"]) == normalize_state(crm["state"])
                and normalize_zip(website["zip"]) == normalize_zip(crm["zip"])
                and website["address_key"] != crm["address_key"]
            )
            if locality_agrees:
                low_candidates[website["source_url"]].append(crm)

    ambiguous_crm = set()
    owner: dict[str, str] = {}
    for url, group in low_candidates.items():
        for crm in group:
            previous = owner.get(crm["account_id"])
            if previous and previous != url:
                ambiguous_crm.add(crm["account_id"])
            owner[crm["account_id"]] = url

    low_matches = []
    low_web_urls = set()
    low_crm_ids = set()
    for website in remaining_web:
        group = [
            crm
            for crm in low_candidates.get(website["source_url"], [])
            if crm["account_id"] not in ambiguous_crm
        ]
        if not group:
            continue
        low_web_urls.add(website["source_url"])
        for crm in group:
            low_crm_ids.add(crm["account_id"])
        low_matches.append(
            {
                "confidence": "low",
                "website": website,
                "crm_accounts": group,
                "evidence": [
                    (
                        "Low confidence only: normalized name, city, state, and ZIP agree, "
                        "but the normalized street does not. This is not a confident match."
                    ),
                    f"Website street: {website['street']}.",
                    *[
                        f"CRM {crm['account_id']} street: {crm['street']}."
                        for crm in group
                    ],
                ],
                "assumptions": [
                    "Same name plus same ZIP can still be two records. No CRM change is identified from this comparison."
                ],
            }
        )

    matched_web = {match["website"]["source_url"] for match in high_matches} | low_web_urls
    matched_crm = claimed | low_crm_ids
    unmatched_website = [row for row in website_rows if row["source_url"] not in matched_web]
    unmatched_crm = [row for row in crm_rows if row["account_id"] not in matched_crm]

    name_conflicts = []
    for website in website_rows:
        for crm in crm_rows:
            if website["normalized_name"] != crm["normalized_name"]:
                continue
            if website["address_key"] and website["address_key"] == crm["address_key"]:
                continue
            if website["source_url"] in low_web_urls and crm["account_id"] in low_crm_ids:
                continue
            name_conflicts.append(
                {
                    "website_name": website["name"],
                    "website_address": website["full_address"],
                    "website_url": website["source_url"],
                    "crm_account_id": crm["account_id"],
                    "crm_name": crm["name"],
                    "crm_address": f"{crm['street']}, {crm['city']}, {crm['state']} {crm['zip']}",
                    "evidence": "Normalized names are equal and normalized addresses are not. These records were not matched.",
                }
            )

    high_crm_ids = claimed
    one_to_one = [match for match in high_matches if len(match["crm_accounts"]) == 1]
    one_to_many = [match for match in high_matches if len(match["crm_accounts"]) > 1]
    outdated = [match for match in one_to_one if match["outdated_name"]]
    counts = {
        "crm_accounts_loaded": len(accounts),
        "parent_accounts_excluded": len(parents),
        "crm_facility_accounts": len(crm_rows),
        "website_facilities": len(website_rows),
        "high_confidence_website_facilities": len(high_matches),
        "high_confidence_one_to_one": len(one_to_one),
        "high_confidence_one_to_many": len(one_to_many),
        "outdated_crm_names": len(outdated),
        "possible_duplicate_groups": len(duplicate_groups),
        "crm_accounts_in_duplicate_groups": sum(len(group["crm_accounts"]) for group in duplicate_groups),
        "low_confidence_website_facilities": len(low_matches),
        "unmatched_website_facilities": len(unmatched_website),
        "unmatched_crm_facilities": len(unmatched_crm),
        "financial_hold_facility_accounts": sum(row["financial_hold"] for row in crm_rows),
        "financial_hold_with_high_website_match": sum(
            row["financial_hold"] for row in crm_rows if row["account_id"] in high_crm_ids
        ),
    }
    if counts["high_confidence_website_facilities"] + counts["low_confidence_website_facilities"] + counts["unmatched_website_facilities"] != counts["website_facilities"]:
        raise RuntimeError("Website facilities are not partitioned across match categories")
    if len(high_crm_ids) + len(low_crm_ids) + counts["unmatched_crm_facilities"] != counts["crm_facility_accounts"]:
        raise RuntimeError("CRM facility accounts are not partitioned across match categories")

    return {
        "parents": parents,
        "counts": counts,
        "high_matches": high_matches,
        "one_to_one": one_to_one,
        "outdated_names": outdated,
        "duplicate_groups": duplicate_groups,
        "low_matches": low_matches,
        "unmatched_website": unmatched_website,
        "unmatched_crm": unmatched_crm,
        "name_conflicts": name_conflicts,
        "financial_holds": [row for row in crm_rows if row["financial_hold"]],
        "ambiguous_low_confidence_crm_ids": sorted(ambiguous_crm),
    }
