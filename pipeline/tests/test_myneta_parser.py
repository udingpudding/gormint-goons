"""Parsing MyNeta listing pages.

Two real archived pages are checked in as fixtures — the oldest election covered (2004) and
the newest (2024). They are the guard against the failure this project is most exposed to:
a layout change upstream that leaves the parser returning plausible but wrong numbers rather
than failing outright.

The unit cases below focus on the money column, where a quiet error would be least visible
and most damaging. ``Rs 56,81,54,912~ 56 Crore+`` contains two numbers, and reading the wrong
one understates a declaration by three orders of magnitude.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.parsers.myneta import (
    Candidate,
    parse_age,
    parse_count,
    parse_listing,
    parse_money,
    split_constituency,
)

FIXTURES = Path(__file__).parent / "fixtures" / "myneta"


# -- money ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Rs\xa013,58,312~ 13\xa0Lacs+", 1_358_312),
        ("Rs\xa056,81,54,912~ 56\xa0Crore+", 56_81_54_912),
        ("Rs\xa04,79,83,303~ 4\xa0Crore+", 4_79_83_303),
        ("Rs\xa042,000~ 42\xa0Thou+", 42_000),
        ("Rs\xa00~", 0),
        ("Rs 1,00,000", 100_000),
    ],
)
def test_reads_the_exact_figure_not_the_rounded_gloss(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("text", ["", "-", "Nil", "N/A", "Not Available", "  "])
def test_undeclared_amounts_are_none_not_zero(text):
    """A candidate who declared nothing and one whose affidavit could not be read are
    different facts; collapsing them to zero understates the second group."""
    assert parse_money(text) is None


def test_zero_is_distinguished_from_missing():
    assert parse_money("Rs\xa00~") == 0
    assert parse_money("Nil") is None


# -- counts and constituencies --------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [("0", 0), ("16", 16), (" 3 ", 3), ("Nil", None)])
def test_case_counts(text, expected):
    assert parse_count(text) == expected


@pytest.mark.parametrize(("text", "expected"), [("33", 33), ("25", 25), ("88", 88)])
def test_real_ages_are_kept(text, expected):
    assert parse_age(text) == expected


@pytest.mark.parametrize("text", ["0", "4", "21", "24", "", "Nil"])
def test_ages_below_the_constitutional_minimum_are_not_declared(text):
    """MyNeta writes an undeclared age as 0. Read literally, 165 candidates would be aged
    zero and every median age would be dragged down by them. Nobody under 25 can sit in the
    Lok Sabha, so anything below that is a data error rather than a young candidate."""
    assert parse_age(text) is None


def test_zero_age_is_not_confused_with_zero_cases():
    """A candidate can genuinely have zero criminal cases; none can genuinely be zero."""
    assert parse_count("0") == 0
    assert parse_age("0") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("JALANDHAR (SC)", ("JALANDHAR", "SC")),
        ("VILUPPURAM (SC)", ("VILUPPURAM", "SC")),
        ("NABARANGPUR (ST)", ("NABARANGPUR", "ST")),
        ("MADHUBANI", ("MADHUBANI", None)),
        ("HINGOLI ", ("HINGOLI", None)),
    ],
)
def test_reservation_is_split_off_the_seat_name(text, expected):
    assert split_constituency(text) == expected


# -- whole pages ----------------------------------------------------------------------


def parse_fixture(slug: str, year: int) -> list[Candidate]:
    html = (FIXTURES / f"{slug}_winner_analyzed_p001.html").read_text(encoding="utf-8")
    return parse_listing(
        html,
        election_slug=slug,
        election_year=year,
        house="Lok Sabha",
        view="winner_analyzed",
    )


@pytest.mark.parametrize(("slug", "year"), [("loksabha2004", 2004), ("LokSabha2024", 2024)])
def test_a_full_page_yields_one_row_per_candidate(slug, year):
    rows = parse_fixture(slug, year)

    assert len(rows) == 18
    assert all(row.election_year == year for row in rows)
    assert all(row.is_winner for row in rows)


@pytest.mark.parametrize(("slug", "year"), [("loksabha2004", 2004), ("LokSabha2024", 2024)])
def test_every_row_carries_the_fields_the_metrics_depend_on(slug, year):
    """Twenty years apart, the same columns must come through — this is what makes the two
    elections comparable at all."""
    for row in parse_fixture(slug, year):
        assert row.name
        assert row.party
        assert row.constituency
        assert row.criminal_cases is not None
        assert row.assets_rupees is not None


def test_2004_page_parses_to_known_values():
    """Pinned against the archived 2004 page so an upstream layout change is caught."""
    first = parse_fixture("loksabha2004", 2004)[0]

    assert first.name.startswith("A. B. A. Ghani Khan")
    assert first.party == "INC"
    assert first.constituency == "Malda"
    assert first.criminal_cases == 1
    assert first.assets_rupees == 4_115_704
    assert first.liabilities_rupees == 4_187_744


def test_2024_page_parses_to_known_values():
    rows = parse_fixture("LokSabha2024", 2024)
    by_name = {row.name: row for row in rows}

    raja = next(row for name, row in by_name.items() if name.startswith("Abhay Kumar Sinha"))
    assert raja.party == "RJD"
    assert raja.constituency == "AURANGABAD"
    assert raja.criminal_cases == 16
    assert raja.assets_rupees == 10_77_18_589


def test_assets_are_plausible_magnitudes():
    """Guards the tilde bug specifically: reading the rounded gloss instead of the exact
    figure would leave most winners declaring implausibly tiny sums."""
    rows = parse_fixture("LokSabha2024", 2024)
    assets = [row.assets_rupees for row in rows if row.assets_rupees]

    assert min(assets) > 100_000
    assert max(assets) > 10_000_000


def test_a_page_without_a_candidate_table_is_empty_not_an_error():
    """This is how the crawler detects it has run past the end of the pagination."""
    assert (
        parse_listing(
            "<html><body><table><tr><td>nothing here</td></tr></table></body></html>",
            election_slug="LokSabha2024",
            election_year=2024,
            house="Lok Sabha",
            view="winner_analyzed",
        )
        == []
    )


def test_a_table_missing_expected_columns_raises():
    """Better to fail loudly than to guess which column held the assets."""
    html = "<table><tr><th>Candidate</th><th>Party</th></tr><tr><td>X</td><td>Y</td></tr></table>"

    with pytest.raises(ValueError, match="missing expected columns"):
        parse_listing(
            html,
            election_slug="LokSabha2024",
            election_year=2024,
            house="Lok Sabha",
            view="winner_analyzed",
        )
