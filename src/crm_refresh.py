"""Replace the local account file with a read-only CRM download.

This module uses GET only. It does not create, update, or delete CRM records.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.crm_read import _get_json, authenticated_session

ROOT = Path(__file__).resolve().parents[1]
CRM_PATH = ROOT / "data" / "crm" / "accounts.json"
PAGE_SIZE = 50
MAX_PAGES = 50


def refresh_accounts(path: Path = CRM_PATH) -> list[dict]:
    session = authenticated_session()
    records = []
    page = 1
    while page <= MAX_PAGES:
        payload = _get_json(session, "/api/v1/accounts", {"page": page, "page_size": PAGE_SIZE})
        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise RuntimeError("CRM account list did not contain a data array")
        if not items:
            break
        records.extend(items)
        if len(items) < PAGE_SIZE:
            break
        page += 1
    else:
        raise RuntimeError("CRM account pagination stopped at the safety limit")
    if len(records) < 100:
        raise RuntimeError(f"CRM refresh returned {len(records)} accounts")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
    temporary.replace(path)
    return records
