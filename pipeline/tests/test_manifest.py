"""The manifest is the provenance record, so its guarantees are tested directly: it must
survive a round trip, refuse to double-count identical documents, and preserve the history
when a publisher changes something it already released."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from pipeline.archive.manifest import Manifest, ManifestEntry


def make_entry(**overrides: object) -> ManifestEntry:
    defaults: dict[str, object] = {
        "source": "mospi_flash",
        "key": "2024-05",
        "url": "https://mospi.gov.in/FlashReport_May_2024.pdf",
        "sha256": "a" * 64,
        "bytes": 1024,
        "content_type": "application/pdf",
    }
    return ManifestEntry(**(defaults | overrides))


def test_round_trips_through_disk(tmp_path):
    path = tmp_path / "manifest.jsonl"
    manifest = Manifest(path=path)
    manifest.add(make_entry())
    manifest.add(make_entry(key="2024-06", sha256="b" * 64))
    manifest.save()

    reloaded = Manifest.load(path)

    assert len(reloaded) == 2
    assert reloaded.keys("mospi_flash") == ["2024-05", "2024-06"]
    assert reloaded.latest("mospi_flash", "2024-05").sha256 == "a" * 64


def test_missing_file_loads_as_empty(tmp_path):
    assert len(Manifest.load(tmp_path / "absent.jsonl")) == 0


def test_identical_document_is_not_recorded_twice(tmp_path):
    manifest = Manifest(path=tmp_path / "manifest.jsonl")

    assert manifest.add(make_entry()) is True
    assert manifest.add(make_entry()) is False
    assert len(manifest) == 1


def test_changed_document_is_kept_as_a_new_revision(tmp_path):
    """A ministry republishing a report with restated figures must not silently overwrite
    the original — the divergence is the interesting part."""
    manifest = Manifest(path=tmp_path / "manifest.jsonl")
    earlier = datetime(2026, 1, 1, tzinfo=UTC)

    manifest.add(make_entry(sha256="a" * 64, fetched_at=earlier))
    manifest.add(make_entry(sha256="c" * 64, fetched_at=earlier + timedelta(days=30)))

    revisions = manifest.revisions("mospi_flash", "2024-05")

    assert [r.sha256 for r in revisions] == ["a" * 64, "c" * 64]
    assert manifest.latest("mospi_flash", "2024-05").sha256 == "c" * 64
    assert manifest.keys("mospi_flash") == ["2024-05"]


def test_saved_file_is_sorted_so_diffs_stay_readable(tmp_path):
    path = tmp_path / "manifest.jsonl"
    manifest = Manifest(path=path)
    for key in ("2024-12", "2024-01", "2024-06"):
        manifest.add(make_entry(key=key, sha256=key.replace("-", "") * 8))
    manifest.save()

    keys = [line.split('"key":"')[1].split('"')[0] for line in path.read_text().splitlines()]

    assert keys == sorted(keys)


def test_has_content_is_scoped_to_the_source(tmp_path):
    manifest = Manifest(path=tmp_path / "manifest.jsonl")
    manifest.add(make_entry(sha256="d" * 64))

    assert manifest.has_content("mospi_flash", "d" * 64) is True
    assert manifest.has_content("myneta", "d" * 64) is False


def test_latest_returns_none_for_unknown_document(tmp_path):
    assert Manifest(path=tmp_path / "m.jsonl").latest("mospi_flash", "1999-01") is None


@pytest.mark.parametrize("field", ["source", "key", "url", "sha256", "bytes"])
def test_required_fields_are_enforced(field):
    payload = {
        "source": "s",
        "key": "k",
        "url": "https://example.gov.in/x.pdf",
        "sha256": "e" * 64,
        "bytes": 1,
    }
    del payload[field]

    with pytest.raises(ValidationError):
        ManifestEntry(**payload)
