"""Content-addressed storage for archived source documents.

Documents are stored under their own SHA-256 rather than a human path. That is what allows
two revisions of "the May 2024 Flash Report" to coexist: when a ministry silently republishes
a report with restated figures, the new bytes hash differently, land beside the old ones, and
the change becomes visible instead of being overwritten. Human-readable identity lives in the
manifest, which maps ``(source, key)`` to a hash.

Layout::

    data/raw/<source>/<sha256[:2]>/<sha256><suffix>

The two-character shard keeps directory sizes reasonable once a decade of monthly reports and
several hundred thousand candidate pages have accumulated.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

from pipeline import paths

#: Content types we archive, mapped to the suffix used on disk.
_SUFFIX_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "text/html": ".html",
    "application/json": ".json",
    "text/csv": ".csv",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def suffix_for(url: str, content_type: str | None) -> str:
    """Pick a file suffix, preferring the URL's own extension over the served content type.

    Government hosts routinely serve PDFs as ``application/octet-stream`` or mislabel them as
    HTML, so the URL is the more reliable signal when it carries a recognisable extension.
    """
    extension = Path(urlparse(url).path).suffix.lower()
    if extension in {".pdf", ".html", ".htm", ".json", ".csv", ".xls", ".xlsx"}:
        return ".html" if extension == ".htm" else extension

    if content_type:
        base = content_type.split(";")[0].strip().lower()
        if base in _SUFFIX_BY_CONTENT_TYPE:
            return _SUFFIX_BY_CONTENT_TYPE[base]

    return ".bin"


def blob_path(source: str, sha256: str, suffix: str, root: Path | None = None) -> Path:
    root = root or paths.RAW
    return root / source / sha256[:2] / f"{sha256}{suffix}"


def write_blob(
    source: str,
    content: bytes,
    suffix: str,
    root: Path | None = None,
) -> tuple[str, Path]:
    """Store bytes and return ``(sha256, path)``.

    Writing is atomic — content goes to a temporary file and is renamed into place — so an
    interrupted run can never leave a truncated document that looks complete to a later pass.
    Storing content that is already present is a no-op.
    """
    digest = sha256_of(content)
    destination = blob_path(source, digest, suffix, root=root)

    if destination.exists() and destination.stat().st_size == len(content):
        return digest, destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    tmp.write_bytes(content)
    tmp.replace(destination)
    return digest, destination


def read_blob(source: str, sha256: str, suffix: str, root: Path | None = None) -> bytes:
    """Read an archived document back, verifying it still hashes to its own name.

    The check is cheap and guards against disk corruption silently propagating into published
    figures — the whole point of the archive is that stored bytes are trustworthy.
    """
    path = blob_path(source, sha256, suffix, root=root)
    content = path.read_bytes()
    actual = sha256_of(content)
    if actual != sha256:
        raise ValueError(f"Archived document {path} is corrupt: hashes to {actual}, not {sha256}")
    return content
