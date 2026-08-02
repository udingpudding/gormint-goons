"""Stage two: archived pages into tidy tables.

Reads exclusively from the archive and the manifest, never from the network, so this can be
re-run as often as the parser changes. Where a page has been archived more than once — MyNeta
corrects affidavit data after publication — only the most recent revision is parsed. Earlier
revisions stay on disk, and comparing them is how a correction becomes visible, but the
published tables should reflect the current state rather than a mixture of vintages.

Three kinds of archived page, distinguished by their manifest key:

``<slug>/registry``
    An election's landing page, carrying the state-to-constituency map.

``<slug>/constituency/<id>``
    Every candidate who stood in one seat. This is the canonical candidate source: it covers
    all candidates rather than a filtered subset, and carries state, age and person ids.

``<slug>/<view>/p<n>``
    A paginated summary listing. Kept for reconciliation — winners counted from these must
    agree with winners counted from the constituency pages, and a disagreement means one of
    the two parsers is wrong.
"""

from __future__ import annotations

import logging

import polars as pl

from pipeline import paths
from pipeline.archive.manifest import Manifest
from pipeline.archive.sources import myneta
from pipeline.archive.store import read_blob
from pipeline.parsers.myneta import parse_constituency, parse_listing, parse_seat_registry

log = logging.getLogger(__name__)

CANDIDATES_PARQUET = paths.NORMALIZED / "myneta_candidates.parquet"
SEATS_PARQUET = paths.NORMALIZED / "myneta_seats.parquet"

SCHEMA = {
    "election_slug": pl.Utf8,
    "election_year": pl.Int32,
    "house": pl.Utf8,
    "view": pl.Utf8,
    "is_winner": pl.Boolean,
    "serial": pl.Int32,
    "name": pl.Utf8,
    "state": pl.Utf8,
    "constituency": pl.Utf8,
    "constituency_id": pl.Int32,
    "candidate_id": pl.Int32,
    "reservation": pl.Utf8,
    "party": pl.Utf8,
    "criminal_cases": pl.Int32,
    "education": pl.Utf8,
    "age": pl.Int32,
    "assets_rupees": pl.Int64,
    "liabilities_rupees": pl.Int64,
    "is_bye_election": pl.Boolean,
}

SEAT_SCHEMA = {
    "election_slug": pl.Utf8,
    "election_year": pl.Int32,
    "state": pl.Utf8,
    "state_id": pl.Int32,
    "constituency": pl.Utf8,
    "constituency_id": pl.Int32,
    "reservation": pl.Utf8,
}


def normalize_myneta(manifest: Manifest | None = None) -> tuple[int, int]:
    """Parse every archived MyNeta page. Returns ``(candidate rows, seats)``."""
    manifest = manifest if manifest is not None else Manifest.load()

    seats = _parse_registries(manifest)
    seat_frame = pl.DataFrame([s.model_dump() for s in seats], schema=SEAT_SCHEMA)
    seat_lookup = {(s.election_slug, s.constituency_id): s for s in seats}

    rows: list[dict] = []
    for key in manifest.keys(myneta.SOURCE):
        entry = manifest.latest(myneta.SOURCE, key)
        if entry is None or key.endswith("/registry"):
            continue

        slug = key.split("/")[0]
        election = myneta.ELECTIONS_BY_SLUG.get(slug)
        if election is None:
            log.warning("Archived page %s is from an unknown election; skipping", key)
            continue

        html = _read(entry, key)
        if html is None:
            continue

        if "/constituency/" in key:
            seat = seat_lookup.get((slug, int(key.rsplit("/", 1)[1])))
            if seat is None:
                log.warning("No seat registered for %s; skipping", key)
                continue
            parsed = parse_constituency(
                html,
                election_slug=slug,
                election_year=election.year,
                house=election.house,
                seat=seat,
            )
        else:
            parsed = parse_listing(
                html,
                election_slug=slug,
                election_year=election.year,
                house=election.house,
                view=key.split("/")[1],
            )
        rows.extend(candidate.model_dump() for candidate in parsed)

    frame = pl.DataFrame(rows, schema=SCHEMA) if rows else pl.DataFrame(schema=SCHEMA)

    # A person appears once per view. Within the constituency view the seat id makes the row
    # unique; within a summary listing only the name and seat are available.
    frame = frame.unique(
        subset=["election_slug", "view", "constituency", "name", "party"], keep="first"
    ).sort(["election_year", "view", "state", "constituency", "name"])

    CANDIDATES_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(CANDIDATES_PARQUET)
    seat_frame.write_parquet(SEATS_PARQUET)

    log.info("Normalized %s candidate rows across %s seats", frame.height, seat_frame.height)
    return frame.height, seat_frame.height


def _parse_registries(manifest: Manifest):
    seats = []
    for key in manifest.keys(myneta.SOURCE):
        if not key.endswith("/registry"):
            continue
        entry = manifest.latest(myneta.SOURCE, key)
        if entry is None:
            continue
        election = myneta.ELECTIONS_BY_SLUG.get(key.split("/")[0])
        if election is None:
            continue
        html = _read(entry, key)
        if html is None:
            continue
        seats.extend(
            parse_seat_registry(html, election_slug=election.slug, election_year=election.year)
        )
    return seats


def _read(entry, key: str) -> bytes | None:
    try:
        return read_blob(entry.source, entry.sha256, ".html")
    except (OSError, ValueError) as exc:
        log.warning("Could not read %s: %s", key, exc)
        return None
