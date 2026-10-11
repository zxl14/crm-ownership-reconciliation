"""Human-readable reconciliation report. No CRM writes are described as completed."""

from __future__ import annotations

from collections import Counter


def _cell(value) -> str:
    return str(value).replace("|", "/").replace("\n", " ")


def _table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return "_None._\n"
    head = "| " + " | ".join(headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return "\n".join([head, rule, *body]) + "\n"


def _crm_line(account: dict) -> str:
    hold = "yes" if account["financial_hold"] else "no"
    parent = account["parent_name"] or "(blank)"
    return (
        f"- `{account['account_id']}` {account['name']} | {account['street']}, "
        f"{account['city']}, {account['state']} {account['zip']} | status {account['status']} | "
        f"parent {parent} | revenue {account['lifetime_revenue']} | "
        f"AR {account['outstanding_ar']} | financial hold {hold}"
    )


def render_report(scrape: dict, result: dict) -> str:
    counts = result["counts"]
    lines = [
        "# Bellhaven facility reconciliation",
        "",
        "This is a read-only comparison. No CRM record was created or updated, and no parent change is proposed.",
        "",
        "## Method",
        "",
        "Website facilities come from every directory page and from each facility detail page. The homepage is included because its facility count and links are not limited to the directory. A facility is confidently matched only when the normalized street, city, state, and ZIP are the same.",
        "",
        "Normalization lowercases text, removes punctuation, and collapses whitespace. Whole words are expanded: St/Street, Rd/Road, Ave/Avenue, Blvd/Boulevard, Dr/Drive, Ln/Lane, W/West, N/North, S/South, E/East, and Pk/Pike. Pk is mapped to Pike because the CRM itself uses both spellings for Wilmington Pike. The same normalized name supports a match, but it is not enough to declare one.",
        "",
        "A second, low-confidence bucket is used only when the normalized name, city, state, and ZIP agree and the street does not. Those rows are questions, not confident matches.",
        "",
        "The six CRM records whose names end in `(Parent Account)` are parents and are excluded from facility matching. A page on the Bellhaven website does not identify a parent organization. Appearing there is not evidence that `parent_id` should change. The About page mentions Harborview Care Group and Cedar Trail, but it does not list the facilities in those transactions.",
        "",
        "## What was collected",
        "",
        f"- Directory pages read: {scrape['directory_pages']}",
        f"- Directory heading count: {scrape['directory_reported_count']}",
        f"- Homepage community count: {scrape['homepage_reported_count']}",
        f"- Detail pages saved: {counts['website_facilities']}",
        f"- CRM accounts loaded from the local file: {counts['crm_accounts_loaded']}",
        f"- Parent accounts excluded: {counts['parent_accounts_excluded']}",
        f"- CRM facility accounts compared: {counts['crm_facility_accounts']}",
        "",
        "About page, quoted as background rather than facility evidence:",
        "",
        f"> {scrape['about_text']}",
        "",
        "## Counts",
        "",
        "| Result | Count |",
        "| --- | ---: |",
        f"| High-confidence website facilities | {counts['high_confidence_website_facilities']} |",
        f"| High-confidence one-to-one matches | {counts['high_confidence_one_to_one']} |",
        f"| High-confidence facilities matching more than one CRM account | {counts['high_confidence_one_to_many']} |",
        f"| One-to-one matches with a different CRM name | {counts['outdated_crm_names']} |",
        f"| CRM address groups that may be duplicates | {counts['possible_duplicate_groups']} |",
        f"| CRM accounts inside those groups | {counts['crm_accounts_in_duplicate_groups']} |",
        f"| Low-confidence website facilities | {counts['low_confidence_website_facilities']} |",
        f"| Unmatched website facilities | {counts['unmatched_website_facilities']} |",
        f"| Unmatched CRM facility accounts | {counts['unmatched_crm_facilities']} |",
        f"| Facility accounts with lifetime revenue and outstanding AR both greater than 0 | {counts['financial_hold_facility_accounts']} |",
        f"| Of those, also tied to a high-confidence website match | {counts['financial_hold_with_high_website_match']} |",
        "",
        "Each website facility is in exactly one of high confidence, low confidence, or unmatched. Each CRM facility account is in exactly one of those outcomes. Accounts that share an address are also listed under possible duplicates. If no website facility uses that address, they remain in the unmatched CRM list.",
        "",
        "## Outdated names",
        "",
        "These are one-to-one address matches whose normalized names differ. The shared address is the evidence. The name difference is a discrepancy for a reviewer, not an applied correction.",
        "",
    ]
    if not result["outdated_names"]:
        lines.append("_None._\n")
    for match in result["outdated_names"]:
        website = match["website"]
        crm = match["crm_accounts"][0]
        lines.append(f"### {website['name']}")
        lines.append("")
        lines.append(f"- Website: {website['full_address']} ({website['source_url']})")
        lines.append(f"- CRM: `{crm['account_id']}` {crm['name']}")
        lines.append(f"- CRM address: {crm['street']}, {crm['city']}, {crm['state']} {crm['zip']}")
        lines.append(f"- Normalized address: {website['normalized_address']}")
        lines.append(f"- CRM status: {crm['status']}; parent: {crm['parent_name'] or '(blank)'}")
        lines.append(f"- Website care offerings: {', '.join(website['care_offerings']) or '(none)'}")
        lines.append(f"- CRM care type: {crm['care_type'] or '(blank)'}")
        lines.append(f"- Financial hold if a parent change is later proposed: {'yes' if crm['financial_hold'] else 'no'}")
        for item in match["evidence"]:
            lines.append(f"- Evidence: {item}")
        lines.append("")

    lines.extend(["## Possible duplicate CRM accounts", ""])
    if not result["duplicate_groups"]:
        lines.append("_None._\n")
    for group in result["duplicate_groups"]:
        lines.append(f"### {group['normalized_address']}")
        lines.append("")
        lines.append(f"- Confidence that these records represent one facility: {group['confidence']}")
        websites = group["website"]
        if websites:
            for website in websites:
                lines.append(f"- Website facility: {website['name']} ({website['source_url']})")
        else:
            lines.append("- No current website facility has this normalized address.")
        for item in group["evidence"]:
            lines.append(f"- Evidence: {item}")
        for account in group["crm_accounts"]:
            lines.append(_crm_line(account))
        lines.append("")

    lines.extend(
        [
            "## Low-confidence comparisons",
            "",
            "Name, city, state, and ZIP agree, and the street does not. These are not matches.",
            "",
        ]
    )
    if not result["low_matches"]:
        lines.append("_None._\n")
    for match in result["low_matches"]:
        website = match["website"]
        lines.append(f"### {website['name']}")
        lines.append("")
        lines.append(f"- Website address: {website['full_address']} ({website['source_url']})")
        for item in match["evidence"]:
            lines.append(f"- Evidence: {item}")
        for account in match["crm_accounts"]:
            lines.append(_crm_line(account))
        lines.append("")

    lines.extend(["## Unmatched website facilities", ""])
    if not result["unmatched_website"]:
        lines.append("_None._\n")
    else:
        rows = [
            [
                facility["name"],
                facility["full_address"],
                ", ".join(facility["care_offerings"]),
                "directory" if facility["listed_in_directory"] else "homepage only",
                facility["source_url"],
            ]
            for facility in result["unmatched_website"]
        ]
        lines.append(_table(["Name", "Address", "Care offerings", "Found on", "Source"], rows))
        lines.append("")

    lines.extend(
        [
            "## Unmatched CRM facility accounts",
            "",
            "These accounts have no high- or low-confidence website comparison. Absence from this website is not a finding that the account should become Inactive or change parent. Counts below are grouped by the parent currently stored in the CRM.",
            "",
        ]
    )
    parent_counts = Counter(row["parent_name"] or "(blank)" for row in result["unmatched_crm"])
    lines.append(_table(["Current CRM parent", "Unmatched facility accounts"], [[name, count] for name, count in parent_counts.most_common()]))
    lines.append("")
    for account in result["unmatched_crm"]:
        lines.append(_crm_line(account))
    lines.append("")

    lines.extend(
        [
            "## Same name, different address",
            "",
            "These pairs share a normalized name and were not given an address match. They remain in the unmatched or low-confidence sections above. Identical names were not treated as the same facility.",
            "",
        ]
    )
    if not result["name_conflicts"]:
        lines.append("_None._\n")
    else:
        rows = [
            [
                item["website_name"],
                item["website_address"],
                item["crm_account_id"],
                item["crm_address"],
            ]
            for item in result["name_conflicts"]
        ]
        lines.append(_table(["Website name", "Website address", "CRM account", "CRM address"], rows))
        lines.append("")

    lines.extend(
        [
            "## Financial hold",
            "",
            "If a later review proposes changing one of these accounts' `parent_id`, the account and its current parent must be preserved. A new facility account would be created under the correct parent, and `chow_current_account` on the old account would point at the new account. This report does not propose those parent changes.",
            "",
        ]
    )
    rows = [
        [
            account["account_id"],
            account["name"],
            account["parent_name"] or "(blank)",
            account["status"],
            account["lifetime_revenue"],
            account["outstanding_ar"],
            "yes" if account["account_id"] in {
                crm["account_id"]
                for match in result["high_matches"]
                for crm in match["crm_accounts"]
            } else "no",
        ]
        for account in result["financial_holds"]
    ]
    lines.append(_table(
        ["Account", "Name", "Current parent", "Status", "Lifetime revenue", "Outstanding AR", "High-confidence website match"],
        rows,
    ))
    lines.append("")

    lines.extend(
        [
            "## Ownership",
            "",
            "No ownership update is confirmed.",
            "",
            "- Confirmed: facility detail pages publish name, address, care offerings, administrator, and phone. They do not publish a parent account.",
            "- Confirmed: the About page says Harborview Care Group joined in 2025 and selected communities joined from Cedar Trail in 2026. It names neither the communities nor a CRM parent id.",
            "- Not assumed: a facility listed on the Bellhaven website should be re-parented to Bellhaven Senior Living.",
            "- Not assumed: a CRM facility missing from the website has closed, should be inactivated, or should leave its current parent.",
            "- Current parent names on matched accounts are CRM facts. They are shown with each match as context for a reviewer.",
            "",
            "## Unresolved questions",
            "",
            "- Which account should survive in each high-confidence duplicate group? This pass does not set `duplicate_of_account`.",
            "- For same-address groups whose names differ, are the records duplicates or separate care programs on one campus?",
            "- Should any matched account change parent? The website extract does not answer that.",
            "- The website care labels and CRM `care_type` values are different vocabularies. No crosswalk was supplied, so Memory Support is not treated as confirmed Memory Care, and Short-Term Rehabilitation & Nursing is not treated as confirmed Skilled Nursing.",
            "- The CRM status values observed in the saved accounts are Active and Inactive. Needs Review was not present. Whether a later update should use it is a reviewer decision.",
            "- Low-confidence street disagreements, including a street address compared with a PO Box, need a person to decide whether the records are the same facility.",
            (
                "- Some low-confidence candidates pointed at more than one website facility and were left unmatched: "
                + ", ".join(f"`{account_id}`" for account_id in result["ambiguous_low_confidence_crm_ids"])
                + "."
                if result["ambiguous_low_confidence_crm_ids"]
                else "- No CRM account was a low-confidence candidate for more than one website facility."
            ),
            "- Homepage text says 35 communities and the directory says 34. Findlay is the extra detail page. That explains the counts; it does not assign Findlay's parent.",
            "- Bellhaven of Portsmouth has the same normalized name and street in both sources, but the ZIP codes differ: website 45662 and CRM 45626. It was not treated as the same facility.",
            "- Amberly Manor on the website is in Hudson, OH. A CRM account with the same normalized name is in Colorado Springs, CO. The shared name was not treated as a match.",
            "",
        ]
    )
    return "\n".join(lines)
