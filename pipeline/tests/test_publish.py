"""Published metrics.

The properties worth pinning are the ones a reader would be misled by if they broke: that
rates use the denominator they claim, that a winner is counted among the candidates their
party fielded as well as among those it elected, and that a party with three candidates does
not appear beside a party with three hundred as though the two percentages meant the same.
"""

from __future__ import annotations

import polars as pl

from pipeline.transform.normalize import SCHEMA
from pipeline.transform.publish import (
    MIN_CANDIDATES_FOR_RATE,
    election_totals,
    party_election_metrics,
    reconcile,
    state_election_metrics,
)


def make_candidates(rows: list[dict]) -> pl.DataFrame:
    defaults = {
        "election_slug": "LokSabha2024",
        "election_year": 2024,
        "house": "Lok Sabha",
        "view": "constituency",
        "is_winner": False,
        "serial": 1,
        "name": "Someone",
        "state": "MAHARASHTRA",
        "constituency": "SOMEWHERE",
        "constituency_id": 1,
        "candidate_id": 1,
        "reservation": None,
        "party": "XYZ",
        "criminal_cases": 0,
        "education": "Graduate",
        "age": 50,
        "assets_rupees": 1_000_000,
        "liabilities_rupees": 0,
        "is_bye_election": False,
    }
    return pl.DataFrame([defaults | row for row in rows], schema=SCHEMA)


def one(frame: pl.DataFrame, **filters) -> dict:
    for column, value in filters.items():
        frame = frame.filter(pl.col(column) == value)
    assert frame.height == 1, f"expected one row for {filters}, got {frame.height}"
    return frame.to_dicts()[0]


def test_rate_uses_the_stated_denominator():
    candidates = make_candidates(
        [{"name": f"c{i}", "party": "AAA", "criminal_cases": 1 if i < 4 else 0} for i in range(20)]
    )

    row = one(party_election_metrics(candidates), party="AAA", cohort="contested")

    assert row["candidates"] == 20
    assert row["with_declared_cases"] == 4
    assert row["pct_with_declared_cases"] == 20.0


def test_winners_are_counted_among_those_the_party_fielded():
    """A winner is still someone the party put up. Treating 'contested' as everyone who
    lost would answer a question nobody asked."""
    candidates = make_candidates(
        [{"name": f"c{i}", "party": "AAA", "is_winner": i == 0} for i in range(10)]
    )

    metrics = party_election_metrics(candidates)

    assert one(metrics, party="AAA", cohort="contested")["candidates"] == 10
    assert one(metrics, party="AAA", cohort="won")["candidates"] == 1


def test_case_count_and_candidate_count_are_different_metrics():
    """One candidate with sixteen cases is not sixteen candidates with cases."""
    candidates = make_candidates(
        [
            {"name": f"c{i}", "party": "AAA", "criminal_cases": 16 if i == 0 else 0}
            for i in range(10)
        ]
    )

    row = one(party_election_metrics(candidates), party="AAA", cohort="contested")

    assert row["with_declared_cases"] == 1
    assert row["total_declared_cases"] == 16
    assert row["pct_with_declared_cases"] == 10.0


def test_small_groups_get_no_percentage():
    candidates = make_candidates(
        [{"name": f"c{i}", "party": "TINY", "criminal_cases": 1} for i in range(3)]
    )

    row = one(party_election_metrics(candidates), party="TINY", cohort="contested")

    assert row["candidates"] == 3
    assert row["with_declared_cases"] == 3
    assert row["pct_with_declared_cases"] is None


def test_the_threshold_is_inclusive():
    candidates = make_candidates(
        [{"name": f"c{i}", "party": "EDGE"} for i in range(MIN_CANDIDATES_FOR_RATE)]
    )

    assert (
        one(party_election_metrics(candidates), party="EDGE", cohort="contested")[
            "pct_with_declared_cases"
        ]
        == 0.0
    )


def test_the_two_cohorts_can_differ_sharply():
    """The gap between who a party fielded and who it elected is the interesting number."""
    candidates = make_candidates(
        [
            {
                "name": f"c{i}",
                "party": "AAA",
                "is_winner": i < 10,
                "criminal_cases": 1 if i < 8 else 0,
            }
            for i in range(40)
        ]
    )

    metrics = party_election_metrics(candidates)

    assert one(metrics, party="AAA", cohort="contested")["pct_with_declared_cases"] == 20.0
    assert one(metrics, party="AAA", cohort="won")["pct_with_declared_cases"] == 80.0


def test_states_with_the_same_constituency_name_stay_apart():
    """Aurangabad is a seat in both Bihar and Maharashtra. Grouping by name alone merges
    two different members — which is exactly why the state is collected."""
    candidates = make_candidates(
        [
            {
                "name": f"bihar{i}",
                "state": "BIHAR",
                "constituency": "AURANGABAD",
                "constituency_id": 100,
                "criminal_cases": 1,
            }
            for i in range(10)
        ]
        + [
            {
                "name": f"mh{i}",
                "state": "MAHARASHTRA",
                "constituency": "AURANGABAD",
                "constituency_id": 200,
                "criminal_cases": 0,
            }
            for i in range(10)
        ]
    )

    metrics = state_election_metrics(candidates)

    assert one(metrics, state="BIHAR", cohort="contested")["pct_with_declared_cases"] == 100.0
    assert one(metrics, state="MAHARASHTRA", cohort="contested")["pct_with_declared_cases"] == 0.0
    assert one(metrics, state="BIHAR", cohort="contested")["seats"] == 1


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

    row = one(party_election_metrics(candidates), party="AAA", cohort="contested")

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

    by_year = {
        row["election_year"]: row
        for row in party_election_metrics(candidates)
        .filter(pl.col("cohort") == "contested")
        .to_dicts()
    }

    assert by_year[2024]["pct_with_declared_cases"] == 0.0
    assert by_year[2019]["pct_with_declared_cases"] == 100.0


def test_election_totals_count_seats_and_parties():
    candidates = make_candidates(
        [
            {
                "name": f"c{i}",
                "party": f"P{i % 4}",
                "constituency_id": i % 5,
                "criminal_cases": i % 2,
            }
            for i in range(20)
        ]
    )

    row = one(election_totals(candidates), cohort="contested")

    assert row["candidates"] == 20
    assert row["parties"] == 4
    assert row["seats"] == 5
    assert row["pct_with_declared_cases"] == 50.0


def test_reconciliation_compares_two_independent_counts():
    """Winners marked on constituency pages against winners enumerated by the summary
    listing — different pages, different parser, so agreement is real evidence."""
    everything = pl.concat(
        [
            make_candidates([{"name": f"w{i}", "is_winner": i < 3} for i in range(10)]),
            make_candidates(
                [{"name": f"w{i}", "view": "winner_analyzed", "is_winner": True} for i in range(3)]
            ),
        ]
    )

    row = reconcile(everything).to_dicts()[0]

    assert row["winners_from_seats"] == 3
    assert row["winners_from_listing"] == 3
    assert row["difference"] == 0


def test_empty_input_produces_empty_tables_not_a_crash():
    empty = pl.DataFrame(schema=SCHEMA)

    assert party_election_metrics(empty).height == 0
    assert state_election_metrics(empty).height == 0
    assert election_totals(empty).height == 0
