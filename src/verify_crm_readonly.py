"""Read-only CRM check. GET /api/v1/me and paginated GET /api/v1/accounts.

The API token is read from the environment and sent only as an Authorization
header. It is never printed, logged, or written to disk.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://analyst-assessment-production.up.railway.app"
PAGE_SIZE = 50
MAX_PAGES = 50
OUT_DIR = ROOT / "data" / "crm"
SENSITIVE_KEYS = {
    "token",
    "api_token",
    "access_token",
    "authorization",
    "secret",
    "password",
}


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def load_token() -> str:
    load_dotenv(ROOT / ".env")
    token = os.environ.get("BELLHAVEN_API_TOKEN", "").strip()
    if not token or token == "PASTE_YOUR_TOKEN_HERE":
        fail("BELLHAVEN_API_TOKEN is missing or still the placeholder in .env")
    return token


def redact(value, token: str):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_KEYS or contains_token(item, token):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = redact(item, token)
        return cleaned
    if isinstance(value, list):
        return [redact(item, token) for item in value]
    if contains_token(value, token):
        return "[REDACTED]"
    return value


def contains_token(value, token: str) -> bool:
    return isinstance(value, str) and token in value


def get_json(session: requests.Session, path: str, params: dict | None = None):
    try:
        response = session.get(f"{BASE}{path}", params=params, timeout=60)
    except requests.RequestException as exc:
        fail(f"GET {path} failed: {type(exc).__name__}")
    body = response.text
    if token_from_session(session) in body:
        body = body.replace(token_from_session(session), "[REDACTED]")
    if response.status_code != 200:
        fail(f"GET {path} returned HTTP {response.status_code}: {body[:500]}")
    try:
        return response.json()
    except ValueError:
        fail(f"GET {path} returned a non-JSON body")


def token_from_session(session: requests.Session) -> str:
    header = session.headers.get("Authorization", "")
    prefix = "Bearer "
    if header.startswith(prefix):
        return header[len(prefix) :]
    return ""


def page_items(payload):
    if isinstance(payload, list):
        return payload, {}
    if not isinstance(payload, dict):
        fail(f"Unexpected accounts payload type: {type(payload).__name__}")
    for key in ("items", "accounts", "data", "results", "records"):
        if isinstance(payload.get(key), list):
            meta = {name: value for name, value in payload.items() if name != key}
            return payload[key], meta
    fail(
        "Accounts response was an object without a list field. Keys: "
        + ", ".join(payload.keys())
    )


def json_type(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def flatten(value, prefix: str = ""):
    if isinstance(value, dict):
        if not value and prefix:
            yield prefix, {}
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from flatten(item, path)
        return
    yield prefix, value


def summarize_records(records: list) -> dict:
    paths: Counter[str] = Counter()
    types: dict[str, Counter[str]] = {}
    values: dict[str, list] = {}
    for record in records:
        seen = set()
        for path, value in flatten(record):
            if not path or path in seen:
                continue
            seen.add(path)
            paths[path] += 1
            types.setdefault(path, Counter())[json_type(value)] += 1
            if not isinstance(value, (dict, list)):
                values.setdefault(path, []).append(value)

    fields = []
    for path in sorted(paths):
        observed = values.get(path, [])
        non_null = [item for item in observed if item is not None and item != ""]
        entry = {
            "field": path,
            "present_on_records": paths[path],
            "types": dict(types[path]),
            "null_or_empty": len(observed) - len(non_null),
            "distinct_non_empty": len({json.dumps(item, sort_keys=True, default=str) for item in non_null}),
        }
        distinct = list(dict.fromkeys(non_null))
        if distinct and len(distinct) <= 12 and all(
            isinstance(item, (str, int, float, bool)) for item in distinct
        ):
            entry["values"] = distinct
        numeric = [item for item in non_null if isinstance(item, (int, float)) and not isinstance(item, bool)]
        if numeric:
            entry["min"] = min(numeric)
            entry["max"] = max(numeric)
            entry["positive"] = sum(item > 0 for item in numeric)
            entry["zero"] = sum(item == 0 for item in numeric)
        fields.append(entry)
    return {"record_count": len(records), "fields": fields}


def account_id(record: dict):
    for key in ("id", "account_id"):
        if key in record:
            return record.get(key)
    return None


def summarize_parents(records: list) -> dict:
    by_id = {}
    for record in records:
        if isinstance(record, dict):
            identifier = account_id(record)
            if identifier is not None:
                by_id[str(identifier)] = record.get("name")
    parents = Counter()
    missing_parent_record = 0
    linked = 0
    for record in records:
        if not isinstance(record, dict) or "parent_id" not in record:
            continue
        parent = record.get("parent_id")
        if parent in (None, ""):
            continue
        linked += 1
        parents[str(parent)] += 1
        if str(parent) not in by_id:
            missing_parent_record += 1
    named = []
    for parent_id, count in parents.most_common():
        named.append(
            {
                "parent_id": parent_id,
                "child_accounts": count,
                "parent_name": by_id.get(parent_id),
            }
        )
    return {
        "accounts_with_parent_id": linked,
        "distinct_parent_ids": len(parents),
        "child_rows_whose_parent_is_absent": missing_parent_record,
        "parents": named,
    }


def main() -> None:
    token = load_token()
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    session.headers["Accept"] = "application/json"

    me = redact(get_json(session, "/api/v1/me"), token)
    records = []
    envelopes = []
    page = 1
    while page <= MAX_PAGES:
        payload = get_json(
            session,
            "/api/v1/accounts",
            params={"page": page, "page_size": PAGE_SIZE},
        )
        items, meta = page_items(payload)
        if page == 1:
            envelopes.append(redact(meta, token))
        if not items:
            break
        records.extend(redact(item, token) for item in items)
        if len(items) < PAGE_SIZE:
            break
        page += 1
    else:
        fail("Pagination stopped at the safety limit before a short page")

    ids = [account_id(record) for record in records if isinstance(record, dict)]
    id_counts = Counter(str(item) for item in ids if item is not None)
    summary = {
        "me_keys": sorted(me.keys()) if isinstance(me, dict) else [json_type(me)],
        "me": me,
        "pages_fetched": page if records else 0,
        "page_size": PAGE_SIZE,
        "list_envelope": envelopes[0] if envelopes else {},
        "accounts_returned": len(records),
        "distinct_account_ids": len(id_counts),
        "duplicate_account_ids": sorted(
            identifier for identifier, count in id_counts.items() if count > 1
        ),
        "schema": summarize_records([record for record in records if isinstance(record, dict)]),
        "parents": summarize_parents([record for record in records if isinstance(record, dict)]),
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "me.json").write_text(json.dumps(me, indent=2), encoding="utf-8")
    (OUT_DIR / "accounts.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(f"accounts_returned={len(records)}")
    print(f"distinct_account_ids={len(id_counts)}")
    print(f"pages_fetched={summary['pages_fetched']}")
    print(f"wrote={OUT_DIR}")


if __name__ == "__main__":
    main()
