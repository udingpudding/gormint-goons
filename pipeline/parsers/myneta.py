"""Stage two for MyNeta: archived listing pages into normalized candidate rows.

The listing tables carry eight columns — serial, candidate, constituency, party, criminal
case count, education, total assets, liabilities — and the markup is identical across all
five Lok Sabha elections, so one parser covers 2004 to 2024.

Most of the work here is in the money column, which is written for a reader rather than a
machine::

    Rs 13,58,312~ 13 Lacs+          ->  1358312
    Rs 56,81,54,912~ 56 Crore+      ->  568154912
    Rs 0~                           ->  0
    Nil / Not Available / -         ->  None

The digits before the tilde are the exact declared figure and the text after it is a rounded
restatement, so the parser reads the former and ignores the latter. Indian digit grouping
(``56,81,54,912`` is 56.8 crore, not 5.68 billion) is irrelevant once commas are stripped,
which is why they are simply removed rather than interpreted.

A missing figure is ``None``, never zero. A candidate who declared nothing and a candidate
whose affidavit ADR could not read are different facts, and collapsing them would quietly
understate the assets of anyone in the second group.
"""

from __future__ import annotations

import re

from pydantic import BaseModel
from selectolax.parser import HTMLParser

#: Values appearing in place of a number when nothing was declared or read.
_NOT_A_NUMBER = {"", "-", "--", "nil", "n/a", "na", "not available", "nota", "none"}

_MONEY = re.compile(r"(\d[\d,]*)")
_RESERVATION = re.compile(r"\(\s*(SC|ST)\s*\)\s*$", re.IGNORECASE)


class Candidate(BaseModel):
    """One candidate's declaration, as published in a listing."""

    election_slug: str
    election_year: int
    house: str
    view: str
    """Listing this row came from, e.g. ``winner_analyzed``."""

    is_winner: bool
    serial: int | None
    name: str
    constituency: str
    reservation: str | None
    """``SC``, ``ST``, or ``None`` for a general seat."""

    party: str
    criminal_cases: int | None
    education: str | None
    assets_rupees: int | None
    liabilities_rupees: int | None


def parse_money(text: str) -> int | None:
    """Read a declared rupee figure. Returns ``None`` when nothing was declared."""
    cleaned = _normalise_space(text).lower()
    if cleaned in _NOT_A_NUMBER:
        return None

    # Only the portion before the tilde is the exact figure; after it is a rounded gloss
    # ("~ 13 Lacs+") whose digits would otherwise be mistaken for the value.
    exact = cleaned.split("~", 1)[0]
    match = _MONEY.search(exact)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def parse_count(text: str) -> int | None:
    """Read a case count. Returns ``None`` when it was not reported."""
    cleaned = _normalise_space(text).lower()
    if cleaned in _NOT_A_NUMBER:
        return None
    match = re.search(r"\d+", cleaned)
    return int(match.group()) if match else None


def split_constituency(text: str) -> tuple[str, str | None]:
    """Separate a seat's name from its reservation, e.g. ``JALANDHAR (SC)``."""
    cleaned = _normalise_space(text)
    match = _RESERVATION.search(cleaned)
    if not match:
        return cleaned, None
    return _RESERVATION.sub("", cleaned).strip(), match.group(1).upper()


def parse_listing(
    html: str | bytes,
    *,
    election_slug: str,
    election_year: int,
    house: str,
    view: str,
) -> list[Candidate]:
    """Extract every candidate row from one archived listing page.

    An empty list means the page held no candidate table, which is how the crawler learns it
    has run off the end of the pagination.
    """
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")

    table = _find_candidate_table(HTMLParser(html))
    if table is None:
        return []

    rows = table.css("tr")
    header = [_normalise_space(c.text()) for c in rows[0].css("th,td")]
    index = _column_index(header)

    out: list[Candidate] = []
    for row in rows[1:]:
        cells = [c.text() for c in row.css("td")]
        if len(cells) < len(header) - 1:
            continue

        name = _normalise_space(cells[index["candidate"]])
        if not name:
            continue

        constituency, reservation = split_constituency(cells[index["constituency"]])
        out.append(
            Candidate(
                election_slug=election_slug,
                election_year=election_year,
                house=house,
                view=view,
                is_winner=view.startswith("winner"),
                serial=parse_count(cells[index["sno"]]) if "sno" in index else None,
                name=name,
                constituency=constituency,
                reservation=reservation,
                party=_normalise_space(cells[index["party"]]),
                criminal_cases=parse_count(cells[index["criminal"]]),
                education=_normalise_space(cells[index["education"]]) or None
                if "education" in index
                else None,
                assets_rupees=parse_money(cells[index["assets"]]) if "assets" in index else None,
                liabilities_rupees=(
                    parse_money(cells[index["liabilities"]]) if "liabilities" in index else None
                ),
            )
        )
    return out


# -- internals ----------------------------------------------------------------------


def _normalise_space(text: str) -> str:
    """Collapse whitespace, including the non-breaking spaces MyNeta uses inside figures."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _find_candidate_table(tree: HTMLParser):
    """The page also holds layout and footer tables, so pick the one with a candidate
    header and the most rows."""
    best, best_rows = None, 0
    for table in tree.css("table"):
        rows = table.css("tr")
        if not rows:
            continue
        header = " ".join(_normalise_space(c.text()) for c in rows[0].css("th,td")).lower()
        if "candidate" in header and "party" in header and len(rows) > best_rows:
            best, best_rows = table, len(rows)
    return best


def _column_index(header: list[str]) -> dict[str, int]:
    """Map logical fields to column positions by header text.

    Positional assumptions would be the obvious shortcut and the obvious way to silently
    mis-attribute assets to liabilities if a column is ever inserted.
    """
    lookup: dict[str, int] = {}
    for position, label in enumerate(header):
        text = label.lower().replace("∇", "").strip()
        if text.startswith("sno"):
            lookup["sno"] = position
        elif "candidate" in text:
            lookup["candidate"] = position
        elif "constituency" in text:
            lookup["constituency"] = position
        elif "party" in text:
            lookup["party"] = position
        elif "criminal" in text:
            lookup["criminal"] = position
        elif "education" in text:
            lookup["education"] = position
        elif "asset" in text:
            lookup["assets"] = position
        elif "liabilit" in text:
            lookup["liabilities"] = position

    required = {"candidate", "constituency", "party", "criminal"}
    missing = required - lookup.keys()
    if missing:
        raise ValueError(f"Listing table is missing expected columns: {sorted(missing)}")
    return lookup
