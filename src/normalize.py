"""Address and name normalization used by reconciliation.

Only whole words are changed. "W" becomes "west" and "St" becomes "street",
while words such as "stewart" are left alone. "pk" becomes "pike" because the
CRM contains both "Wilmington Pk" and "Wilmington Pike" for the same campus.
"""

from __future__ import annotations

import re

_STREET_WORDS = {
    "st": "street",
    "street": "street",
    "rd": "road",
    "road": "road",
    "ave": "avenue",
    "avenue": "avenue",
    "blvd": "boulevard",
    "boulevard": "boulevard",
    "dr": "drive",
    "drive": "drive",
    "ln": "lane",
    "lane": "lane",
    "ct": "court",
    "court": "court",
    "cir": "circle",
    "circle": "circle",
    "pkwy": "parkway",
    "parkway": "parkway",
    "hwy": "highway",
    "highway": "highway",
    "trl": "trail",
    "trail": "trail",
    "ter": "terrace",
    "terrace": "terrace",
    "pl": "place",
    "place": "place",
    "pk": "pike",
    "pike": "pike",
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
    "north": "north",
    "south": "south",
    "east": "east",
    "west": "west",
    "northeast": "northeast",
    "northwest": "northwest",
    "southeast": "southeast",
    "southwest": "southwest",
    "centre": "center",
    "center": "center",
}

_NAME_WORDS = {
    "centre": "center",
    "center": "center",
    "and": "and",
}


def _letters_and_digits(value: str) -> list[str]:
    text = (value or "").lower().replace("&", " and ")
    text = text.replace(".", " ").replace(",", " ").replace("#", " ")
    text = text.replace("-", " ").replace("/", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [token for token in text.split() if token]


def normalize_street(street: str) -> str:
    tokens = [_STREET_WORDS.get(token, token) for token in _letters_and_digits(street)]
    return " ".join(tokens)


def normalize_name(name: str) -> str:
    tokens = [_NAME_WORDS.get(token, token) for token in _letters_and_digits(name)]
    return " ".join(tokens)


def normalize_city(city: str) -> str:
    return " ".join(_letters_and_digits(city))


def normalize_state(state: str) -> str:
    compact = re.sub(r"[^A-Za-z]", "", state or "")
    return compact.upper()


def normalize_zip(zip_code: str) -> str:
    digits = re.sub(r"\D", "", zip_code or "")
    return digits[:5]


def address_key(street: str, city: str, state: str, zip_code: str) -> tuple[str, str, str, str] | None:
    """Return the match key, or None when the street or ZIP is missing."""
    normalized = (
        normalize_street(street),
        normalize_city(city),
        normalize_state(state),
        normalize_zip(zip_code),
    )
    if not normalized[0] or not normalized[3]:
        return None
    return normalized


def format_address_key(key: tuple[str, str, str, str] | None) -> str:
    if key is None:
        return ""
    street, city, state, zip_code = key
    return f"{street}, {city}, {state} {zip_code}"


def phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def check_normalizer() -> None:
    assert normalize_street("1120 W Main St") == normalize_street("1120 West Main Street")
    assert normalize_street("1120 W. Main St.") == "1120 west main street"
    assert normalize_street("3313 Wilmington Pk") == normalize_street("3313 Wilmington Pike")
    assert normalize_street("210 Orchard Ln.") == normalize_street("210 orchard lane")
    assert normalize_name("Nursing & Rehabilitation") == normalize_name("Nursing and Rehabilitation")
    assert address_key("1120 W. Main St", "Owosso", "mi", "48867") == address_key(
        "1120 West Main Street", "owosso", "MI", "48867-0000"
    )
    assert address_key("", "Owosso", "MI", "48867") is None
