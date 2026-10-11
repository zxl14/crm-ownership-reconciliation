"""Scrape Bellhaven facilities and reconcile them with the saved CRM file."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.normalize import check_normalizer
from src.reconcile import load_crm_accounts, reconcile
from src.report import render_report
from src.scrape_website import FINDLAY_SLUG, scrape_facilities
from src.site_context import CONTEXT_PATH

ROOT = Path(__file__).resolve().parents[1]
CRM_PATH = ROOT / "data" / "crm" / "accounts.json"
WEBSITE_DIR = ROOT / "data" / "website"
REPORT_DIR = ROOT / "data" / "reconciliation"


def _public_facility(facility: dict) -> dict:
    return {
        "name": facility["name"],
        "street": facility["street"],
        "city": facility["city"],
        "state": facility["state"],
        "zip": facility["zip"],
        "full_address": facility["full_address"],
        "care_offerings": facility["care_offerings"],
        "source_url": facility["source_url"],
        "listed_in_directory": facility["listed_in_directory"],
        "address_parsed": facility["address_parsed"],
    }


def _flatten_matches(result: dict) -> list[dict]:
    rows = []

    def add(category: str, confidence: str, website: dict | None, crm: dict | None, evidence: str) -> None:
        rows.append(
            {
                "category": category,
                "confidence": confidence,
                "website_name": "" if website is None else website["name"],
                "website_address": "" if website is None else website["full_address"],
                "website_city": "" if website is None else website["city"],
                "website_state": "" if website is None else website["state"],
                "website_zip": "" if website is None else website["zip"],
                "website_care_offerings": "" if website is None else "; ".join(website["care_offerings"]),
                "website_url": "" if website is None else website["source_url"],
                "crm_account_id": "" if crm is None else crm["account_id"],
                "crm_name": "" if crm is None else crm["name"],
                "crm_street": "" if crm is None else crm["street"],
                "crm_city": "" if crm is None else crm["city"],
                "crm_state": "" if crm is None else crm["state"],
                "crm_zip": "" if crm is None else crm["zip"],
                "crm_care_type": "" if crm is None else crm["care_type"],
                "crm_status": "" if crm is None else crm["status"],
                "crm_parent_name": "" if crm is None else crm["parent_name"],
                "lifetime_revenue": "" if crm is None else crm["lifetime_revenue"],
                "outstanding_ar": "" if crm is None else crm["outstanding_ar"],
                "financial_hold": "" if crm is None else crm["financial_hold"],
                "normalized_address": (
                    ""
                    if website is None
                    else website.get("normalized_address", "")
                ),
                "evidence": evidence,
            }
        )

    for match in result["high_matches"]:
        category = "possible_duplicate" if match["possible_duplicate"] else "confident_match"
        if match["outdated_name"]:
            category = "outdated_name"
        for crm in match["crm_accounts"]:
            add(category, "high", match["website"], crm, " ".join(match["evidence"]))
    for match in result["low_matches"]:
        for crm in match["crm_accounts"]:
            add("low_confidence_name_and_zip", "low", match["website"], crm, " ".join(match["evidence"]))
    for website in result["unmatched_website"]:
        add("unmatched_website", "none", website, None, "No CRM facility account has this normalized address.")
    for crm in result["unmatched_crm"]:
        add("unmatched_crm", "none", None, crm, "No website facility has this normalized address, name, city, and ZIP.")
    return rows


def _json_ready(value):
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return list(value)
    return value


def main() -> None:
    check_normalizer()
    if not CRM_PATH.exists():
        raise SystemExit(f"Missing saved CRM file: {CRM_PATH}")

    scrape = scrape_facilities()
    CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONTEXT_PATH.write_text(
        json.dumps(
            {
                "homepage_url": "https://analyst-assessment-production.up.railway.app/",
                "about_url": "https://analyst-assessment-production.up.railway.app/about",
                "welcome_text": scrape.get("homepage_welcome", ""),
                "about_text": scrape.get("about_text", ""),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    facilities = scrape["facilities"]
    directory_count = sum(facility["listed_in_directory"] for facility in facilities)
    slugs = {facility["slug"] for facility in facilities}
    unparsed = [facility["name"] for facility in facilities if not facility["address_parsed"]]
    if directory_count != 34 or FINDLAY_SLUG not in slugs or len(facilities) != 35:
        raise SystemExit(
            f"Expected 34 directory facilities plus Findlay, found {directory_count} directory and {len(facilities)} total"
        )
    if unparsed:
        raise SystemExit("Address parse failed for: " + ", ".join(unparsed))

    WEBSITE_DIR.mkdir(parents=True, exist_ok=True)
    public_rows = [_public_facility(facility) for facility in facilities]
    (WEBSITE_DIR / "facilities.json").write_text(
        json.dumps(
            {
                "directory_pages": scrape["directory_pages"],
                "directory_reported_count": scrape["directory_reported_count"],
                "homepage_reported_count": scrape["homepage_reported_count"],
                "facilities": public_rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    pd.DataFrame(public_rows).to_csv(WEBSITE_DIR / "facilities.csv", index=False)

    result = reconcile(facilities, load_crm_accounts(CRM_PATH))
    report = render_report(scrape, result)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "report.md").write_text(report, encoding="utf-8")
    (REPORT_DIR / "reconciliation.json").write_text(
        json.dumps(_json_ready({"counts": result["counts"], "result": result}), indent=2),
        encoding="utf-8",
    )
    pd.DataFrame(_flatten_matches(result)).to_csv(REPORT_DIR / "reconciliation.csv", index=False)

    counts = result["counts"]
    print(f"website_facilities={counts['website_facilities']}")
    print(f"high_confidence={counts['high_confidence_website_facilities']}")
    print(f"one_to_one={counts['high_confidence_one_to_one']}")
    print(f"one_to_many={counts['high_confidence_one_to_many']}")
    print(f"outdated_names={counts['outdated_crm_names']}")
    print(f"duplicate_groups={counts['possible_duplicate_groups']}")
    print(f"low_confidence={counts['low_confidence_website_facilities']}")
    print(f"unmatched_website={counts['unmatched_website_facilities']}")
    print(f"unmatched_crm={counts['unmatched_crm_facilities']}")
    print(f"financial_holds={counts['financial_hold_facility_accounts']}")
    print(f"report={REPORT_DIR / 'report.md'}")


if __name__ == "__main__":
    main()
