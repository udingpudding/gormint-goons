"""The provenance record.

Every document the pipeline ever fetches gets one line here. The manifest is committed to
git even though the documents themselves are not, which is what lets the repository stand
behind a claim like "this figure came from the May 2024 Flash Report, SHA-256 abc123,
fetched on this date" without shipping gigabytes of PDFs.

Format is JSON Lines, sorted deterministically, because the diff has to be readable. A pull
request that archives a new month should show up as a handful of added lines, and a
government host quietly replacing a published document shows up as a new entry with the same
``(source, key)`` but a different ``sha256``. That drift is a finding in its own right, so
the manifest is designed to preserve it rather than overwrite it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from pipeline import paths


class ManifestEntry(BaseModel):
    """One archived document, at one point in time."""

    source: str
    """Logical dataset, e.g. ``mospi_flash``."""

    key: str
    """Stable human identifier within the source, e.g. ``2024-05``. Not unique over time:
    re-archiving a revised document creates a second entry with the same key."""

    url: str
    sha256: str
    bytes: int
    content_type: str | None = None
    http_status: int = 200
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def identity(self) -> tuple[str, str, str]:
        """What makes an entry a duplicate: same document, same bytes, same source."""
        return (self.source, self.key, self.sha256)

    def _sort_key(self) -> tuple[str, str, str]:
        return (self.source, self.key, self.fetched_at.isoformat())


class Manifest:
    """Append-only collection of :class:`ManifestEntry`, persisted as JSON Lines."""

    def __init__(self, entries: Iterable[ManifestEntry] = (), path: Path | None = None) -> None:
        self.path = path or paths.MANIFEST
        self._entries: list[ManifestEntry] = list(entries)
        self._identities = {entry.identity for entry in self._entries}

    # -- persistence ----------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Manifest:
        """Read a manifest from disk. A missing file is an empty manifest, not an error."""
        path = path or paths.MANIFEST
        if not path.exists():
            return cls(path=path)
        with path.open(encoding="utf-8") as handle:
            entries = [ManifestEntry.model_validate_json(line) for line in handle if line.strip()]
        return cls(entries, path=path)

    def save(self) -> None:
        """Write the manifest, sorted, via a temporary file so an interrupted run cannot
        truncate the provenance record."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(self._entries, key=ManifestEntry._sort_key)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for entry in ordered:
                handle.write(entry.model_dump_json() + "\n")
        tmp.replace(self.path)

    # -- querying -------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[ManifestEntry]:
        return iter(self._entries)

    def add(self, entry: ManifestEntry) -> bool:
        """Record an entry. Returns ``False`` if this exact document was already recorded.

        Deduplication is on content, not URL: re-fetching an unchanged file adds nothing,
        while a changed file is always recorded as a new revision.
        """
        if entry.identity in self._identities:
            return False
        self._entries.append(entry)
        self._identities.add(entry.identity)
        return True

    def latest(self, source: str, key: str) -> ManifestEntry | None:
        """Most recently fetched revision of one document, or ``None``."""
        candidates = [e for e in self._entries if e.source == source and e.key == key]
        if not candidates:
            return None
        return max(candidates, key=lambda e: e.fetched_at)

    def revisions(self, source: str, key: str) -> list[ManifestEntry]:
        """Every archived revision of one document, oldest first.

        More than one entry means the publisher changed a document already released, which
        for the MoSPI reports means restated costs or completion dates.
        """
        candidates = [e for e in self._entries if e.source == source and e.key == key]
        return sorted(candidates, key=lambda e: e.fetched_at)

    def keys(self, source: str) -> list[str]:
        """Distinct document keys archived for a source, sorted."""
        return sorted({e.key for e in self._entries if e.source == source})

    def has_content(self, source: str, sha256: str) -> bool:
        """Whether these exact bytes are already archived under this source."""
        return any(e.source == source and e.sha256 == sha256 for e in self._entries)


def entry_to_json(entry: ManifestEntry) -> str:
    """Serialise one entry. Exposed for tests and debugging."""
    return json.dumps(json.loads(entry.model_dump_json()), sort_keys=True)
