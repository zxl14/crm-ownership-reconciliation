"""Authenticated read-only CRM access for the Owosso duplicate review."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from src.scrape_website import DetailParser

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://analyst-assessment-production.up.railway.app"
SNAPSHOT_PATH = ROOT / "data" / "crm" / "owosso_latest.json"
OWOSSO_IDS = ("001EGU7BMJ942ZTRE6", "001QU150PM4Z15UA71")
OWOSSO_WEBSITE = "/communities/bellhaven-of-owosso"


def _token() -> str:
    load_dotenv(ROOT / ".env")
    token = os.environ.get("BELLHAVEN_API_TOKEN", "").strip()
    if not token or token == "PASTE_YOUR_TOKEN_HERE":
        raise RuntimeError("BELLHAVEN_API_TOKEN is missing or still the placeholder")
    return token


def authenticated_session() -> requests.Session:
    session = requests.Session()
    session.headers["Authorization"] = "Bearer " + _token()
    session.headers["Accept"] = "application/json"
    return session


def _get_json(session: requests.Session, path: str, params: dict | None = None):
    try:
        response = session.get(BASE_URL + path, params=params, timeout=60)
    except requests.RequestException as exc:
        raise RuntimeError(f"GET {path} failed: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise RuntimeError(f"GET {path} returned HTTP {response.status_code}")
    return response.json()


def _contact_page(session: requests.Session, account_id: str) -> list[dict]:
    rows = []
    page = 1
    total = None
    while page <= 20:
        payload = _get_json(
            session,
            "/api/v1/contacts",
            {"account_id": account_id, "page": page, "page_size": 50},
        )
        batch = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(batch, list):
            raise RuntimeError("Contacts response has no data list")
        rows.extend(batch)
        if isinstance(payload, dict) and isinstance(payload.get("total"), int):
            total = payload["total"]
        if not batch or (total is not None and len(rows) >= total) or len(batch) < 50:
            break
        page += 1
    return rows


def _website() -> dict:
    try:
        response = requests.get(BASE_URL + OWOSSO_WEBSITE, timeout=60)
    except requests.RequestException as exc:
        raise RuntimeError(f"GET {OWOSSO_WEBSITE} failed: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise RuntimeError(f"GET {OWOSSO_WEBSITE} returned HTTP {response.status_code}")
    parser = DetailParser()
    parser.feed(response.text)
    return {
        "name": parser.h1,
        "address_lines": parser.address_lines,
        "full_address": parser.fields.get("Address", ""),
        "administrator": parser.fields.get("Administrator", ""),
        "phone": parser.fields.get("Phone", ""),
        "care_offerings": parser.badges_by_field.get("Care Offerings", []),
        "source_url": BASE_URL + OWOSSO_WEBSITE,
    }


def fetch_owosso_snapshot(session: requests.Session | None = None) -> dict:
    client = session or authenticated_session()
    accounts = {}
    contacts = {}
    for account_id in OWOSSO_IDS:
        accounts[account_id] = _get_json(client, f"/api/v1/accounts/{account_id}")
        contacts[account_id] = _contact_page(client, account_id)
    parent_ids = sorted({account.get("parent_id", "") for account in accounts.values() if account.get("parent_id")})
    parents = {parent_id: _get_json(client, f"/api/v1/accounts/{parent_id}") for parent_id in parent_ids}
    return {
        "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "accounts": accounts,
        "contacts": contacts,
        "parents": parents,
        "website": _website(),
    }


def save_owosso_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    snapshot = fetch_owosso_snapshot()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot


def load_findlay_snapshot(session: requests.Session | None = None) -> dict:
    """Read the Findlay facility and the proposed Bellhaven parent. GET only."""
    from src.ownership import BELLHAVEN_PARENT_ID, FINDLAY_ACCOUNT

    client = session or authenticated_session()
    facility = _get_json(client, f"/api/v1/accounts/{FINDLAY_ACCOUNT}")
    parent = _get_json(client, f"/api/v1/accounts/{BELLHAVEN_PARENT_ID}")
    return {"accounts": {FINDLAY_ACCOUNT: facility, BELLHAVEN_PARENT_ID: parent}}


def load_owosso_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    if not path.exists():
        raise RuntimeError("Owosso snapshot is missing. Refresh it with a GET before review.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "accounts" not in payload or "contacts" not in payload:
        raise RuntimeError("Owosso snapshot is unreadable")
    normalized = {}
    for account_id, value in payload["contacts"].items():
        if isinstance(value, dict) and isinstance(value.get("data"), list):
            normalized[account_id] = value["data"]
        elif isinstance(value, list):
            normalized[account_id] = value
        else:
            raise RuntimeError(f"Contacts for {account_id} are unreadable")
    payload["contacts"] = normalized
    return payload
