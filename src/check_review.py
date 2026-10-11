"""Checks for the local review screen. No CRM requests are made."""

from __future__ import annotations

from pathlib import Path

from src.findings import (
    ADDRESS_DISCREPANCY,
    CRM_PATH,
    INVESTIGATE,
    NAME_DISCREPANCY,
    OWNERSHIP,
    POSSIBLE_DUPLICATE,
    UNMATCHED_CRM,
    UNMATCHED_WEBSITE,
    build_review_model,
)
from src.ownership import BELLHAVEN_PARENT_ID, FINDLAY_ACCOUNT, MARIETTA_ACCOUNT, TIFFIN_ACCOUNT
from src.reconcile import load_crm_accounts
from src.review_store import decision_view, load_store, save_decision

ROOT = Path(__file__).resolve().parents[1]


def _check_model(model: dict) -> None:
    findings = model["findings"]
    ids = [finding["finding_id"] for finding in findings]
    assert len(ids) == len(set(ids))
    assert all(finding["executable"] is False for finding in findings)
    assert all("POST" not in finding["proposal"] and "PATCH" not in finding["proposal"] for finding in findings)
    for finding in findings:
        if NAME_DISCREPANCY in finding["categories"]:
            assert finding["proposal_justified"] is False
            assert "rename" in finding["proposal"].lower()
        if UNMATCHED_WEBSITE in finding["categories"] or UNMATCHED_CRM in finding["categories"]:
            assert finding["proposal_justified"] is False
        if OWNERSHIP in finding["categories"] and POSSIBLE_DUPLICATE not in finding["categories"]:
            assert finding["proposal_justified"] is False
            assert "parent_id should change" not in finding["proposal"].lower()
            assert "set parent_id" not in finding["proposal"].lower()
    duplicates = [finding for finding in findings if POSSIBLE_DUPLICATE in finding["categories"]]
    assert len(duplicates) == model["summary"]["potential_duplicates"] == 6
    justified = [finding for finding in duplicates if finding["proposal_justified"]]
    assert justified == []
    owosso = next(
        finding
        for finding in duplicates
        if {account["account_id"] for account in finding["crm_accounts"]}
        == {"001EGU7BMJ942ZTRE6", "001QU150PM4Z15UA71"}
    )
    assert owosso["proposal_justified"] is False
    assert owosso["executable"] is False
    assert "Already applied" in owosso["proposal"]
    assert "001QU150PM4Z15UA71" in owosso["proposal"]
    saved = {account["account_id"]: account for account in load_crm_accounts(CRM_PATH)}
    findlay = saved[FINDLAY_ACCOUNT]
    assert findlay["parent_id"] == BELLHAVEN_PARENT_ID
    assert findlay["status"] == "Active"
    assert findlay["lifetime_revenue"] == 22000
    assert findlay["outstanding_ar"] == 0
    assert not any(
        FINDLAY_ACCOUNT in {account["account_id"] for account in finding["crm_accounts"]}
        and finding["proposal_justified"]
        for finding in findings
    )
    holds = {item["account_id"] for item in model["acquisition"]["financial_holds"]}
    assert holds == {MARIETTA_ACCOUNT, TIFFIN_ACCOUNT}
    assert all(item["financial_hold"] for item in model["acquisition"]["financial_holds"])
    assert "not confirmed" in model["acquisition"]["financial_hold_note"].lower()
    assert model["acquisition"]["investigation_addresses"]
    assert "investigation-only" in model["acquisition"]["investigation_note"]
    assert model["summary"]["website_facilities"] == 35
    assert model["summary"]["crm_accounts"] == 121
    assert model["summary"]["confirmed_matches"] > 0
    assert any(ADDRESS_DISCREPANCY in finding["categories"] and INVESTIGATE in finding["categories"] for finding in findings)


def _check_persistence(model: dict, path: Path) -> None:
    finding = model["findings"][0]
    save_decision(finding, "approve", "check", "local only", path)
    status = decision_view(finding, load_store(path))
    assert status["decision"] == "approve"
    changed = {**finding, "evidence_hash": "different-evidence"}
    reopened = decision_view(changed, load_store(path))
    assert reopened["decision"] == "undecided"
    assert reopened["stale_decision"]["decision"] == "approve"
    again = build_review_model()
    assert [item["finding_id"] for item in again["findings"]] == [item["finding_id"] for item in model["findings"]]
    reloaded = next(item for item in again["findings"] if item["finding_id"] == finding["finding_id"])
    assert reloaded["evidence_hash"] == finding["evidence_hash"]
    assert decision_view(reloaded, load_store(path))["decision"] == "approve"


def _check_app_source() -> None:
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    review = (ROOT / "src" / "review_store.py").read_text(encoding="utf-8")
    findings = (ROOT / "src" / "findings.py").read_text(encoding="utf-8")
    for text in (source, review, findings):
        lowered = text.lower()
        assert ".post(" not in lowered
        assert ".patch(" not in lowered
        assert "requests" not in lowered


def _check_ui() -> None:
    from streamlit.testing.v1 import AppTest

    owosso_id = next(
        finding["finding_id"]
        for finding in build_review_model()["findings"]
        if {account["account_id"] for account in finding["crm_accounts"]}
        == {"001EGU7BMJ942ZTRE6", "001QU150PM4Z15UA71"}
    )
    findlay_id = next(
        (
            finding["finding_id"]
            for finding in build_review_model()["findings"]
            if any(account["account_id"] == FINDLAY_ACCOUNT for account in finding["crm_accounts"])
            and finding["proposal_justified"]
        ),
        None,
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    app.run()
    assert not app.exception, app.exception
    assert app.metric
    assert any("CRM writes are disabled" in element.value for element in app.info)
    app.selectbox[0].set_value(owosso_id).run()
    assert not app.exception, app.exception
    assert len(app.dataframe) >= 2
    visible = []
    for name in ("markdown", "text", "code", "caption", "info", "success"):
        for element in getattr(app, name, []):
            visible.append(getattr(element, "value", "") or "")
    assert any("already in the CRM" in text for text in visible)
    assert findlay_id is None


def main() -> None:
    model = build_review_model()
    _check_model(model)
    _check_persistence(model, ROOT / "data" / "review" / "check-decisions.json")
    _check_app_source()
    _check_ui()
    summary = model["summary"]
    print(f"findings={len(model['findings'])}")
    print(f"website_facilities={summary['website_facilities']}")
    print(f"crm_accounts={summary['crm_accounts']}")
    print(f"confirmed_matches={summary['confirmed_matches']}")
    print(f"ambiguous_matches={summary['ambiguous_matches']}")
    print(f"potential_duplicates={summary['potential_duplicates']}")
    print(f"confirmed_financial_holds={len(summary['confirmed_financial_holds'])}")
    print("checks=ok")


if __name__ == "__main__":
    main()
