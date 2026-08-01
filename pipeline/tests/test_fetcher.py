"""The fetcher's contract: archive a document once, notice when a publisher changes one it
already released, never re-request what it already has, and fail one document without taking
down a backfill of two hundred."""

from __future__ import annotations

import httpx
import pytest
import respx

from pipeline.archive.fetcher import Fetcher, Outcome
from pipeline.archive.manifest import Manifest

REPORT_URL = "https://mospi.gov.in/FlashReport_May_2024.pdf"
ROBOTS_URL = "https://mospi.gov.in/robots.txt"


@pytest.fixture(autouse=True)
def no_retry_backoff():
    """Retries are real behaviour worth testing; waiting several seconds for them is not."""
    Fetcher._request.retry.sleep = lambda _seconds: None


@pytest.fixture
def fetcher(tmp_path):
    """A fetcher wired to a temporary archive, with courtesy delays and robots.txt disabled
    so tests exercise fetching rather than politeness."""
    manifest = Manifest(path=tmp_path / "manifest.jsonl")
    with Fetcher(
        manifest,
        min_interval=0.0,
        respect_robots=False,
        root=tmp_path / "raw",
    ) as f:
        yield f


@respx.mock
def test_first_fetch_archives_the_document(fetcher):
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4 flash report", headers={"content-type": "application/pdf"}
        )
    )

    result = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    assert result.outcome is Outcome.ARCHIVED
    assert result.path.exists()
    assert result.path.suffix == ".pdf"
    assert result.entry.bytes == len(b"%PDF-1.4 flash report")
    assert len(fetcher.manifest) == 1


@respx.mock
def test_already_archived_documents_are_not_re_requested(fetcher):
    """Backfilling two hundred historical reports must be cheap to re-run."""
    route = respx.get(REPORT_URL).mock(return_value=httpx.Response(200, content=b"report"))
    fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    result = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    assert result.outcome is Outcome.SKIPPED
    assert route.call_count == 1


@respx.mock
def test_revalidation_honours_a_not_modified_response(fetcher):
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(200, content=b"report", headers={"etag": '"v1"'})
    )
    fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    respx.get(REPORT_URL).mock(return_value=httpx.Response(304))
    result = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL, revalidate=True)

    assert result.outcome is Outcome.UNCHANGED
    assert len(fetcher.manifest) == 1


@respx.mock
def test_revalidation_sends_the_stored_validators(fetcher):
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(
            200,
            content=b"report",
            headers={"etag": '"v1"', "last-modified": "Wed, 01 May 2024 00:00:00 GMT"},
        )
    )
    fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    route = respx.get(REPORT_URL).mock(return_value=httpx.Response(304))
    fetcher.fetch("mospi_flash", "2024-05", REPORT_URL, revalidate=True)

    sent = route.calls.last.request.headers
    assert sent["If-None-Match"] == '"v1"'
    assert sent["If-Modified-Since"] == "Wed, 01 May 2024 00:00:00 GMT"


@respx.mock
def test_a_republished_document_is_recorded_as_a_revision(fetcher):
    """MoSPI restates costs and completion dates between editions. Catching that is the
    point of revalidating at all."""
    respx.get(REPORT_URL).mock(return_value=httpx.Response(200, content=b"original figures"))
    first = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    respx.get(REPORT_URL).mock(return_value=httpx.Response(200, content=b"restated figures"))
    second = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL, revalidate=True)

    assert second.outcome is Outcome.REVISED
    assert len(fetcher.manifest) == 2
    assert first.path.exists() and second.path.exists()
    assert first.path != second.path


@respx.mock
def test_unchanged_bytes_are_not_recorded_again_even_without_a_304(fetcher):
    """Hosts that ignore conditional requests still must not bloat the manifest."""
    respx.get(REPORT_URL).mock(return_value=httpx.Response(200, content=b"report"))
    fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    result = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL, revalidate=True)

    assert result.outcome is Outcome.UNCHANGED
    assert len(fetcher.manifest) == 1


@respx.mock
def test_force_refetches_without_conditional_headers(fetcher):
    respx.get(REPORT_URL).mock(
        return_value=httpx.Response(200, content=b"report", headers={"etag": '"v1"'})
    )
    fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    route = respx.get(REPORT_URL).mock(return_value=httpx.Response(200, content=b"report"))
    fetcher.fetch("mospi_flash", "2024-05", REPORT_URL, force=True)

    assert "If-None-Match" not in route.calls.last.request.headers


@respx.mock
def test_a_missing_document_fails_without_raising(fetcher):
    """One dead archive URL must not abort a backfill."""
    respx.get(REPORT_URL).mock(return_value=httpx.Response(404))

    result = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    assert result.outcome is Outcome.FAILED
    assert result.ok is False
    assert "404" in result.detail
    assert len(fetcher.manifest) == 0


@respx.mock
def test_client_errors_are_not_retried(fetcher):
    """Retrying a 404 is rude and pointless."""
    route = respx.get(REPORT_URL).mock(return_value=httpx.Response(404))

    fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    assert route.call_count == 1


@respx.mock
def test_server_errors_are_retried_then_succeed(fetcher):
    route = respx.get(REPORT_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, content=b"report"),
        ]
    )

    result = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    assert result.outcome is Outcome.ARCHIVED
    assert route.call_count == 3


@respx.mock
def test_persistent_server_errors_give_up_and_report(fetcher):
    respx.get(REPORT_URL).mock(return_value=httpx.Response(503))

    result = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    assert result.outcome is Outcome.FAILED


@respx.mock
def test_throttling_is_treated_as_retryable(fetcher):
    route = respx.get(REPORT_URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, content=b"report")]
    )

    result = fetcher.fetch("mospi_flash", "2024-05", REPORT_URL)

    assert result.outcome is Outcome.ARCHIVED
    assert route.call_count == 2


@respx.mock
def test_robots_disallow_blocks_the_fetch(tmp_path):
    respx.get(ROBOTS_URL).mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n")
    )
    route = respx.get(REPORT_URL).mock(return_value=httpx.Response(200, content=b"report"))

    with Fetcher(
        Manifest(path=tmp_path / "manifest.jsonl"),
        min_interval=0.0,
        root=tmp_path / "raw",
    ) as f:
        result = f.fetch("mospi_flash", "2024-05", REPORT_URL)

    assert result.outcome is Outcome.BLOCKED
    assert route.call_count == 0


@respx.mock
def test_a_missing_robots_file_means_no_restriction(tmp_path):
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    respx.get(REPORT_URL).mock(return_value=httpx.Response(200, content=b"report"))

    with Fetcher(
        Manifest(path=tmp_path / "manifest.jsonl"),
        min_interval=0.0,
        root=tmp_path / "raw",
    ) as f:
        result = f.fetch("mospi_flash", "2024-05", REPORT_URL)

    assert result.outcome is Outcome.ARCHIVED


@respx.mock
def test_robots_is_fetched_once_per_host(tmp_path):
    robots = respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    respx.get(url__startswith="https://mospi.gov.in/FlashReport").mock(
        return_value=httpx.Response(200, content=b"report")
    )

    with Fetcher(
        Manifest(path=tmp_path / "manifest.jsonl"),
        min_interval=0.0,
        root=tmp_path / "raw",
    ) as f:
        f.fetch("mospi_flash", "2024-05", "https://mospi.gov.in/FlashReport_May_2024.pdf")
        f.fetch("mospi_flash", "2024-06", "https://mospi.gov.in/FlashReport_June_2024.pdf")

    assert robots.call_count == 1


@respx.mock
def test_http_urls_are_upgraded_to_https(fetcher):
    """The MoSPI archive links to some reports over plain http."""
    respx.get("https://uatipm.mospi.gov.in/FR_oct_2022.pdf").mock(
        return_value=httpx.Response(200, content=b"report")
    )

    result = fetcher.fetch("mospi_flash", "2022-10", "http://uatipm.mospi.gov.in/FR_oct_2022.pdf")

    assert result.outcome is Outcome.ARCHIVED
    assert result.entry.url.startswith("https://")


@respx.mock
def test_manifest_survives_a_run(tmp_path):
    respx.get(REPORT_URL).mock(return_value=httpx.Response(200, content=b"report"))
    manifest_path = tmp_path / "manifest.jsonl"

    with Fetcher(
        Manifest(path=manifest_path), min_interval=0.0, respect_robots=False, root=tmp_path / "raw"
    ) as f:
        f.fetch("mospi_flash", "2024-05", REPORT_URL)
        f.save()

    assert len(Manifest.load(manifest_path)) == 1
