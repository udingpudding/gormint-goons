"""Canonical filesystem locations.

Everything resolves from the repository root so the pipeline behaves the same whether it is
run from the repo, from CI, or from a cron job with an arbitrary working directory. The
local checkout lives under a directory whose name ends in a space, so paths are always
handled as ``Path`` objects rather than interpolated into shell strings.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DATA = REPO_ROOT / "data"

#: Immutable source documents, content-addressed. Not committed — reproducible from MANIFEST.
RAW = DATA / "raw"

#: Tidy intermediate output of the parse stage.
NORMALIZED = DATA / "normalized"

#: Published Parquet the website reads. Committed.
PUBLIC = DATA / "public"

#: Provenance record: one JSON object per archived document. Committed, so the repository
#: proves what was fetched and when even though the bytes themselves are not stored in git.
MANIFEST = DATA / "manifest.jsonl"


def ensure_dirs() -> None:
    """Create the data directories if they are missing. Safe to call repeatedly."""
    for directory in (RAW, NORMALIZED, PUBLIC):
        directory.mkdir(parents=True, exist_ok=True)
