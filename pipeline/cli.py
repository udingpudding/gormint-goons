"""Command line entry points for the three pipeline stages.

    uv run python -m pipeline archive    # fetch listings into data/raw/
    uv run python -m pipeline parse      # archived pages -> data/normalized/
    uv run python -m pipeline publish    # normalized rows -> data/public/

The stages are separate commands rather than one pipeline run because they fail for
unrelated reasons and recover differently. Archiving is slow, network-bound and polite;
parsing is fast, local, and re-run every time a format surprise is found. Collapsing them
would mean a parser fix costs another crawl.
"""

from __future__ import annotations

import argparse
import logging
import sys

import polars as pl

from pipeline import paths
from pipeline.archive.crawl import crawl_constituencies, crawl_elections
from pipeline.archive.fetcher import Fetcher
from pipeline.archive.manifest import Manifest
from pipeline.archive.sources import myneta
from pipeline.transform import normalize as normalize_stage
from pipeline.transform import publish as publish_stage

log = logging.getLogger("pipeline")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
        stream=sys.stderr,
    )


def cmd_archive(args: argparse.Namespace) -> int:
    paths.ensure_dirs()
    elections = myneta.LOK_SABHA
    if args.election:
        elections = tuple(myneta.ELECTIONS_BY_SLUG[s] for s in args.election)

    views = (myneta.View.WINNERS,) if args.winners_only else myneta.DEFAULT_VIEWS

    manifest = Manifest.load()
    before = len(manifest)

    with Fetcher(manifest, min_interval=args.delay) as fetcher:
        try:
            if args.constituencies:
                for election in elections:
                    for summary in crawl_constituencies(fetcher, election):
                        log.info("%s", summary)
                    # Persist per election rather than only at the end: an interrupted
                    # backfill of several thousand pages should not lose its progress.
                    fetcher.save()
            else:
                for summary in crawl_elections(fetcher, elections, views):
                    log.info("%s", summary)
        finally:
            # Persist whatever was archived even if the crawl is interrupted; a partial
            # backfill that remembers its progress is resumable, one that forgets is not.
            fetcher.save()

    log.info("Manifest: %s entries (%+d)", len(manifest), len(manifest) - before)
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    paths.ensure_dirs()
    candidates, seats = normalize_stage.normalize_myneta()
    log.info("Wrote %s candidate rows and %s seats to %s", candidates, seats, paths.NORMALIZED)
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    paths.ensure_dirs()
    outputs = publish_stage.publish_all()
    for name, rows in outputs.items():
        log.info("Published %s (%s rows)", name, rows)

    # Winners counted from constituency pages against winners enumerated by the summary
    # listing — different pages, different parser. This is the project's only external
    # check on the parsers now that MoSPI is unreachable, so it is always reported.
    everything = pl.read_parquet(publish_stage.CANDIDATES_PARQUET)
    for row in publish_stage.reconcile(everything).to_dicts():
        seats, listing = row["winners_from_seats"], row["winners_from_listing"]
        if seats is None or listing is None:
            continue
        verdict = "ok" if seats >= listing else "SEATS SHORT OF LISTING"
        log.info(
            "Reconcile %s: %s winners from seats, %s from listing (%+d) — %s",
            row["election_year"],
            seats,
            listing,
            row["difference"],
            verdict,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    archive = sub.add_parser("archive", help="fetch source documents into data/raw/")
    archive.add_argument(
        "--election",
        action="append",
        choices=sorted(myneta.ELECTIONS_BY_SLUG),
        help="restrict to one election; repeatable. Defaults to all.",
    )
    archive.add_argument(
        "--winners-only",
        action="store_true",
        help="archive only the winners listing, which is ~30 pages per election rather than ~470",
    )
    archive.add_argument(
        "--constituencies",
        action="store_true",
        help="archive every seat's candidate list (~545 pages per election). Slower than the "
        "summary listings but covers all candidates and carries state, age and candidate ids",
    )
    archive.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="seconds between requests to one host (default: 1.5)",
    )
    archive.set_defaults(func=cmd_archive)

    parse = sub.add_parser("parse", help="archived pages -> normalized rows")
    parse.set_defaults(func=cmd_parse)

    publish = sub.add_parser("publish", help="normalized rows -> published Parquet")
    publish.set_defaults(func=cmd_publish)

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
