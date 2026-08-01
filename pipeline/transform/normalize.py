"""Stage two: archived pages into one tidy table of candidate rows.

Reads exclusively from the archive and the manifest, never from the network, so this can be
re-run as often as the parser changes. Where a page has been archived more than once — MyNeta
corrects affidavit data after publication — only the most recent revision is parsed. Earlier
revisions stay on disk, and comparing them is how a correction becomes visible, but the
published tables should reflect the current state rather than a mixture of vintages.
"""

from __future__ import annotations

import logging

import polars as pl

from pipeline import paths
from pipeline.archive.manifest import Manifest
from pipeline.archive.sources import myneta
from pipeline.archive.store import read_blob
from pipeline.parsers.myneta import parse_listing

log = logging.getLogger(__name__)

CANDIDATES_PARQUET = paths.NORMALIZED / "myneta_candidates.parquet"

SCHEMA = {
    "election_slug": pl.Utf8,
    "election_year": pl.Int32,
    "house": pl.Utf8,
    "view": pl.Utf8,
    "is_winner": pl.Boolean,
    "serial": pl.Int32,
    "name": pl.Utf8,
    "constituency": pl.Utf8,
    "reservation": pl.Utf8,
    "party": pl.Utf8,
    "criminal_cases": pl.Int32,
    "education": pl.Utf8,
    "assets_rupees": pl.Int64,
    "liabilities_rupees": pl.Int64,
}


def normalize_myneta(manifest: Manifest | None = None) -> int:
    """Parse every archived MyNeta listing into ``data/normalized/``. Returns the row count."""
    manifest = manifest if manifest is not None else Manifest.load()

    rows: list[dict] = []
    keys = manifest.keys(myneta.SOURCE)
    for key in keys:
        entry = manifest.latest(myneta.SOURCE, key)
        if entry is None:
            continue

        slug, view, _page = key.split("/")
        election = myneta.ELECTIONS_BY_SLUG.get(slug)
        if election is None:
            log.warning("Archived page %s is from an unknown election; skipping", key)
            continue

        try:
            html = read_blob(entry.source, entry.sha256, ".html")
        except (OSError, ValueError) as exc:
            log.warning("Could not read %s: %s", key, exc)
            continue

        parsed = parse_listing(
            html,
            election_slug=slug,
            election_year=election.year,
            house=election.house,
            view=view,
        )
        rows.extend(candidate.model_dump() for candidate in parsed)

    frame = pl.DataFrame(rows, schema=SCHEMA) if rows else pl.DataFrame(schema=SCHEMA)

    # One person can appear in both the all-candidates and winners listings. Keep both,
    # since the two are different populations, but drop exact duplicates that arise when
    # pagination overlaps.
    frame = frame.unique(subset=["election_slug", "view", "name", "constituency"], keep="first")
    frame = frame.sort(["election_year", "view", "party", "name"])

    CANDIDATES_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(CANDIDATES_PARQUET)
    log.info("Parsed %s pages into %s rows", len(keys), frame.height)
    return frame.height
