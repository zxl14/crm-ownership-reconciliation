"""Read-only scrape of the public Bellhaven facility pages.

Directory cards do not contain a full street address, so every facility detail
page is fetched. The homepage is also read because Bellhaven Meadows of Findlay
is linked there and is absent from the paginated directory.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import requests

BASE_URL = "https://analyst-assessment-production.up.railway.app"
FINDLAY_SLUG = "bellhaven-meadows-of-findlay"


class _SkipParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("style", "script"):
            self.skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script") and self.skip:
            self.skip -= 1


def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
    values = dict(attrs)
    return set((values.get("class") or "").split())


def _text(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


class DirectoryParser(_SkipParser):
    def __init__(self) -> None:
        super().__init__()
        self.cards: list[dict] = []
        self.page_number: int | None = None
        self.total_pages: int | None = None
        self.reported_count: int | None = None
        self._div_depth = 0
        self._card_depth: int | None = None
        self._current: dict | None = None
        self._capture: str | None = None
        self._parts: list[str] = []
        self._page_parts: list[str] = []
        self.visible: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        if self.skip:
            return
        classes = _classes(attrs)
        if tag == "div":
            self._div_depth += 1
            if "card" in classes and self._current is None:
                self._card_depth = self._div_depth
                self._current = {"name": "", "href": "", "city_line": "", "badges": []}
            elif "city" in classes and self._current is not None:
                self._capture = "city"
                self._parts = []
        elif tag == "h3" and self._current is not None:
            self._capture = "name"
            self._parts = []
        elif tag == "a" and self._current is not None:
            self._current["href"] = dict(attrs).get("href") or ""
        elif tag == "span" and "badge" in classes and self._current is not None:
            self._capture = "badge"
            self._parts = []
    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and not self.skip:
            if self._card_depth == self._div_depth and self._current is not None:
                self.cards.append(self._current)
                self._current = None
                self._card_depth = None
            self._div_depth = max(0, self._div_depth - 1)
        if tag in ("h3", "div", "span") and self._capture in ("name", "city", "badge"):
            text = _text(self._parts)
            if self._current is not None and text:
                if self._capture == "name":
                    self._current["name"] = text
                elif self._capture == "city":
                    self._current["city_line"] = text
                elif self._capture == "badge":
                    self._current["badges"].append(text)
            if tag != "div" or self._capture != "city":
                if not (tag == "div" and self._capture == "city"):
                    self._capture = None
            if tag == "div" and self._capture == "city":
                self._capture = None
        super().handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        self.visible.append(data)
        if self._capture:
            self._parts.append(data)

    def finish(self) -> None:
        text = _text(self.visible)
        match = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", text)
        count = re.search(r"(\d+)\s+communities listed", text)
        if match:
            self.page_number = int(match.group(1))
            self.total_pages = int(match.group(2))
        if count:
            self.reported_count = int(count.group(1))


class DetailParser(_SkipParser):
    def __init__(self) -> None:
        super().__init__()
        self.h1 = ""
        self.fields: dict[str, str] = {}
        self.badges_by_field: dict[str, list[str]] = {}
        self._capture: str | None = None
        self._parts: list[str] = []
        self._label = ""
        self._badges: list[str] = []
        self.address_lines: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        if self.skip:
            return
        if tag == "h1":
            self._capture = "h1"
            self._parts = []
        elif tag == "dt":
            self._capture = "dt"
            self._parts = []
        elif tag == "dd":
            self._capture = "dd"
            self._parts = []
            self._badges = []
        elif tag == "br" and self._capture == "dd":
            self._parts.append("\n")
        elif tag == "span" and "badge" in _classes(attrs) and self._capture == "dd":
            self._capture = "badge"
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if not self.skip:
            if tag == "h1" and self._capture == "h1":
                self.h1 = _text(self._parts)
                self._capture = None
            elif tag == "dt" and self._capture == "dt":
                self._label = _text(self._parts)
                self._capture = None
            elif tag == "span" and self._capture == "badge":
                badge = _text(self._parts)
                if badge:
                    self._badges.append(badge)
                self._capture = "dd"
                self._parts = []
            elif tag == "dd" and self._label:
                lines = [" ".join(line.split()) for line in "".join(self._parts).split("\n")]
                lines = [line for line in lines if line]
                if self._label == "Address":
                    self.address_lines = lines
                self.fields[self._label] = ", ".join(lines)
                if self._badges:
                    self.badges_by_field[self._label] = list(self._badges)
                self._label = ""
                self._capture = None
        super().handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self.skip or not self._capture:
            return
        if self._capture in ("h1", "dt", "dd", "badge"):
            self._parts.append(data)


def _get(session: requests.Session, path: str) -> str:
    try:
        response = session.get(f"{BASE_URL}{path}", timeout=60)
    except requests.RequestException as exc:
        raise RuntimeError(f"GET {path} failed: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise RuntimeError(f"GET {path} returned HTTP {response.status_code}")
    return response.text


def _slug(href: str) -> str:
    match = re.fullmatch(r"/communities/([a-z0-9-]+)", href or "")
    return match.group(1) if match else ""


def _split_city_line(city_line: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+),\s*([A-Z]{2})", " ".join(city_line.split()))
    if not match:
        return "", ""
    return match.group(1).strip(), match.group(2)


def parse_address_lines(lines: list[str]) -> tuple[str, str, str, str]:
    """Split the detail-page address. The street and locality are separate lines."""
    if len(lines) < 2:
        return "", "", "", ""
    match = re.fullmatch(r"(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)", lines[-1].strip())
    if not match:
        return "", "", "", ""
    street = ", ".join(line.strip() for line in lines[:-1] if line.strip())
    return street, match.group(1).strip(), match.group(2), match.group(3)


def homepage_welcome(html: str) -> str:
    match = re.search(r'<div class="notice">(.*?)</div>', html, flags=re.DOTALL)
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", " ", match.group(1))
    return _text([text])


def _homepage_context(html: str) -> dict:
    count_match = re.search(r"(\d+)\s+communities", html, flags=re.IGNORECASE)
    hints = re.findall(r"\bin\s+([A-Z][A-Za-z .'-]+),\s*([A-Z]{2})\b", html)
    slugs = []
    for href in re.findall(r'href="(/communities/[a-z0-9-]+)"', html):
        slug = _slug(href)
        if slug and slug not in slugs:
            slugs.append(slug)
    return {
        "reported_count": int(count_match.group(1)) if count_match else None,
        "city_hints": [(city.strip(), state) for city, state in hints],
        "slugs": slugs,
        "welcome_text": homepage_welcome(html),
    }


def _about_text(html: str) -> str:
    class AboutParser(_SkipParser):
        def __init__(self) -> None:
            super().__init__()
            self.parts: list[str] = []
            self._in_wrap = False
            self._div_depth = 0
            self._wrap_depth: int | None = None

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            super().handle_starttag(tag, attrs)
            if self.skip:
                return
            if tag == "div":
                self._div_depth += 1
                if "wrap" in _classes(attrs) and self._wrap_depth is None:
                    self._wrap_depth = self._div_depth

        def handle_endtag(self, tag: str) -> None:
            if tag == "div" and not self.skip:
                if self._wrap_depth == self._div_depth:
                    self._wrap_depth = None
                self._div_depth = max(0, self._div_depth - 1)
            super().handle_endtag(tag)

        def handle_data(self, data: str) -> None:
            if not self.skip and self._wrap_depth is not None:
                self.parts.append(data)

    parser = AboutParser()
    parser.feed(html)
    return _text(parser.parts)


def scrape_facilities(session: requests.Session | None = None) -> dict:
    client = session or requests.Session()
    client.headers.setdefault("Accept", "text/html")
    client.headers.setdefault("User-Agent", "bellhaven-reconciliation-readonly")

    cards: list[dict] = []
    page = 1
    total_pages = None
    reported_count = None
    while True:
        path = "/communities" if page == 1 else f"/communities?page={page}"
        parsed = DirectoryParser()
        parsed.feed(_get(client, path))
        parsed.finish()
        if parsed.page_number != page or not parsed.total_pages:
            raise RuntimeError(f"Could not read directory pagination from {path}")
        if total_pages is None:
            total_pages = parsed.total_pages
            reported_count = parsed.reported_count
        elif parsed.total_pages != total_pages:
            raise RuntimeError("Directory page count changed while scraping")
        for card in parsed.cards:
            card["directory_page"] = page
            cards.append(card)
        if page >= total_pages:
            break
        page += 1
        if page > 20:
            raise RuntimeError("Directory pagination exceeded the safety limit")

    by_slug: dict[str, dict] = {}
    for card in cards:
        slug = _slug(card["href"])
        if not slug or slug in by_slug:
            continue
        city, state = _split_city_line(card["city_line"])
        by_slug[slug] = {
            "slug": slug,
            "directory_name": card["name"],
            "city_hint": city,
            "state_hint": state,
            "directory_page": card["directory_page"],
            "listed_in_directory": True,
        }

    homepage = _homepage_context(_get(client, "/"))
    for slug in homepage["slugs"]:
        if slug not in by_slug:
            by_slug[slug] = {
                "slug": slug,
                "directory_name": "",
                "city_hint": "",
                "state_hint": "",
                "directory_page": None,
                "listed_in_directory": False,
            }
    homepage_only = [item for item in by_slug.values() if not item["listed_in_directory"]]
    if len(homepage_only) == 1 and len(homepage["city_hints"]) == 1:
        city, state = homepage["city_hints"][0]
        homepage_only[0]["city_hint"] = city
        homepage_only[0]["state_hint"] = state

    facilities = []
    for slug, hint in by_slug.items():
        parsed = DetailParser()
        parsed.feed(_get(client, f"/communities/{slug}"))
        street, city, state, zip_code = parse_address_lines(parsed.address_lines)
        hint_agrees = True
        if hint["city_hint"] or hint["state_hint"]:
            hint_agrees = (
                hint["city_hint"].casefold() == city.casefold()
                and hint["state_hint"].upper() == state.upper()
            )
        locality = f"{city}, {state} {zip_code}".strip()
        facilities.append(
            {
                "name": parsed.h1 or hint["directory_name"],
                "street": street,
                "city": city,
                "state": state,
                "zip": zip_code,
                "full_address": ", ".join(part for part in (street, locality) if part),
                "care_offerings": parsed.badges_by_field.get("Care Offerings", []),
                "administrator": parsed.fields.get("Administrator", ""),
                "phone": parsed.fields.get("Phone", ""),
                "source_url": f"{BASE_URL}/communities/{slug}",
                "slug": slug,
                "listed_in_directory": hint["listed_in_directory"],
                "directory_page": hint["directory_page"],
                "address_parsed": bool(street and city and state and zip_code and hint_agrees),
            }
        )

    return {
        "directory_pages": total_pages,
        "directory_reported_count": reported_count,
        "homepage_reported_count": homepage["reported_count"],
        "homepage_city_hints": homepage["city_hints"],
        "homepage_welcome": homepage["welcome_text"],
        "about_text": _about_text(_get(client, "/about")),
        "facilities": facilities,
    }
