"""Published metrics.

The properties worth pinning are the ones a reader would be misled by if they broke: that
rates use the denominator they claim, that the contested and won cohorts stay separate, and
that a party with three candidates does not appear alongside a party with three hundred as
though the two percentages meant the same thing.
"""

from __future__ import annotations

import polars as pl

from pipeline.transform.normalize import SCHEMA
from pipeline.transform.publish import (
    MIN_CANDIDATES_FOR_RATE,
    election_totals,
    party_election_metrics,
)


def make_candidates(rows: list[dict]) -> pl.DataFrame:
    defaults = {
        "election_slug": "LokSabha2024",
        "election_year": 2024,
        "house": "Lok Sabha",
        "view": "candidates_analyzed",
        "is_winner": False,
        "serial": 1,
        "name": "Someone",
        "constituency": "SOMEWHERE",
        "reservation": None,
        "party": "XYZ",
        "criminal_cases": 0,
        "education": "Graduate",
        "assets_rupees": 1_000_000,
        "liabilities_rupees": 0,
    }
    return pl.DataFrame([defaults | row for row in rows])


def party_row(frame: pl.DataFrame, party: str, cohort: str) -> dict:
    matched = frame.filter((pl.col("party") == party) & (pl.col("cohort") == cohort))
    assert matched.height == 1, f"expected one row for {party}/{cohort}, got {matched.height}"
    return matched.to_dicts()[0]


def test_rate_uses_the_stated_denominator():
    candidates = make_candidates(
        [{"name": f"c{i}", "party": "AAA", "criminal_cases": 1 if i < 4 else 0} for i in range(20)]
    )

    row = party_row(party_election_metrics(candidates), "AAA", "contested")

    assert row["candidates_analysed"] == 20
    assert row["with_declared_cases"] == 4
    assert row["pct_with_declared_cases"] == 20.0


def test_case_count_and_candidate_count_are_different_metrics():
    """One candidate with sixteen cases is not sixteen candidates with cases."""
    candidates = make_candidates(
        [
            {"name": f"c{i}", "party": "AAA", "criminal_cases": 16 if i == 0 else 0}
            for i in range(10)
        ]
    )

    row = party_row(party_election_metrics(candidates), "AAA", "contested")

    assert row["with_declared_cases"] == 1
    assert row["total_declared_cases"] == 16
    assert row["pct_with_declared_cases"] == 10.0


def test_small_parties_get_no_percentage():
    """A single candidate with a case would otherwise publish as '100% criminal' — true,
    and useless next to a party that fielded four hundred."""
    candidates = make_candidates(
        [{"name": f"c{i}", "party": "TINY", "criminal_cases": 1} for i in range(3)]
    )

    row = party_row(party_election_metrics(candidates), "TINY", "contested")

    assert row["candidates_analysed"] == 3
    assert row["with_declared_cases"] == 3
    assert row["pct_with_declared_cases"] is None


def test_the_threshold_is_inclusive():
    candidates = make_candidates(
        [
            {"name": f"c{i}", "party": "EDGE", "criminal_cases": 0}
            for i in range(MIN_CANDIDATES_FOR_RATE)
        ]
    )

    row = party_row(party_election_metrics(candidates), "EDGE", "contested")

    assert row["pct_with_declared_cases"] == 0.0


def test_contested_and_won_are_reported_separately():
    """The gap between the two is the interesting number, so they must never be merged."""
    candidates = make_candidates(
        [{"name": f"c{i}", "party": "AAA", "criminal_cases": 1 if i < 2 else 0} for i in range(20)]
        + [
            {
                "name": f"w{i}",
                "party": "AAA",
                "is_winner": True,
                "view": "winner_analyzed",
                "criminal_cases": 1 if i < 8 else 0,
            }
            for i in range(10)
        ]
    )

    metrics = party_election_metrics(candidates)

    assert party_row(metrics, "AAA", "contested")["pct_with_declared_cases"] == 10.0
    assert party_row(metrics, "AAA", "won")["pct_with_declared_cases"] == 80.0


def test_no_composite_score_is_published():
    """The observatory publishes defined metrics, not a blended index no one can defend."""
    columns = party_election_metrics(make_candidates([{"name": "a"}])).columns

    forbidden = {"score", "index", "rank", "rating", "grade"}
    assert not any(any(word in column.lower() for word in forbidden) for column in columns)


def test_assets_are_summarised_by_median_not_mean():
    """One billionaire should not move a party's headline asset figure."""
    candidates = make_candidates(
        [{"name": f"c{i}", "party": "AAA", "assets_rupees": 1_000_000} for i in range(10)]
        + [{"name": "rich", "party": "AAA", "assets_rupees": 10_000_000_000}]
    )

    row = party_row(party_election_metrics(candidates), "AAA", "contested")

    assert row["median_assets_rupees"] == 1_000_000


def test_elections_are_kept_apart():
    candidates = pl.concat(
        [
            make_candidates([{"name": f"a{i}", "party": "AAA"} for i in range(10)]),
            make_candidates(
                [
                    {
                        "name": f"b{i}",
                        "party": "AAA",
                        "election_year": 2019,
                        "election_slug": "LokSabha2019",
                        "criminal_cases": 1,
                    }
                    for i in range(10)
                ]
            ),
        ]
    )

    metrics = party_election_metrics(candidates)

    by_year = {row["election_year"]: row for row in metrics.to_dicts()}
    assert by_year[2024]["pct_with_declared_cases"] == 0.0
    assert by_year[2019]["pct_with_declared_cases"] == 100.0


def test_election_totals_summarise_without_party():
    candidates = make_candidates(
        [{"name": f"c{i}", "party": f"P{i % 4}", "criminal_cases": i % 2} for i in range(20)]
    )

    totals = election_totals(candidates).to_dicts()

    assert len(totals) == 1
    assert totals[0]["candidates_analysed"] == 20
    assert totals[0]["parties"] == 4
    assert totals[0]["pct_with_declared_cases"] == 50.0


def test_empty_input_produces_an_empty_table_not_a_crash():
    """An election archived but not yet parsed leaves a correctly-shaped, rowless frame."""
    empty = pl.DataFrame(schema=SCHEMA)

    assert party_election_metrics(empty).height == 0
    assert election_totals(empty).height == 0
