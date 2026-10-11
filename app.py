"""Local Streamlit review screen. Approval does not call the CRM."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.crm_read import load_findlay_snapshot, load_owosso_snapshot, save_owosso_snapshot
from src.crm_refresh import refresh_accounts
from src.findings import CATEGORIES, build_review_model
from src.ownership import build_findlay_change
from src.proposals import assess_finding
from src.review_store import DECISIONS_PATH, decision_view, load_store, save_decision
from src.site_context import refresh_site_context
from src.writeback import (
    OWOSSO_IDS,
    RECOMMENDATION,
    RECOMMENDED_DUPLICATE,
    RECOMMENDED_SURVIVOR,
    apply_approved_change,
    apply_findlay_change,
    build_change,
    load_approvals,
    save_executable_approval,
    writes_enabled,
)

DECISION_LABELS = {
    "undecided": "Undecided",
    "approve": "Approved",
    "reject": "Rejected",
    "needs_investigation": "Needs investigation",
}


@st.cache_data
def _model(website_mtime: float, crm_mtime: float, context_mtime: float) -> dict:
    del website_mtime, crm_mtime, context_mtime
    return build_review_model()


def _annotated(model: dict, store: dict) -> list[dict]:
    rows = []
    for finding in model["findings"]:
        status = decision_view(finding, store)
        rows.append({**finding, **status})
    return rows


def _metrics(summary: dict, findings: list[dict]) -> None:
    unresolved = sum(finding["decision"] == "undecided" for finding in findings)
    columns = st.columns(6)
    figures = [
        ("Website facilities", summary["website_facilities"]),
        ("CRM accounts", summary["crm_accounts"]),
        ("Confirmed matches", summary["confirmed_matches"]),
        ("Ambiguous matches", summary["ambiguous_matches"]),
        ("Potential duplicates", summary["potential_duplicates"]),
        ("Unresolved records", unresolved),
    ]
    for column, (label, value) in zip(columns, figures):
        column.metric(label, value)


def _display_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Copy a frame into string columns so Arrow can serialize mixed display values."""
    display = frame.copy()
    for column in display.columns:
        display[column] = ["" if value is None else str(value) for value in display[column]]
    return display


def _table(findings: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Finding": finding["title"],
                "Categories": ", ".join(finding["categories"]),
                "Confidence": finding["confidence"],
                "Decision": DECISION_LABELS.get(finding["decision"], finding["decision"]),
                "Financial hold": "Yes" if finding["financial_hold"] else "No",
                "Account IDs": ", ".join(account["account_id"] for account in finding["crm_accounts"]) or "—",
            }
            for finding in findings
        ]
    )


def _filter(findings: list[dict]) -> list[dict]:
    st.sidebar.header("Filters")
    categories = st.sidebar.multiselect("Category", CATEGORIES, default=CATEGORIES)
    statuses = st.sidebar.multiselect(
        "Decision",
        list(DECISION_LABELS),
        default=list(DECISION_LABELS),
        format_func=lambda value: DECISION_LABELS[value],
    )
    query = st.sidebar.text_input("Search name, city, or account ID").strip().casefold()
    chosen = []
    for finding in findings:
        if not set(finding["categories"]).intersection(categories):
            continue
        if finding["decision"] not in statuses:
            continue
        haystack = " ".join(
            [
                finding["title"],
                " ".join(finding["categories"]),
                " ".join(account["account_id"] for account in finding["crm_accounts"]),
                " ".join(account["name"] for account in finding["crm_accounts"]),
                "" if finding["website"] is None else finding["website"]["full_address"],
            ]
        ).casefold()
        if query and query not in haystack:
            continue
        chosen.append(finding)
    return chosen


def _detail(finding: dict, reviewer: str) -> None:
    st.subheader(finding["title"])
    kind = "Described change, not sent" if finding["proposal_justified"] else "Observation"
    st.write(f"**Record type:** {kind}")
    st.write(f"**Confidence:** {finding['confidence']}")
    st.write(f"**Uncertainty:** {finding['uncertainty']}")
    st.write("**Categories:** " + ", ".join(finding["categories"]))

    left, right = st.columns(2)
    with left:
        st.markdown("**Website**")
        website = finding["website"]
        if website is None:
            st.write("No website facility is linked to this finding.")
        else:
            st.write(website["name"])
            st.write(website["full_address"])
            st.write("Care offerings: " + (", ".join(website["care_offerings"]) or "(none)"))
            st.write(website["source_url"])
    with right:
        st.markdown("**Current CRM**")
        if not finding["crm_accounts"]:
            st.write("No CRM facility account is linked to this finding.")
        for account in finding["crm_accounts"]:
            st.write(f"`{account['account_id']}` {account['name']}")
            st.write(
                f"{account['street']}, {account['city']}, {account['state']} {account['zip']}"
            )
            st.write(
                f"Status {account['status']}; parent {account['parent_name'] or '(blank)'}; "
                f"care type {account['care_type'] or '(blank)'}"
            )

    st.markdown("**Evidence**")
    for line in finding["evidence"]:
        st.write("- " + line)

    st.markdown("**Proposed change**")
    st.write(finding["proposal"])
    st.write("Approve finding stores a review decision only. It does not authorize a CRM write.")

    st.markdown("**Financial risk**")
    if finding["financial_hold"]:
        st.warning("At least one linked account has lifetime revenue and outstanding AR both above zero.")
    else:
        st.write("No linked account has both lifetime revenue and outstanding AR above zero.")

    if finding["stale_decision"]:
        stale = finding["stale_decision"]
        st.warning(
            "A previous decision ("
            + DECISION_LABELS.get(stale["decision"], stale["decision"])
            + " at "
            + stale["decided_at"]
            + ") was for different evidence. This finding is undecided again."
        )
    elif finding["decision"] != "undecided":
        st.success(
            DECISION_LABELS[finding["decision"]]
            + " by "
            + (finding["reviewer"] or reviewer)
            + " at "
            + finding["decided_at"]
        )

    note = st.text_area("Note", value=finding["note"], key=f"note-{finding['finding_id']}")
    approve, reject, investigate = st.columns(3)
    choice = None
    if approve.button("Approve finding", key=f"approve-{finding['finding_id']}"):
        choice = "approve"
    if reject.button("Reject", key=f"reject-{finding['finding_id']}"):
        choice = "reject"
    if investigate.button("Needs Investigation", key=f"investigate-{finding['finding_id']}"):
        choice = "needs_investigation"
    if choice:
        save_decision(finding, choice, reviewer, note, DECISIONS_PATH)
        st.rerun()
    _executable_change(finding, reviewer)


def _contacts(snapshot: dict, account_id: str) -> list[dict]:
    value = snapshot["contacts"][account_id]
    if isinstance(value, dict) and isinstance(value.get("data"), list):
        return value["data"]
    return value


def _comparison(snapshot: dict) -> None:
    website = snapshot.get("website") or {}
    survivor = snapshot["accounts"][RECOMMENDED_SURVIVOR]
    duplicate = snapshot["accounts"][RECOMMENDED_DUPLICATE]
    survivor_contacts = _contacts(snapshot, RECOMMENDED_SURVIVOR)
    duplicate_contacts = _contacts(snapshot, RECOMMENDED_DUPLICATE)

    def contact_text(rows: list[dict]) -> str:
        if not rows:
            return "(none)"
        return "; ".join(
            f"{row.get('name', '')}, {row.get('title', '')}, {row.get('phone', '')}, {row.get('email', '') or '(no email)'}"
            for row in rows
        )

    rows = [
        ["Street", website.get("full_address", ""), survivor["billing_street"], duplicate["billing_street"]],
        ["Phone", website.get("phone", ""), survivor["phone"], duplicate["phone"]],
        ["Care", ", ".join(website.get("care_offerings") or []), survivor["care_type"], duplicate["care_type"]],
        ["Status", "", survivor["status"], duplicate["status"]],
        ["Parent", "", survivor["parent_name"], duplicate["parent_name"]],
        ["Lifetime revenue", "", survivor["lifetime_revenue"], duplicate["lifetime_revenue"]],
        ["Outstanding AR", "", survivor["outstanding_ar"], duplicate["outstanding_ar"]],
        ["duplicate_of_account", "", survivor["duplicate_of_account"] or "(blank)", duplicate["duplicate_of_account"] or "(blank)"],
        ["Active contacts", website.get("administrator", ""), contact_text(survivor_contacts), contact_text(duplicate_contacts)],
    ]
    st.dataframe(
        _display_frame(pd.DataFrame(rows, columns=["Field", "Website", RECOMMENDED_SURVIVOR, RECOMMENDED_DUPLICATE])),
        width="stretch",
        hide_index=True,
    )
    st.caption("Retrieved " + snapshot.get("retrieved_at", "(unknown)") + ". Parent and contact records are read-only context.")


def _survivor_label(value: str, account_ids: set[str]) -> str:
    if value == "Not selected":
        return "Not selected"
    if account_ids == OWOSSO_IDS and value == RECOMMENDED_SURVIVOR:
        return value + " (recommended)"
    return value


def _executable_change(finding: dict, reviewer: str) -> None:
    account_ids = {account["account_id"] for account in finding["crm_accounts"]}
    st.divider()
    st.markdown("**Executable CRM change**")
    st.caption("Approve finding does not authorize this section.")
    live_accounts = {}
    snapshot = None
    if account_ids == OWOSSO_IDS:
        st.write(RECOMMENDATION)
        if st.button("Refresh Owosso records (GET only)", key="refresh-owosso"):
            save_owosso_snapshot()
            st.rerun()
        try:
            snapshot = load_owosso_snapshot()
        except RuntimeError as exc:
            st.error(str(exc))
        else:
            live_accounts = snapshot["accounts"]
            _comparison(snapshot)
    assessment = assess_finding(finding, live_accounts)
    st.write(assessment["reason"])
    if assessment["kind"] == "already_applied":
        st.success("This duplicate link is already in the CRM. No further request is proposed.")
        return
    if assessment["kind"] == "reparent":
        _findlay_change(finding, reviewer, assessment)
        return
    if assessment["kind"] != "mark_duplicate":
        if assessment["steps"]:
            st.markdown("**Dry-run preview**")
            st.code(
                json.dumps({"request_will_be_sent": False, "steps": assessment["steps"]}, indent=2),
                language="json",
            )
            st.write("This plan is not sent. Its request body is not in the published schema.")
        else:
            st.write("No executable CRM change is available for this finding.")
        return
    if snapshot is None:
        st.write("A current GET snapshot is required before this duplicate can be designated. No request is sent.")
        return
    options = ["Not selected", *assessment["candidate_ids"]]
    survivor = st.radio(
        "Designate the surviving account",
        options,
        format_func=lambda value: _survivor_label(value, account_ids),
        key="duplicate-survivor",
    )
    if survivor == "Not selected":
        st.write("The dry-run appears after a survivor is designated. Nothing is preselected.")
        return
    duplicate = next(account_id for account_id in assessment["candidate_ids"] if account_id != survivor)
    change = build_change(snapshot, survivor, duplicate)
    if account_ids == OWOSSO_IDS and not change["matches_recommendation"]:
        st.warning("This survivor is not the recommended account. The preview shows this designation anyway.")
    st.markdown("**Dry-run preview**")
    st.code(json.dumps(change["preview"], indent=2), language="json")
    st.write(
        "No request has been sent. The survivor account, parent account, and contacts are not in the body."
    )
    exec_note = st.text_area("Note for this CRM change", key="duplicate-exec-note")
    if st.button("Approve this exact CRM change", key="approve-duplicate-change"):
        save_executable_approval(change, reviewer, exec_note)
        st.rerun()
    record = load_approvals()["approvals"].get(change["change_id"])
    if record:
        st.success(
            "Executable approval saved at "
            + record["approved_at"]
            + ". CRM request sent: no."
        )
    else:
        st.write("This exact change does not have an executable approval.")
    if st.button("Apply CRM write-back", key="apply-duplicate-change"):
        result = apply_approved_change(record or {"decision": "approve"}, save_owosso_snapshot)
        st.write("Result: " + result["result"] + ". " + result.get("reason", ""))
        st.write("CRM request sent: " + ("yes" if result["request_sent"] else "no"))


def _findlay_change(finding: dict, reviewer: str, assessment: dict) -> None:
    account = finding["crm_accounts"][0]
    proposal = finding["ownership_change"]
    st.write("Website evidence: " + assessment["website_evidence"])
    st.write(assessment["inference"])
    change = build_findlay_change(account, proposal)
    change["preview"]["writes_enabled"] = writes_enabled()
    st.markdown("**Dry-run preview**")
    st.code(json.dumps(change["preview"], indent=2), language="json")
    st.write("No request has been sent. Approving the finding above does not approve this body.")
    exec_note = st.text_area("Note for this CRM change", key="findlay-exec-note")
    if st.button("Approve this exact CRM change", key="approve-findlay-change"):
        save_executable_approval(change, reviewer, exec_note)
        st.rerun()
    record = load_approvals()["approvals"].get(change["change_id"])
    if record:
        st.success(
            "Executable approval saved at "
            + record["approved_at"]
            + ". CRM request sent: no."
        )
    else:
        st.write("This exact change does not have an executable approval.")
    if st.button("Apply CRM write-back", key="apply-findlay-change"):
        result = apply_findlay_change(record or {"decision": "approve"}, load_findlay_snapshot)
        st.write("Result: " + result["result"] + ". " + result.get("reason", ""))
        st.write("CRM request sent: " + ("yes" if result["request_sent"] else "no"))


def _acquisition(model: dict) -> None:
    context = model.get("acquisition") or {}
    with st.expander("Acquisition evidence"):
        if context.get("about_text"):
            st.write(context["about_text"])
        st.write(context.get("harborview", ""))
        st.write(context.get("cedar_trail", ""))
        st.write(context.get("financial_hold_note", ""))
        for account in context.get("financial_holds") or []:
            st.write(
                f"`{account['account_id']}` {account['name']}: "
                f"parent {account['parent_name']}, "
                f"lifetime revenue {account['lifetime_revenue']}, "
                f"outstanding AR {account['outstanding_ar']}. "
                "Not a confirmed ownership change."
            )
        st.write(context.get("investigation_note", ""))
        for address in context.get("investigation_addresses") or []:
            st.write("- " + address)


def _refresh_sources() -> None:
    if st.session_state.get("sources_refreshed"):
        return
    messages = []
    try:
        refresh_site_context()
    except RuntimeError as exc:
        messages.append(str(exc))
    try:
        refresh_accounts()
    except RuntimeError as exc:
        messages.append(str(exc))
    st.session_state["sources_refreshed"] = True
    st.session_state["refresh_messages"] = messages


def main() -> None:
    st.set_page_config(page_title="Bellhaven review", layout="wide")
    st.title("Bellhaven facility review")
    st.info(
        "CRM writes are disabled. Approve finding, Reject, and Needs Investigation save a review decision only. "
        "They do not authorize an API write. An executable CRM change needs a separate approval and is still not sent while write-back is disabled."
    )
    with st.expander("What the counts mean"):
        st.write(
            "Confirmed matches are high-confidence one-to-one address matches whose normalized names agree. "
            "Ambiguous matches are findings tagged Requires investigation. "
            "Potential duplicates are CRM address groups. "
            "Unresolved records are findings with no decision for the current evidence."
        )
    reviewer = st.sidebar.text_input("Reviewer name", value=st.session_state.get("reviewer", "local reviewer"))
    st.session_state["reviewer"] = reviewer
    _refresh_sources()
    for message in st.session_state.get("refresh_messages") or []:
        st.warning(message + " The review is using the last saved file.")

    website = ROOT / "data" / "website" / "facilities.json"
    crm = ROOT / "data" / "crm" / "accounts.json"
    if not website.exists() or not crm.exists():
        st.error("Saved website or CRM data is missing. Run Phase 1 before opening this review screen.")
        return

    context_path = ROOT / "data" / "website" / "site_context.json"
    context_mtime = context_path.stat().st_mtime if context_path.exists() else 0.0
    model = _model(website.stat().st_mtime, crm.stat().st_mtime, context_mtime)
    findings = _annotated(model, load_store(DECISIONS_PATH))
    _metrics(model["summary"], findings)
    _acquisition(model)
    holds = model["summary"]["confirmed_financial_holds"]
    if holds:
        with st.expander(f"Confirmed Bellhaven matches with a financial hold ({len(holds)})"):
            st.write("No parent change is proposed for these matches, so they are not queued as ownership findings.")
            for account in holds:
                st.write(
                    f"`{account['account_id']}` {account['name']}: "
                    f"revenue {account['lifetime_revenue']}, AR {account['outstanding_ar']}"
                )

    filtered = _filter(findings)
    st.write(f"{len(filtered)} findings shown of {len(findings)}.")
    if filtered:
        st.dataframe(_display_frame(_table(filtered)), width="stretch", hide_index=True)
        labels = {finding["finding_id"]: finding["title"] for finding in filtered}
        selected_id = st.selectbox(
            "Open a finding",
            list(labels),
            format_func=lambda value: labels[value],
        )
        selected = next(finding for finding in filtered if finding["finding_id"] == selected_id)
        _detail(selected, reviewer)
    else:
        st.write("No findings match the current filters.")


if __name__ == "__main__":
    main()
