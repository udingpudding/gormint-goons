"""Locating candidate affidavit data on MyNeta.

MyNeta is published by the Association for Democratic Reforms and National Election Watch,
compiled from the affidavits every candidate must file with the Election Commission of India.
It is the only place criminal-case and asset declarations exist in comparable form across
twenty years of Indian elections.

Two things make it tractable where the ministry portals were not: the pages are
server-rendered HTML behind a valid certificate, and ``robots.txt`` restricts only printer
views. Everything below stays clear of those.

The one piece of era drift is the URL slug, which was renamed twice::

    2004  /loksabha2004/     2019  /LokSabha2019/
    2009  /ls2009/           2024  /LokSabha2024/
    2014  /ls2014/

The table markup inside, checked across all five, is identical — so this module versions the
addresses and the parser stays single-version. Listings paginate at eighteen rows.

**Denominator note.** The ``candidates_analyzed`` view covers candidates whose affidavits ADR
could actually read, which is not quite the same as candidates who stood. Rates computed from
it are "of candidates analysed", and the site must say so rather than quietly implying
complete coverage.
"""

from __future__ import annotations

from dataclasses import dataclass

SOURCE = "myneta"

BASE = "https://myneta.info"

#: Listings return this many candidates per page; the first empty page ends the crawl.
ROWS_PER_PAGE = 18


@dataclass(frozen=True, slots=True)
class Election:
    """One election covered by MyNeta."""

    slug: str
    """Path segment on myneta.info, e.g. ``LokSabha2024``."""

    year: int
    house: str
    label: str

    @property
    def key_prefix(self) -> str:
        return f"{self.house.lower().replace(' ', '_')}/{self.year}"


#: The Lok Sabha series. Deliberately ordered oldest first so a backfill reads chronologically.
LOK_SABHA: tuple[Election, ...] = (
    Election("loksabha2004", 2004, "Lok Sabha", "Lok Sabha 2004"),
    Election("ls2009", 2009, "Lok Sabha", "Lok Sabha 2009"),
    Election("ls2014", 2014, "Lok Sabha", "Lok Sabha 2014"),
    Election("LokSabha2019", 2019, "Lok Sabha", "Lok Sabha 2019"),
    Election("LokSabha2024", 2024, "Lok Sabha", "Lok Sabha 2024"),
)

ELECTIONS_BY_SLUG = {election.slug: election for election in LOK_SABHA}


class View:
    """The listing views worth archiving.

    Each has a ``winner_`` counterpart covering only those who won. Both are collected
    because the criminalisation rate among candidates a party *fielded* and among those it
    actually *elected* are different numbers, and the gap between them is itself a finding.
    """

    ALL_CANDIDATES = "candidates_analyzed"
    WINNERS = "winner_analyzed"
    CANDIDATES_WITH_CASES = "crime"
    WINNERS_WITH_CASES = "winner_crime"
    CANDIDATES_SERIOUS_CASES = "serious_crime"
    WINNERS_SERIOUS_CASES = "winner_serious_crime"


#: Views collected by default: the full analysed set and the winners, for each election.
DEFAULT_VIEWS: tuple[str, ...] = (View.ALL_CANDIDATES, View.WINNERS)


def page_url(election: Election, view: str, page: int = 1, sort: str = "candidate") -> str:
    """Address of one page of one listing.

    ``sort`` is pinned rather than left to the site's default so that repeated crawls return
    rows in a stable order, which keeps archived pages byte-comparable between runs and stops
    the manifest recording spurious revisions.
    """
    if page < 1:
        raise ValueError(f"page numbers start at 1, got {page}")
    url = f"{BASE}/{election.slug}/index.php?action=summary&subAction={view}&sort={sort}"
    return url if page == 1 else f"{url}&page={page}"


def document_key(election: Election, view: str, page: int) -> str:
    """Manifest key for one archived page, e.g. ``LokSabha2024/winner_analyzed/p003``."""
    return f"{election.slug}/{view}/p{page:03d}"
