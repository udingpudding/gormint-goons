"""Locating MoSPI Flash Reports on Central Sector Projects.

One monthly PDF per report, each covering every central-sector infrastructure project of
₹150 crore or more — original cost against revised cost, original completion date against
anticipated completion date. Because the series is monthly and project identity is stable
across editions, the reports together form a project-month panel. That panel is what makes
it possible to count how many times a project's completion date has moved, which no one
publishes directly.

Finding the files is the hard part. There is no index page, no directory listing, and no
API: the portal at ``paimana-proj.mospi.gov.in`` is unreachable, ``ipm.mospi.gov.in`` serves
only a redirect shell, and ``Home/ViewPdf/<id>`` returns 500 without the very ``path``
parameter you would be trying to discover. What does work is that documents sit at
predictable *folders* under fiscal-year directories — the file names inside them are simply
not predictable.

A sample of what one directory actually contains::

    FR_may_2014.pdf          FR_APril_2023.pdf      FRApril2025.pdf
    FR_sept_2023.pdf         FR_july1_2023.pdf      FR_JUNE_2025.pdf
    FR_oct_2022.pdf          FlashReport_August_2025_c.pdf

Capitalisation drifts, month names are abbreviated inconsistently, separators come and go,
and some names carry a stray ``1`` or ``_c``. So discovery generates the plausible spellings
for a month and probes them with HEAD requests, falling back to a hand-recorded table for the
genuinely unguessable ones. Results are cached to disk, because this only needs to be correct
once per month.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from pipeline import paths

log = logging.getLogger(__name__)

SOURCE = "mospi_flash"

#: Fiscal-year folders holding the historical series. Confirmed live back to FY 2014-15.
ARCHIVE_BASE = "https://ipm.mospi.gov.in/Content/ArchiveReport/flash"

#: The ministry's main site also publishes recent months, sometimes when the archive has not
#: caught up. Worth probing as a secondary location.
PUBLICATION_BASE = "https://mospi.gov.in/sites/default/files/publication_reports"

#: Cached discovery results, committed so a backfill is not repeated on every machine.
URL_CACHE = paths.DATA / "sources" / "mospi_flash_urls.json"

_MONTH_SPELLINGS: dict[int, tuple[str, ...]] = {
    1: ("jan", "january"),
    2: ("feb", "february"),
    3: ("mar", "march"),
    4: ("apr", "april"),
    5: ("may",),
    6: ("jun", "june"),
    7: ("jul", "july"),
    8: ("aug", "august"),
    9: ("sep", "sept", "september"),
    10: ("oct", "october"),
    11: ("nov", "november"),
    12: ("dec", "december"),
}

#: Names that no generator would produce. Recorded by hand as they are found, keyed by
#: report month. Consulted before the generated candidates.
KNOWN_FILENAMES: dict[str, str] = {
    "2023-07": "FR_july1_2023.pdf",
}


@dataclass(frozen=True, slots=True)
class ReportMonth:
    """One edition of the Flash Report."""

    year: int
    month: int

    @property
    def key(self) -> str:
        """Manifest key, e.g. ``2024-05``."""
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def fiscal_year(self) -> str:
        """Indian fiscal year folder, e.g. ``2023-24``. April starts the year, so January
        to March belong to the fiscal year that began the previous April."""
        start = self.year if self.month >= 4 else self.year - 1
        return f"{start}-{(start + 1) % 100:02d}"

    def __str__(self) -> str:
        return self.key


def months_between(start: ReportMonth, end: ReportMonth) -> list[ReportMonth]:
    """Every report month from ``start`` to ``end`` inclusive."""
    if (end.year, end.month) < (start.year, start.month):
        raise ValueError(f"{end} precedes {start}")
    out, year, month = [], start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(ReportMonth(year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def _name_variants(report: ReportMonth) -> list[str]:
    """Plausible file names for one month, most likely first.

    Covers the observed axes of variation: ``FR`` versus ``FlashReport``, abbreviated versus
    full month names, underscores present or absent, and lower/title/upper casing.
    """
    seen: dict[str, None] = {}
    for spelling in _MONTH_SPELLINGS[report.month]:
        for cased in (spelling, spelling.capitalize(), spelling.upper()):
            for stem, separator in (
                ("FR", "_"),
                ("FlashReport", "_"),
                ("FR", ""),
                ("FlashReport", ""),
            ):
                if separator:
                    seen[f"{stem}{separator}{cased}{separator}{report.year}.pdf"] = None
                else:
                    seen[f"{stem}{cased}{report.year}.pdf"] = None
                    seen[f"{stem}_{cased}{report.year}.pdf"] = None
    return list(seen)


def candidate_urls(report: ReportMonth) -> list[str]:
    """Every URL worth trying for one month, most likely first.

    A hand-recorded name, if there is one, is tried before anything generated.
    """
    urls: list[str] = []
    known = KNOWN_FILENAMES.get(report.key)
    if known:
        urls.append(f"{ARCHIVE_BASE}/{report.fiscal_year}/{known}")

    for name in _name_variants(report):
        urls.append(f"{ARCHIVE_BASE}/{report.fiscal_year}/{name}")

    # Recent months sometimes appear on the main site before the archive folder is updated.
    if report.year >= 2024:
        for name in _name_variants(report):
            urls.append(f"{PUBLICATION_BASE}/{name}")

    return urls


def load_url_cache(path: Path | None = None) -> dict[str, str]:
    """Previously discovered month-to-URL mappings."""
    path = path or URL_CACHE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_url_cache(urls: dict[str, str], path: Path | None = None) -> None:
    """Persist discovery results, sorted, so the committed file diffs cleanly."""
    path = path or URL_CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {key: urls[key] for key in sorted(urls)}
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")


def discover(
    client: httpx.Client,
    reports: list[ReportMonth],
    *,
    cache: dict[str, str] | None = None,
    on_progress=None,
) -> tuple[dict[str, str], list[ReportMonth]]:
    """Find the URL for each report month by probing candidates with HEAD requests.

    Cached months are not re-probed. Returns the month-to-URL mapping and the list of months
    nothing was found for — those are the ones needing a hand-recorded entry in
    :data:`KNOWN_FILENAMES`.
    """
    found = dict(cache or {})
    missing: list[ReportMonth] = []

    for report in reports:
        if report.key in found:
            continue

        url = _probe(client, report)
        if url:
            found[report.key] = url
        else:
            missing.append(report)
        if on_progress:
            on_progress(report, url)

    return found, missing


def _probe(client: httpx.Client, report: ReportMonth) -> str | None:
    for url in candidate_urls(report):
        try:
            response = client.head(url, follow_redirects=True, timeout=25.0)
        except httpx.HTTPError:
            continue
        # The host answers 200 with an HTML error body for unknown paths, so the content
        # type is what actually distinguishes a real report from a miss.
        content_type = response.headers.get("content-type", "")
        if response.status_code == 200 and "pdf" in content_type.lower():
            return url
    return None
