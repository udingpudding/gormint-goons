"""Stage three: normalized rows into the tables the site reads.

Every metric here is a count or a rate over a stated denominator. There is no composite
index, no weighting and no ranking of parties, because any such number would mostly encode
the weights chosen rather than anything about the parties — and defending the weights would
become the argument, in place of the data.

Two denominators are published side by side wherever a rate appears: the share among all
candidates a party fielded, and the share among the ones who won. They routinely differ, and
the gap is a finding rather than a rounding detail — a party can field few candidates with
declared cases while electing many, or the reverse.

The population is candidates whose affidavits ADR was able to analyse, which is not
identical to everyone who stood. ``candidates_analysed`` is named for that reason: the column
is the honest denominator, and the site should print it next to any percentage derived
from it.
"""

from __future__ import annotations

import json
import logging

import polars as pl

from pipeline import paths
from pipeline.transform.normalize import CANDIDATES_PARQUET

log = logging.getLogger(__name__)

#: The site reads these directly so the headline paints without loading a query engine.
#: Small enough to inline; everything larger stays in Parquet for the explore view.
SITE_DATA = paths.PUBLIC / "site"

#: Minimum candidates before a rate is published. Percentages over three candidates are
#: noise that reads as signal, and small parties would otherwise dominate any sort.
MIN_CANDIDATES_FOR_RATE = 10


def publish_all() -> dict[str, int]:
    """Write every published table. Returns row counts by file name."""
    if not CANDIDATES_PARQUET.exists():
        raise FileNotFoundError(
            f"{CANDIDATES_PARQUET} not found — run `python -m pipeline parse` first"
        )

    candidates = pl.read_parquet(CANDIDATES_PARQUET)
    paths.PUBLIC.mkdir(parents=True, exist_ok=True)

    totals = election_totals(candidates)
    parties = party_election_metrics(candidates)

    outputs: dict[str, int] = {}
    for name, frame in (
        ("candidates.parquet", _candidates_table(candidates)),
        ("party_election.parquet", parties),
        ("election_totals.parquet", totals),
    ):
        frame.write_parquet(paths.PUBLIC / name)
        outputs[name] = frame.height

    outputs |= _write_site_json(totals, parties)
    return outputs


def _write_site_json(totals: pl.DataFrame, parties: pl.DataFrame) -> dict[str, int]:
    """Emit the small JSON the headline page reads.

    Kept separate from the Parquet because the two serve different jobs: the page needs a few
    hundred bytes on first paint, while the explore view needs columnar data it can query.
    Loading a WASM query engine to render five numbers would be the wrong trade.
    """
    SITE_DATA.mkdir(parents=True, exist_ok=True)

    elected = totals.filter(pl.col("cohort") == "won").sort("election_year")
    timeline = [
        {
            "year": row["election_year"],
            "pct": row["pct_with_declared_cases"],
            "withCases": row["with_declared_cases"],
            "analysed": row["candidates_analysed"],
            "medianAssets": int(row["median_assets_rupees"] or 0),
            "totalCases": row["total_declared_cases"],
        }
        for row in elected.to_dicts()
    ]

    latest_year = max((row["year"] for row in timeline), default=None)
    by_party = (
        parties.filter(
            (pl.col("cohort") == "won")
            & (pl.col("election_year") == latest_year)
            & (pl.col("pct_with_declared_cases").is_not_null())
        )
        .sort("candidates_analysed", descending=True)
        .to_dicts()
    )
    party_rows = [
        {
            "party": row["party"],
            "pct": row["pct_with_declared_cases"],
            "withCases": row["with_declared_cases"],
            "analysed": row["candidates_analysed"],
            "medianAssets": int(row["median_assets_rupees"] or 0),
        }
        for row in by_party
    ]

    # Deliberately carries no build timestamp: it would change on every run and fill the
    # git history with diffs that say nothing. The manifest already records when each
    # source document was retrieved.
    payload = {
        "latestYear": latest_year,
        "minPartySize": MIN_CANDIDATES_FOR_RATE,
        "timeline": timeline,
        "parties": party_rows,
    }
    (SITE_DATA / "headline.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {"site/headline.json": len(timeline) + len(party_rows)}


def _candidates_table(candidates: pl.DataFrame) -> pl.DataFrame:
    """The candidate-level rows, for the explore view to query directly."""
    return candidates.sort(["election_year", "party", "constituency", "name"])


def party_election_metrics(candidates: pl.DataFrame) -> pl.DataFrame:
    """Per party, per election, for each of the two cohorts.

    ``cohort`` is ``contested`` for everyone a party fielded and ``won`` for the subset who
    were elected, so both denominators sit in one table and neither can be quoted without
    the other being one filter away.
    """
    frames = [
        _cohort_metrics(candidates.filter(~pl.col("is_winner")), "contested"),
        _cohort_metrics(candidates.filter(pl.col("is_winner")), "won"),
    ]
    combined = (
        pl.concat([f for f in frames if f.height], how="vertical")
        if any(f.height for f in frames)
        else _empty_metrics()
    )
    return combined.sort(["election_year", "cohort", "party"])


def _cohort_metrics(rows: pl.DataFrame, cohort: str) -> pl.DataFrame:
    if rows.is_empty():
        return _empty_metrics()

    grouped = rows.group_by(["election_year", "election_slug", "house", "party"]).agg(
        pl.len().alias("candidates_analysed"),
        (pl.col("criminal_cases") > 0).sum().alias("with_declared_cases"),
        pl.col("criminal_cases").sum().alias("total_declared_cases"),
        pl.col("assets_rupees").median().alias("median_assets_rupees"),
        pl.col("assets_rupees").sum().alias("total_assets_rupees"),
        pl.col("liabilities_rupees").median().alias("median_liabilities_rupees"),
    )

    return grouped.with_columns(
        pl.lit(cohort).alias("cohort"),
        # Withheld rather than rounded for tiny parties: a single candidate with a case
        # would otherwise publish as "100% criminal", which is true and useless.
        pl.when(pl.col("candidates_analysed") >= MIN_CANDIDATES_FOR_RATE)
        .then((pl.col("with_declared_cases") / pl.col("candidates_analysed") * 100).round(1))
        .otherwise(None)
        .alias("pct_with_declared_cases"),
    ).select(
        "election_year",
        "election_slug",
        "house",
        "cohort",
        "party",
        "candidates_analysed",
        "with_declared_cases",
        "pct_with_declared_cases",
        "total_declared_cases",
        "median_assets_rupees",
        "total_assets_rupees",
        "median_liabilities_rupees",
    )


def _empty_metrics() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "election_year": pl.Int32,
            "election_slug": pl.Utf8,
            "house": pl.Utf8,
            "cohort": pl.Utf8,
            "party": pl.Utf8,
            "candidates_analysed": pl.UInt32,
            "with_declared_cases": pl.UInt32,
            "pct_with_declared_cases": pl.Float64,
            "total_declared_cases": pl.Int32,
            "median_assets_rupees": pl.Float64,
            "total_assets_rupees": pl.Int64,
            "median_liabilities_rupees": pl.Float64,
        }
    )


def election_totals(candidates: pl.DataFrame) -> pl.DataFrame:
    """One row per election and cohort — the headline figures, party held aside."""
    return (
        candidates.with_columns(
            pl.when(pl.col("is_winner"))
            .then(pl.lit("won"))
            .otherwise(pl.lit("contested"))
            .alias("cohort")
        )
        .group_by(["election_year", "election_slug", "house", "cohort"])
        .agg(
            pl.len().alias("candidates_analysed"),
            (pl.col("criminal_cases") > 0).sum().alias("with_declared_cases"),
            pl.col("criminal_cases").sum().alias("total_declared_cases"),
            pl.col("assets_rupees").median().alias("median_assets_rupees"),
            pl.col("party").n_unique().alias("parties"),
        )
        .with_columns(
            (pl.col("with_declared_cases") / pl.col("candidates_analysed") * 100)
            .round(1)
            .alias("pct_with_declared_cases")
        )
        .sort(["election_year", "cohort"])
    )
