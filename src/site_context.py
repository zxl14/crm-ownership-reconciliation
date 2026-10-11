"""Save the public homepage and About text used as ownership evidence.

These pages do not require the CRM token. The saved file is website text only.
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

from src.scrape_website import BASE_URL, _about_text, homepage_welcome

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_PATH = ROOT / "data" / "website" / "site_context.json"


def refresh_site_context(path: Path = CONTEXT_PATH) -> dict:
    try:
        homepage = requests.get(BASE_URL + "/", timeout=60)
        about = requests.get(BASE_URL + "/about", timeout=60)
    except requests.RequestException as exc:
        raise RuntimeError(f"Public page refresh failed: {type(exc).__name__}") from exc
    if homepage.status_code != 200 or about.status_code != 200:
        raise RuntimeError(
            f"Public page refresh returned HTTP {homepage.status_code} and {about.status_code}"
        )
    payload = {
        "homepage_url": BASE_URL + "/",
        "about_url": BASE_URL + "/about",
        "welcome_text": homepage_welcome(homepage.text),
        "about_text": _about_text(about.text),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)
    return payload


def load_site_context(path: Path = CONTEXT_PATH) -> dict:
    if not path.exists():
        return {"welcome_text": "", "about_text": "", "homepage_url": "", "about_url": ""}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Site context file is unreadable")
    return payload
