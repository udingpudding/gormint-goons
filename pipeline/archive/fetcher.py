"""Stage one: fetch source documents and archive them, without ever parsing them.

Keeping the fetch stage ignorant of document contents is the decision the rest of the
pipeline leans on. Twenty years of MoSPI PDFs and five Lok Sabha cycles of MyNeta HTML will
not share a layout, and parsers will be rewritten repeatedly as that drift is discovered. If
parsing lived here, every parser fix would mean re-downloading hundreds of documents from
government hosts that rate-limit, intermittently 5xx, and sometimes block. Instead a parser
fix is a local re-run over bytes already on disk.

The fetcher is deliberately polite: one request at a time per host, a courtesy delay between
them, ``robots.txt`` honoured, and an identifying User-Agent so an administrator who sees the
traffic can find out who is responsible.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pipeline.archive import store
from pipeline.archive.manifest import Manifest, ManifestEntry

log = logging.getLogger(__name__)

USER_AGENT = (
    "GormintGoonsBot/0.1 (+https://github.com/udingpudding/gormint-goons) "
    "open governance data archive; contact via repository issues"
)


class Outcome(StrEnum):
    """What a fetch attempt did."""

    ARCHIVED = "archived"
    """New document; bytes stored and manifest updated."""

    REVISED = "revised"
    """Document already archived, but the publisher has since changed it. Both revisions
    are now stored. This is a finding, not a nuisance."""

    UNCHANGED = "unchanged"
    """Remote confirmed the document has not changed since it was archived."""

    SKIPPED = "skipped"
    """Already archived and revalidation was not requested; no request was made."""

    BLOCKED = "blocked"
    """Disallowed by robots.txt."""

    FAILED = "failed"
    """Request did not succeed after retries."""


@dataclass(slots=True)
class FetchResult:
    outcome: Outcome
    url: str
    key: str
    entry: ManifestEntry | None = None
    path: Path | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome not in (Outcome.FAILED, Outcome.BLOCKED)


class RetryableStatus(Exception):
    """A response worth trying again — server-side failure or explicit throttling."""


class RobotsCache:
    """Per-host ``robots.txt`` rules, fetched once.

    Convention on failure follows common practice: if ``robots.txt`` cannot be found the host
    has expressed no restriction and fetching proceeds; if the host errors while serving it,
    that is treated as a signal to stay away rather than an invitation to guess.
    """

    def __init__(self, client: httpx.Client, user_agent: str = USER_AGENT) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}"

        if host_key not in self._parsers:
            self._parsers[host_key] = self._load(host_key)

        parser = self._parsers[host_key]
        if parser is None:
            return True
        return parser.can_fetch(self._user_agent, url)

    def _load(self, host_key: str) -> urllib.robotparser.RobotFileParser | None:
        robots_url = f"{host_key}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            response = self._client.get(robots_url, timeout=15.0)
        except httpx.HTTPError as exc:
            log.warning("Could not reach %s (%s); proceeding without restrictions", robots_url, exc)
            return None

        if response.status_code >= 500:
            log.warning(
                "%s returned %s; treating host as off-limits", robots_url, response.status_code
            )
            parser.disallow_all = True
            return parser
        if response.status_code >= 400:
            return None

        parser.parse(response.text.splitlines())
        return parser


class Fetcher:
    """Archives documents into the content-addressed store, updating the manifest.

    Not thread-safe by design — the courtesy delay only means something if requests are
    serialised.
    """

    def __init__(
        self,
        manifest: Manifest | None = None,
        *,
        user_agent: str = USER_AGENT,
        min_interval: float = 1.5,
        timeout: float = 90.0,
        respect_robots: bool = True,
        root: Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.manifest = manifest if manifest is not None else Manifest.load()
        self.min_interval = min_interval
        self.respect_robots = respect_robots
        self.root = root

        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
        )
        self._robots = RobotsCache(self._client, user_agent)
        self._last_request_at: dict[str, float] = {}

    # -- lifecycle ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- fetching -------------------------------------------------------------------

    def fetch(
        self,
        source: str,
        key: str,
        url: str,
        *,
        revalidate: bool = False,
        force: bool = False,
    ) -> FetchResult:
        """Archive one document.

        Args:
            source: Logical dataset, e.g. ``mospi_flash``.
            key: Stable identifier within the source, e.g. ``2024-05``.
            url: Where to fetch it from.
            revalidate: Ask the server whether an already-archived document has changed.
                Off by default, which makes backfilling several hundred historical documents
                safely re-runnable without re-requesting any of them.
            force: Fetch unconditionally, ignoring both the manifest and cache validators.

        Returns:
            A :class:`FetchResult`. Failures are reported, not raised — a single unreachable
            document should not abort a backfill of two hundred.
        """
        url = _normalise(url)
        existing = self.manifest.latest(source, key)

        if existing is not None and not revalidate and not force:
            return FetchResult(Outcome.SKIPPED, url=url, key=key, entry=existing)

        if self.respect_robots and not self._robots.allowed(url):
            log.warning("robots.txt disallows %s", url)
            return FetchResult(Outcome.BLOCKED, url=url, key=key, detail="disallowed by robots.txt")

        headers: dict[str, str] = {}
        if existing is not None and not force:
            if existing.etag:
                headers["If-None-Match"] = existing.etag
            if existing.last_modified:
                headers["If-Modified-Since"] = existing.last_modified

        try:
            response = self._request(url, headers)
        except Exception as exc:  # reported to the caller, never fatal to a backfill
            log.warning("Failed to fetch %s: %s", url, exc)
            return FetchResult(Outcome.FAILED, url=url, key=key, detail=str(exc))

        if response.status_code == 304:
            return FetchResult(Outcome.UNCHANGED, url=url, key=key, entry=existing)

        if response.status_code >= 400:
            detail = f"HTTP {response.status_code}"
            log.warning("Failed to fetch %s: %s", url, detail)
            return FetchResult(Outcome.FAILED, url=url, key=key, detail=detail)

        content = response.content
        content_type = response.headers.get("content-type")
        suffix = store.suffix_for(url, content_type)
        digest, path = store.write_blob(source, content, suffix, root=self.root)

        entry = ManifestEntry(
            source=source,
            key=key,
            url=url,
            sha256=digest,
            bytes=len(content),
            content_type=content_type,
            http_status=response.status_code,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )

        is_new = self.manifest.add(entry)
        if not is_new:
            return FetchResult(Outcome.UNCHANGED, url=url, key=key, entry=existing, path=path)

        outcome = Outcome.REVISED if existing is not None else Outcome.ARCHIVED
        if outcome is Outcome.REVISED:
            log.info(
                "%s/%s has been changed by the publisher (%s -> %s)",
                source,
                key,
                existing.sha256[:12],
                digest[:12],
            )
        return FetchResult(outcome, url=url, key=key, entry=entry, path=path)

    def save(self) -> None:
        """Persist the manifest. Call once at the end of a run, not per document."""
        self.manifest.save()

    # -- internals ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(
            (RetryableStatus, httpx.TimeoutException, httpx.TransportError)
        ),
        wait=wait_exponential(multiplier=2, min=2, max=60),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _request(self, url: str, headers: dict[str, str]) -> httpx.Response:
        self._wait_turn(urlparse(url).netloc)
        response = self._client.get(url, headers=headers)

        # 4xx other than 429 means the document is genuinely not there; retrying is rude
        # and pointless. 5xx and 429 are worth backing off and trying again.
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableStatus(f"HTTP {response.status_code} from {url}")
        return response

    def _wait_turn(self, host: str) -> None:
        """Hold off until the courtesy interval since this host's last request has elapsed."""
        last = self._last_request_at.get(host)
        if last is not None:
            remaining = self.min_interval - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at[host] = time.monotonic()


def _normalise(url: str) -> str:
    """Upgrade bare http to https and drop fragments, so the same document is not archived
    twice under trivially different URLs."""
    parsed = urlparse(url)
    scheme = "https" if parsed.scheme == "http" else parsed.scheme
    return urlunparse(parsed._replace(scheme=scheme, fragment=""))
