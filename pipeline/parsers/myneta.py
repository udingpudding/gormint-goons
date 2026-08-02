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

#: "List of Candidates in AMALAPURAM (SC) : ANDHRA PRADESH Lok Sabha 2024".
#: A by-election inserts a third segment:
#: "... : BYE ELECTION ON 03-11-2018 : KARNATAKA Loksabha 2014".
#: 2014 spells the house "Loksabha", hence the optional space.
_PAGE_IDENTITY = re.compile(
    r"List of Candidates in\s+(?P<body>.+?)\s+Lok\s?Sabha\s+\d{4}\s*$", re.IGNORECASE
)
_BYE_ELECTION = re.compile(r"BYE[\s-]*ELECTION", re.IGNORECASE)


class Candidate(BaseModel):
    """One candidate's declaration, as published in a listing."""

    election_slug: str
    election_year: int
    house: str
    view: str
    """Listing this row came from, e.g. ``winner_analyzed`` or ``constituency``."""

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

    # Present only on rows parsed from a constituency page, which carries more than the
    # summary listings do.
    state: str | None = None
    constituency_id: int | None = None
    candidate_id: int | None = None
    """MyNeta's own identifier for the person, from the affidavit link."""

    age: int | None = None

    is_bye_election: bool = False
    """Whether this row is from a by-election rather than the general election.

    MyNeta files by-elections under the general election that preceded them, so leaving them
    in would push the count of members elected above the 543 seats a reader expects and mix
    two different events into one figure."""


class Seat(BaseModel):
    """One constituency, and the state it belongs to."""

    election_slug: str
    election_year: int
    state: str
    state_id: int
    constituency: str
    constituency_id: int
    reservation: str | None


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


#: Article 84(b) of the Constitution sets the minimum age to sit in the Lok Sabha. Anything
#: below it is a data error rather than a young candidate.
MINIMUM_AGE = 25


def parse_age(text: str) -> int | None:
    """Read a declared age, rejecting values that cannot be real.

    MyNeta writes an undeclared age as ``0`` rather than leaving the cell empty, and a
    handful of rows carry ages like 4 or 21. Read literally, 165 candidates would be aged
    zero and every median would be dragged down by them. Below the constitutional minimum
    the value is treated as not declared.
    """
    value = parse_count(text)
    if value is None or value < MINIMUM_AGE:
        return None
    return value


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


def parse_seat_registry(html: str | bytes, *, election_slug: str, election_year: int) -> list[Seat]:
    """Extract the state-to-constituency map from an election's landing page.

    The whole map is on that one page, held in a collapsed dropdown per state: a button
    carrying the state name, followed by ``<div id="item_N">`` listing that state's seats
    with their numeric ids. One request per election, rather than opening 543 seats to
    discover which state each belongs to.

    This matters more than it sounds. Constituency names are not unique — Aurangabad is a
    seat in both Bihar and Maharashtra — so without the state, grouping by name silently
    merges different members.
    """
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")

    tree = HTMLParser(html)
    seats: list[Seat] = []

    for button in tree.css("button.dropbtnJS"):
        onclick = button.attributes.get("onclick") or ""
        match = re.search(r"handle_dropdown\(\s*'item'\s*,\s*'(\d+)'\s*\)", onclick)
        if not match:
            continue

        item_id = match.group(1)
        state = _normalise_space(button.text())
        panel = tree.css_first(f"div#item_{item_id}")
        if not state or panel is None:
            continue

        for link in panel.css("a"):
            href = link.attributes.get("href") or ""
            seat_match = re.search(r"constituency_id=(\d+)", href)
            if not seat_match:
                continue  # the "ALL CONSTITUENCIES" link carries a state_id instead

            name, reservation = split_constituency(link.text())
            if not name:
                continue
            seats.append(
                Seat(
                    election_slug=election_slug,
                    election_year=election_year,
                    state=state,
                    state_id=int(item_id),
                    constituency=name,
                    constituency_id=int(seat_match.group(1)),
                    reservation=reservation,
                )
            )
    return seats


class PageIdentity(BaseModel):
    """What a constituency page says it is about, read from its own title."""

    constituency: str
    state: str
    is_bye_election: bool


def parse_page_identity(tree: HTMLParser | str | bytes) -> PageIdentity | None:
    """Read the seat, state and election type off a constituency page's own title.

    Preferred over the registry mapping because it cannot be wrong about which seat the page
    describes — and the registry demonstrably can be, reusing ids for by-elections.

    The title is colon-separated and the state is always the last segment. A by-election adds
    a middle segment naming its date, which a two-part split would mistake for the state.
    """
    if not isinstance(tree, HTMLParser):
        if isinstance(tree, bytes):
            tree = tree.decode("utf-8", errors="replace")
        tree = HTMLParser(tree)

    node = tree.css_first("title")
    if node is None:
        return None
    match = _PAGE_IDENTITY.search(_normalise_space(node.text()))
    if not match:
        return None

    parts = [p.strip() for p in match.group("body").split(":") if p.strip()]
    if len(parts) < 2:
        return None
    return PageIdentity(
        constituency=parts[0],
        state=parts[-1],
        is_bye_election=bool(_BYE_ELECTION.search(match.group("body"))),
    )


def parse_constituency(
    html: str | bytes,
    *,
    election_slug: str,
    election_year: int,
    house: str,
    seat: Seat,
) -> list[Candidate]:
    """Extract every candidate who stood in one constituency.

    Richer than the summary listings: this covers all candidates rather than a filtered
    subset, marks the winner explicitly, and carries each person's MyNeta id and age. The
    winner is flagged by a separate "Winner" element beside the name, not by a suffix on
    it, so the name itself comes through clean.
    """
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")

    tree = HTMLParser(html)

    # The page names its own seat and state in the title, and that is trusted over the
    # registry entry. In 2009 the registry reuses constituency ids for by-elections — id 1
    # covers Adilabad in Andhra Pradesh, Hisar in Haryana and Tehri Garhwal in Uttarakhand —
    # so a lookup keyed on the id alone silently files candidates under the wrong state.
    identity = parse_page_identity(tree)
    constituency, reservation = seat.constituency, seat.reservation
    state, is_bye_election = seat.state, False
    if identity is not None:
        constituency, reservation = split_constituency(identity.constituency)
        state, is_bye_election = identity.state, identity.is_bye_election

    table = _find_candidate_table(tree)
    if table is None:
        return []

    rows = table.css("tr")
    header = [_normalise_space(c.text()) for c in rows[0].css("th,td")]
    index = _column_index(header, require_constituency=False)

    out: list[Candidate] = []
    for row in rows[1:]:
        cells = row.css("td")
        if len(cells) < 4:
            continue

        name_cell = cells[index["candidate"]]
        link = name_cell.css_first("a")
        name = _normalise_space(link.text() if link is not None else name_cell.text())
        if not name:
            continue

        candidate_id = None
        if link is not None:
            id_match = re.search(r"candidate_id=(\d+)", link.attributes.get("href") or "")
            if id_match:
                candidate_id = int(id_match.group(1))

        text = [c.text() for c in cells]
        out.append(
            Candidate(
                election_slug=election_slug,
                election_year=election_year,
                house=house,
                view="constituency",
                is_winner="winner" in name_cell.text().lower(),
                serial=parse_count(text[index["sno"]]) if "sno" in index else None,
                name=name,
                constituency=constituency,
                reservation=reservation,
                party=_normalise_space(text[index["party"]]),
                criminal_cases=parse_count(text[index["criminal"]]),
                education=(
                    _normalise_space(text[index["education"]]) or None
                    if "education" in index
                    else None
                ),
                assets_rupees=parse_money(text[index["assets"]]) if "assets" in index else None,
                liabilities_rupees=(
                    parse_money(text[index["liabilities"]]) if "liabilities" in index else None
                ),
                state=state,
                constituency_id=seat.constituency_id,
                candidate_id=candidate_id,
                age=parse_age(text[index["age"]]) if "age" in index else None,
                is_bye_election=is_bye_election,
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


def _column_index(header: list[str], *, require_constituency: bool = True) -> dict[str, int]:
    """Map logical fields to column positions by header text.

    Positional assumptions would be the obvious shortcut and the obvious way to silently
    mis-attribute assets to liabilities if a column is ever inserted. Constituency pages
    legitimately omit the constituency column — the page is already about one seat — and
    add an age column the summary listings do not have.
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
        elif text == "age":
            lookup["age"] = position
        elif "asset" in text:
            lookup["assets"] = position
        elif "liabilit" in text:
            lookup["liabilities"] = position

    required = {"candidate", "party", "criminal"}
    if require_constituency:
        required.add("constituency")
    missing = required - lookup.keys()
    if missing:
        raise ValueError(f"Listing table is missing expected columns: {sorted(missing)}")
    return lookup
