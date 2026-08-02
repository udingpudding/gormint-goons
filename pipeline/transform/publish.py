"""Stage three: normalized rows into the tables the site reads.

Every metric here is a count or a rate over a stated denominator. There is no composite
index, no weighting and no ranking of parties, because any such number would mostly encode
the weights chosen rather than anything about the parties — and defending the weights would
become the argument, in place of the data.

Two cohorts are published side by side wherever a rate appears: every candidate a party
fielded, and the subset who won. They routinely differ, and the gap is a finding rather than
a rounding detail — a party can field many candidates with declared cases while electing few,
or the reverse.

The candidate population comes from the constituency pages, which list everyone who stood.
The paginated summary listings cover only candidates whose affidavits ADR analysed, and are
kept solely to check this stage's output against — see :func:`reconcile`.
"""

from __future__ import annotations

import json
import logging

import polars as pl

from pipeline import paths
from pipeline.transform.normalize import CANDIDATES_PARQUET

log = logging.getLogger(__name__)

#: The site reads these directly so the headline paints without loading a query engine.
SITE_DATA = paths.PUBLIC / "site"

#: Minimum candidates before a rate is published. A percentage over three candidates is
#: noise that reads as signal, and small parties would otherwise dominate any sort.
MIN_CANDIDATES_FOR_RATE = 10

#: Rows parsed from constituency pages — the canonical candidate population.
CANONICAL_VIEW = "constituency"


def publish_all() -> dict[str, int]:
    """Write every published table. Returns row counts by file name."""
    if not CANDIDATES_PARQUET.exists():
        raise FileNotFoundError(
            f"{CANDIDATES_PARQUET} not found — run `python -m pipeline parse` first"
        )

    everything = pl.read_parquet(CANDIDATES_PARQUET)

    # By-elections are filed by MyNeta under the general election that preceded them. Left
    # in, they push the count of members elected past the 543 seats a reader expects and
    # blend two different events into one figure. They stay in the archive and in the
    # normalized table; they are simply not part of a general-election statistic.
    candidates = everything.filter((pl.col("view") == CANONICAL_VIEW) & ~pl.col("is_bye_election"))
    if candidates.is_empty():
        raise ValueError(
            "No constituency-page rows found. Run "
            "`python -m pipeline archive --constituencies` first — the summary listings "
            "alone cannot support state breakdowns or a candidates-fielded denominator."
        )

    paths.PUBLIC.mkdir(parents=True, exist_ok=True)

    totals = election_totals(candidates)
    parties = party_election_metrics(candidates)
    states = state_election_metrics(candidates)

    outputs: dict[str, int] = {}
    for name, frame in (
        ("candidates.parquet", candidates.sort(["election_year", "state", "constituency", "name"])),
        ("party_election.parquet", parties),
        ("state_election.parquet", states),
        ("election_totals.parquet", totals),
    ):
        frame.write_parquet(paths.PUBLIC / name)
        outputs[name] = frame.height

    outputs |= _write_site_json(candidates, totals, parties, states)
    return outputs


# -- metrics ---------------------------------------------------------------------------


def _cohorts(candidates: pl.DataFrame) -> pl.DataFrame:
    """Label each row with the cohorts it belongs to.

    A winner is also someone the party fielded, so winners appear in both cohorts. Treating
    "contested" as everyone-who-did-not-win would answer a question nobody asked.
    """
    contested = candidates.with_columns(pl.lit("contested").alias("cohort"))
    won = candidates.filter(pl.col("is_winner")).with_columns(pl.lit("won").alias("cohort"))
    return pl.concat([contested, won], how="vertical")


_AGGREGATIONS = (
    pl.len().alias("candidates"),
    (pl.col("criminal_cases") > 0).sum().alias("with_declared_cases"),
    pl.col("criminal_cases").sum().alias("total_declared_cases"),
    pl.col("assets_rupees").median().alias("median_assets_rupees"),
    pl.col("liabilities_rupees").median().alias("median_liabilities_rupees"),
    pl.col("age").median().alias("median_age"),
)


def _with_rate(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(
        # Withheld rather than rounded for tiny groups: a single candidate with a case
        # would otherwise publish as "100% criminal", which is true and useless.
        pl.when(pl.col("candidates") >= MIN_CANDIDATES_FOR_RATE)
        .then((pl.col("with_declared_cases") / pl.col("candidates") * 100).round(1))
        .otherwise(None)
        .alias("pct_with_declared_cases")
    )


def party_election_metrics(candidates: pl.DataFrame) -> pl.DataFrame:
    """Per party, per election, for each cohort."""
    return _with_rate(
        _cohorts(candidates)
        .group_by(["election_year", "election_slug", "house", "cohort", "party"])
        .agg(*_AGGREGATIONS)
    ).sort(["election_year", "cohort", "party"])


def state_election_metrics(candidates: pl.DataFrame) -> pl.DataFrame:
    """Per state, per election, for each cohort.

    Possible only because the constituency pages carry the state. Grouping the summary
    listings by constituency name would have merged Aurangabad in Bihar with Aurangabad in
    Maharashtra, and two other pairs like it.
    """
    return _with_rate(
        _cohorts(candidates)
        .group_by(["election_year", "election_slug", "house", "cohort", "state"])
        .agg(*_AGGREGATIONS, pl.col("constituency_id").n_unique().alias("seats"))
    ).sort(["election_year", "cohort", "state"])


def election_totals(candidates: pl.DataFrame) -> pl.DataFrame:
    """One row per election and cohort — the headline figures, party and state held aside."""
    return _with_rate(
        _cohorts(candidates)
        .group_by(["election_year", "election_slug", "house", "cohort"])
        .agg(
            *_AGGREGATIONS,
            pl.col("party").n_unique().alias("parties"),
            pl.col("state").n_unique().alias("states"),
            pl.col("constituency_id").n_unique().alias("seats"),
        )
    ).sort(["election_year", "cohort"])


# -- reconciliation --------------------------------------------------------------------


def reconcile(everything: pl.DataFrame) -> pl.DataFrame:
    """Compare winners counted two independent ways.

    The constituency pages mark a winner in each seat; the ``winner_analyzed`` summary
    listing enumerates winners directly. The two are parsed by different code from different
    pages, so agreement between them is real evidence the parsers are right — and this
    project has no other external oracle now that MoSPI is unreachable.

    The counts are not expected to match exactly: the summary listing covers only candidates
    whose affidavits ADR analysed, while the constituency pages list everyone who stood. The
    constituency count should be the same or slightly higher, never lower.
    """
    from_seats = (
        everything.filter(
            (pl.col("view") == CANONICAL_VIEW) & pl.col("is_winner") & ~pl.col("is_bye_election")
        )
        .group_by("election_year")
        .agg(pl.len().alias("winners_from_seats"))
    )
    from_listing = (
        everything.filter(pl.col("view") == "winner_analyzed")
        .group_by("election_year")
        .agg(pl.len().alias("winners_from_listing"))
    )
    return (
        from_seats.join(from_listing, on="election_year", how="full", coalesce=True)
        .with_columns(
            (pl.col("winners_from_seats") - pl.col("winners_from_listing")).alias("difference")
        )
        .sort("election_year")
    )


# -- distributions ---------------------------------------------------------------------

#: Declared wealth spans seven orders of magnitude, so bands are logarithmic. Linear buckets
#: would put ninety-odd percent of candidates in the first one and say nothing.
ASSET_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("Under ₹1 lakh", 0, 100_000),
    ("₹1–10 lakh", 100_000, 1_000_000),
    ("₹10 lakh–1 crore", 1_000_000, 10_000_000),
    ("₹1–10 crore", 10_000_000, 100_000_000),
    ("₹10–100 crore", 100_000_000, 1_000_000_000),
    ("Over ₹100 crore", 1_000_000_000, None),
)

AGE_BANDS: tuple[tuple[str, int, int | None], ...] = (
    ("25–34", 25, 35),
    ("35–44", 35, 45),
    ("45–54", 45, 55),
    ("55–64", 55, 65),
    ("65–74", 65, 75),
    ("75+", 75, None),
)


def _band(candidates: pl.DataFrame, column: str, bands, year: int) -> list[dict]:
    """Count candidates and winners falling in each band, for one election.

    Both cohorts share the band definition so the two distributions can be read against each
    other — the whole point is the shape of the winners' distribution against everyone's.
    """
    rows = candidates.filter((pl.col("election_year") == year) & pl.col(column).is_not_null())
    contested_total = rows.height
    won_total = rows.filter(pl.col("is_winner")).height

    out = []
    for label, low, high in bands:
        in_band = pl.col(column) >= low
        if high is not None:
            in_band = in_band & (pl.col(column) < high)
        contested = rows.filter(in_band).height
        won = rows.filter(in_band & pl.col("is_winner")).height
        out.append(
            {
                "label": label,
                "contested": contested,
                "won": won,
                "contestedShare": round(contested / contested_total * 100, 1)
                if contested_total
                else 0.0,
                "wonShare": round(won / won_total * 100, 1) if won_total else 0.0,
            }
        )
    return out


# -- the honours list --------------------------------------------------------------------

#: Each award is the maximum (or minimum) of one published metric — never a blend of several.
#: That keeps the joke arguable only on the facts: there are no weights to dispute, just a
#: party sitting at the top of a column it would rather not top.
AWARDS: tuple[dict, ...] = (
    {
        "id": "cases",
        "title": "The Rap Sheet",
        "citation": "For the largest share of members arriving with a pending criminal case.",
        "metric": "pct_with_declared_cases",
        "unit": "percent",
        "descending": True,
    },
    {
        "id": "money",
        "title": "The Moneybags",
        "citation": "For the highest median declared fortune among members elected.",
        "metric": "median_assets_rupees",
        "unit": "rupees",
        "descending": True,
    },
    {
        "id": "age",
        "title": "The Long Service Medal",
        "citation": "For the highest median age, in a chamber that keeps getting older.",
        "metric": "median_age",
        "unit": "years",
        "descending": True,
    },
    {
        "id": "clean",
        "title": "The Unblemished",
        "citation": (
            "For the smallest share of members with a pending case. Somebody has to win it."
        ),
        "metric": "pct_with_declared_cases",
        "unit": "percent",
        "descending": False,
    },
)


def honours(parties: pl.DataFrame, year: int) -> list[dict]:
    """Pick the party topping each column, for the most recent election."""
    eligible = parties.filter(
        (pl.col("election_year") == year)
        & (pl.col("cohort") == "won")
        & (pl.col("candidates") >= MIN_CANDIDATES_FOR_RATE)
    )
    if eligible.is_empty():
        return []

    out = []
    for award in AWARDS:
        ranked = eligible.filter(pl.col(award["metric"]).is_not_null()).sort(
            award["metric"], descending=award["descending"]
        )
        if ranked.is_empty():
            continue
        row = ranked.to_dicts()[0]
        runners = [
            {
                "party": r["party"],
                "value": _award_value(r[award["metric"]], award["unit"]),
                "analysed": r["candidates"],
            }
            for r in ranked.to_dicts()[1:4]
        ]
        out.append(
            {
                "id": award["id"],
                "title": award["title"],
                "citation": award["citation"],
                "unit": award["unit"],
                "party": row["party"],
                "value": _award_value(row[award["metric"]], award["unit"]),
                "withCases": row["with_declared_cases"],
                "analysed": row["candidates"],
                "runnersUp": runners,
            }
        )
    return out


def _award_value(value, unit: str):
    if value is None:
        return None
    if unit == "rupees":
        return int(value)
    if unit == "years":
        return round(value)
    return round(float(value), 1)


# -- site payload ----------------------------------------------------------------------


def _int_or_none(value) -> int | None:
    """Medians come back as floats. Half a year of age is not a meaningful distinction, and
    a null must survive as null rather than becoming zero."""
    return None if value is None else round(value)


def _write_site_json(
    candidates: pl.DataFrame,
    totals: pl.DataFrame,
    parties: pl.DataFrame,
    states: pl.DataFrame,
) -> dict[str, int]:
    """Emit the small JSON the headline page reads.

    Kept separate from the Parquet because the two serve different jobs: the page needs a few
    kilobytes on first paint, while the explore view needs columnar data it can query.
    Loading a WASM query engine to render five numbers would be the wrong trade.
    """
    SITE_DATA.mkdir(parents=True, exist_ok=True)

    elected = totals.filter(pl.col("cohort") == "won").sort("election_year")
    fielded = {
        r["election_year"]: r for r in totals.filter(pl.col("cohort") == "contested").to_dicts()
    }

    timeline = [
        {
            "year": row["election_year"],
            "pct": row["pct_with_declared_cases"],
            "withCases": row["with_declared_cases"],
            "analysed": row["candidates"],
            "medianAssets": int(row["median_assets_rupees"] or 0),
            "medianAge": _int_or_none(row["median_age"]),
            "contestedPct": fielded.get(row["election_year"], {}).get("pct_with_declared_cases"),
            "contested": fielded.get(row["election_year"], {}).get("candidates"),
        }
        for row in elected.to_dicts()
    ]
    latest_year = max((row["year"] for row in timeline), default=None)

    def rows_for(frame: pl.DataFrame, dimension: str, limit: int | None = None) -> list[dict]:
        selected = (
            frame.filter(
                (pl.col("cohort") == "won")
                & (pl.col("election_year") == latest_year)
                & pl.col("pct_with_declared_cases").is_not_null()
            )
            .sort("candidates", descending=True)
            .to_dicts()
        )
        if limit:
            selected = selected[:limit]
        return [
            {
                dimension: row[dimension],
                "pct": row["pct_with_declared_cases"],
                "withCases": row["with_declared_cases"],
                "analysed": row["candidates"],
                "medianAssets": int(row["median_assets_rupees"] or 0),
                "medianAge": _int_or_none(row["median_age"]),
            }
            for row in selected
        ]

    # Deliberately carries no build timestamp: it would change on every run and fill the git
    # history with diffs that say nothing. The manifest records when each page was retrieved.
    def by_metric(metric: str) -> list[dict]:
        """Parties ranked by one column, for the money and age sections."""
        return sorted(
            rows_for(parties, "party"),
            key=lambda r: (r.get(metric) is None, -(r.get(metric) or 0)),
        )

    payload = {
        "latestYear": latest_year,
        "minPartySize": MIN_CANDIDATES_FOR_RATE,
        "timeline": timeline,
        "parties": rows_for(parties, "party"),
        "states": rows_for(states, "state"),
        "byMoney": by_metric("medianAssets"),
        "byAge": by_metric("medianAge"),
        "assetBands": _band(candidates, "assets_rupees", ASSET_BANDS, latest_year),
        "ageBands": _band(candidates, "age", AGE_BANDS, latest_year),
        "honours": honours(parties, latest_year),
    }
    (SITE_DATA / "headline.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {"site/headline.json": len(timeline) + len(payload["parties"]) + len(payload["states"])}
