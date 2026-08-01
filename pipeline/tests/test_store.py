"""Content-addressed storage. The properties that matter are that identical bytes are stored
once, that a partially written file can never masquerade as a complete one, and that reading
back a corrupted document fails loudly rather than feeding bad numbers into the site."""

from __future__ import annotations

import pytest

from pipeline.archive import store


def test_suffix_prefers_the_url_extension():
    """Government hosts routinely serve PDFs as octet-stream, so the URL wins."""
    assert (
        store.suffix_for("https://mospi.gov.in/FR_may_2014.pdf", "application/octet-stream")
        == ".pdf"
    )


def test_suffix_falls_back_to_content_type_when_url_is_uninformative():
    assert (
        store.suffix_for(
            "https://myneta.info/ls2014/index.php?action=summary", "text/html; charset=utf-8"
        )
        == ".html"
    )


def test_suffix_normalises_htm_to_html():
    assert store.suffix_for("https://example.gov.in/page.htm", None) == ".html"


def test_unknown_types_get_a_neutral_suffix():
    assert store.suffix_for("https://example.gov.in/download", "application/x-thing") == ".bin"


def test_writes_are_content_addressed(tmp_path):
    digest, path = store.write_blob("mospi_flash", b"%PDF-1.4 report", ".pdf", root=tmp_path)

    assert path.exists()
    assert path.name == f"{digest}.pdf"
    assert path.parent.name == digest[:2]
    assert path.read_bytes() == b"%PDF-1.4 report"


def test_writing_the_same_content_twice_is_a_no_op(tmp_path):
    first_digest, first_path = store.write_blob("mospi_flash", b"same", ".pdf", root=tmp_path)
    second_digest, second_path = store.write_blob("mospi_flash", b"same", ".pdf", root=tmp_path)

    assert first_digest == second_digest
    assert first_path == second_path
    assert len(list(first_path.parent.iterdir())) == 1


def test_different_content_lands_side_by_side(tmp_path):
    """Two revisions of one report must coexist rather than overwrite."""
    _, original = store.write_blob("mospi_flash", b"original figures", ".pdf", root=tmp_path)
    _, restated = store.write_blob("mospi_flash", b"restated figures", ".pdf", root=tmp_path)

    assert original != restated
    assert original.exists() and restated.exists()


def test_no_temporary_files_survive_a_write(tmp_path):
    store.write_blob("mospi_flash", b"content", ".pdf", root=tmp_path)

    leftovers = [p for p in tmp_path.rglob("*") if p.name.startswith(".") or p.suffix == ".tmp"]

    assert leftovers == []


def test_read_returns_what_was_written(tmp_path):
    digest, _ = store.write_blob("mospi_flash", b"payload", ".pdf", root=tmp_path)

    assert store.read_blob("mospi_flash", digest, ".pdf", root=tmp_path) == b"payload"


def test_reading_a_corrupted_document_raises(tmp_path):
    """Disk corruption must not propagate silently into published figures."""
    digest, path = store.write_blob("mospi_flash", b"payload", ".pdf", root=tmp_path)
    path.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="corrupt"):
        store.read_blob("mospi_flash", digest, ".pdf", root=tmp_path)


def test_sources_are_kept_apart(tmp_path):
    digest, mospi_path = store.write_blob("mospi_flash", b"shared", ".pdf", root=tmp_path)
    _, myneta_path = store.write_blob("myneta", b"shared", ".pdf", root=tmp_path)

    assert mospi_path != myneta_path
    assert store.read_blob("myneta", digest, ".pdf", root=tmp_path) == b"shared"
