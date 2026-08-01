"""Walking a paginated listing and archiving every page of it.

MyNeta paginates at eighteen rows with no total count and no "next" link, so the only way to
learn where a listing ends is to ask for a page and find no candidates on it. That means the
crawler has to look inside each page as it goes — but only to decide whether to continue.
The archived bytes remain the source of truth, and the parse stage re-reads them from disk
rather than trusting anything computed here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

from pipeline.archive.fetcher import Fetcher, Outcome
from pipeline.archive.sources import myneta
from pipeline.archive.store import read_blob
from pipeline.parsers.myneta import parse_listing

log = logging.getLogger(__name__)

#: Stop after this many pages for one listing. The Lok Sabha has 543 seats and the largest
#: candidate list is roughly 8,400, so anything past this means pagination is not terminating
#: and the crawl should stop rather than walk forever.
MAX_PAGES = 600


@dataclass(slots=True)
class CrawlSummary:
    election: str
    view: str
    pages: int = 0
    archived: int = 0
    skipped: int = 0
    revised: int = 0
    failed: int = 0
    rows_seen: int = 0

    def __str__(self) -> str:
        return (
            f"{self.election}/{self.view}: {self.pages} pages, {self.rows_seen} rows "
            f"({self.archived} new, {self.skipped} cached, {self.revised} revised, "
            f"{self.failed} failed)"
        )


def crawl_listing(
    fetcher: Fetcher,
    election: myneta.Election,
    view: str,
    *,
    max_pages: int = MAX_PAGES,
) -> CrawlSummary:
    """Archive every page of one election's listing, stopping at the first empty page."""
    summary = CrawlSummary(election=election.slug, view=view)

    for page in range(1, max_pages + 1):
        url = myneta.page_url(election, view, page)
        key = myneta.document_key(election, view, page)
        result = fetcher.fetch(myneta.SOURCE, key, url)

        if result.outcome is Outcome.FAILED or result.outcome is Outcome.BLOCKED:
            summary.failed += 1
            log.warning("Stopping %s/%s at page %s: %s", election.slug, view, page, result.detail)
            break

        html = _page_bytes(fetcher, result)
        if html is None:
            summary.failed += 1
            break

        rows = parse_listing(
            html,
            election_slug=election.slug,
            election_year=election.year,
            house=election.house,
            view=view,
        )
        if not rows:
            break

        summary.pages += 1
        summary.rows_seen += len(rows)
        if result.outcome is Outcome.ARCHIVED:
            summary.archived += 1
        elif result.outcome is Outcome.REVISED:
            summary.revised += 1
        else:
            summary.skipped += 1
    else:
        log.warning("%s/%s hit the %s page ceiling", election.slug, view, max_pages)

    return summary


def _page_bytes(fetcher: Fetcher, result) -> bytes | None:
    """The freshly fetched body, or the archived copy when the page was already held.

    Re-reading from the archive on a skip is what keeps a re-run cheap: the second crawl of
    an election makes no requests at all yet still learns where the pagination ends.
    """
    if result.path is not None:
        return result.path.read_bytes()
    if result.entry is not None:
        try:
            return read_blob(result.entry.source, result.entry.sha256, ".html", root=fetcher.root)
        except (OSError, ValueError) as exc:
            log.warning("Could not read archived %s: %s", result.key, exc)
    return None


def crawl_elections(
    fetcher: Fetcher,
    elections: tuple[myneta.Election, ...] = myneta.LOK_SABHA,
    views: tuple[str, ...] = myneta.DEFAULT_VIEWS,
) -> Iterator[CrawlSummary]:
    """Archive several elections, yielding a summary as each listing finishes so a long
    backfill reports progress instead of going quiet for an hour."""
    for election in elections:
        for view in views:
            yield crawl_listing(fetcher, election, view)
