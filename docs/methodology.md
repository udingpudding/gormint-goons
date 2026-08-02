# Methodology

How every figure on this site is produced, and what it does and does not mean.

## Where the data comes from

Candidate declarations come from [MyNeta](https://myneta.info), published by the Association
for Democratic Reforms and National Election Watch. ADR compiles them from the affidavits
every candidate is legally required to file with the Election Commission of India.

Coverage is the five Lok Sabha general elections from 2004 to 2024. Two listings are
collected for each: every candidate ADR analysed, and the subset who won.

## What "declared" means

Every number here is a **declaration**, not a conviction.

A criminal case in this data is a case the candidate themselves disclosed as pending against
them when they filed to contest. It is not a finding of guilt. Indian political practice also
means the count includes cases arising from protest and agitation alongside cases involving
serious alleged offences, and the raw count does not distinguish them. Where the distinction
matters, ADR's separate "serious criminal cases" classification — offences carrying five
years or more, and non-bailable — is the more meaningful measure.

Assets and liabilities are self-declared at the time of filing. They are not audited.

## Where the candidate population comes from

Figures are computed from the **per-constituency candidate lists**, which enumerate everyone
who stood in each seat. Two other routes were available and are not used as the population:

- The paginated summary listings cover only candidates whose affidavits ADR analysed, which
  is a filtered subset.
- They also omit the state, and constituency names are not unique.

The summary listings are still collected, but only as a **check**: winners counted from the
constituency pages must agree with winners enumerated by the `winner_analyzed` listing. The
two are parsed by different code from different pages, so agreement is real evidence rather
than a restatement.

## Denominators

Two cohorts are published side by side:

| Cohort | Denominator |
|---|---|
| `contested` | Every candidate the party fielded in that election |
| `won` | The subset who were elected |

**A winner is counted in both.** They are still someone the party put up. Defining
`contested` as "everyone who lost" would answer a question nobody asked.

These routinely differ and neither is the "real" number. A party can field many candidates
with declared cases while electing few, or the reverse — and that gap is one of the more
informative things in the dataset.

Note that a candidate's affidavit is not always legible or available to ADR, so some rows
carry no asset figure. Those are recorded as missing rather than zero, and are excluded from
median calculations rather than dragging them down.

### Small parties

No percentage is published for a party that fielded fewer than **10 analysed candidates** in
an election. A party with three candidates, all with a declared case, is factually "100%",
and placing that beside a party that fielded four hundred invites a comparison the numbers
cannot support. The underlying counts are still published; only the rate is withheld.

## Where the site editorialises, and where it does not

The site carries an openly satirical section — an honours list awarding parties certificates
for topping a column. The commentary around it is opinion. The figures inside it are not, and
the boundary is structural rather than a matter of good intentions:

**Every award is the maximum or minimum of one published metric**, shown with the denominator
it was computed over. Nothing is blended. A reader who disagrees with an award is disagreeing
with a count.

**No composite score. No weighted index.** A blended "governance index" is mostly a statement
about the weights chosen to build it. Publish one and every conversation becomes an argument
about those weights rather than about the data.

**Eligibility is fixed before the ranking, not after.** Only parties with at least 10 members
elected are considered for an award, using the same threshold that governs every other rate
on the site. That rule exists so a party with four seats cannot win an award on a rounding
artefact — and it is applied whether or not it produces a funnier winner.

**Party colours are used only in the honours list.** Charts stay neutral.

**No party attribution for infrastructure delivery.** When project delivery data is added, it
will be broken down by ministry, state and sector — never by political party. A delayed
central-sector highway cannot be cleanly attributed to a state government, the central
government, or the contractor, and assigning it to one of them would be an editorial act
dressed as a measurement. Party appears only where the link is documentary: a named
candidate, their party, and the affidavit they personally signed.

**Median, not mean, for assets and age.** A single billionaire candidate would otherwise move
a party's headline figure by an order of magnitude.

## Age

Self-declared, like everything else on the affidavit, and reported as a median alongside
assets.

MyNeta writes an undeclared age as `0` rather than leaving the field empty. Read literally,
165 candidates in this dataset are aged zero, and a further three are aged 4, 21 and 24.
Article 84(b) of the Constitution sets 25 as the minimum age to sit in the Lok Sabha, so any
value below it is a data error rather than a young candidate, and is treated as not declared.

Left uncorrected, those zeros would drag every median age down while looking perfectly
plausible — the same failure mode as reading an undeclared asset figure as ₹0.

Median age of members elected has risen from 52 in 2004 to 56 in 2024.

## How the numbers are produced

Three stages, deliberately separated:

1. **Archive.** Source pages are fetched and stored byte-for-byte under their SHA-256, with a
   manifest recording the URL, fetch time, hash and HTTP status. Nothing is parsed at this
   stage.
2. **Parse.** Archived pages are read from disk into normalized rows. Because parsing never
   touches the network, a parser correction is a local re-run rather than another crawl.
3. **Publish.** Normalized rows become the aggregate tables the site queries.

The manifest is committed to this repository even though the archived documents are not. That
means any figure can be traced to a specific document, with its hash and the date it was
retrieved, without the repository carrying gigabytes of source pages.

### Corrections and revisions

Documents are stored by content hash, so when a source page changes after publication the new
version is archived alongside the old rather than replacing it. Published tables use the most
recent revision. The manifest retains every revision, which makes an upstream correction
visible as a change in the record rather than a silent restatement.

## By-elections are excluded

MyNeta files by-elections under the general election that preceded them — a 2018 by-election
appears inside the 2014 section. Left in, they push the count of members elected past the 543
seats a reader expects and blend two different events into one figure.

They are archived and kept in the normalized data with an `is_bye_election` flag, and
excluded from every published general-election statistic. After exclusion the seat count is
exactly 543 for 2009, 2014, 2019 and 2024.

A related trap: in the 2009 listings, by-elections **reuse constituency ids** already used by
general-election seats. Id 1 covers Adilabad in Andhra Pradesh, Hisar in Haryana and Tehri
Garhwal in Uttarakhand. Attributing a seat by id alone would have filed candidates under the
wrong state, so each page's state and constituency are read from the page's own title rather
than from the index that links to it.

## Known limitations

- **2004 is missing 28 winners.** Twenty-eight of the 542 constituency pages for 2004 carry
  no winner marker at all upstream, so the 2004 elected cohort covers 514 seats rather than
  543. Later elections are complete. The 2004 figure is therefore a share of 514, printed
  alongside the percentage like every other denominator here.
- **A state's figure is not a verdict on its government.** State breakdowns count members
  returned to the Lok Sabha from that state, across all parties. They say nothing about who
  governs the state, and should not be read that way.
- **Constituency names repeat, so seats are identified by number.** Aurangabad is a seat in
  both Bihar and Maharashtra, Maharajganj in both Bihar and Uttar Pradesh, Hamirpur in both
  Himachal Pradesh and Uttar Pradesh. Grouping by name alone merges different members, so
  every seat carries its numeric constituency id and its state.
- **Party labels are as recorded per election.** Parties split, merge and rename. No attempt
  is made to trace a party's lineage across elections, so a renamed party appears as two
  distinct entries.
- **Names are not resolved to people.** The same politician appearing in 2009 and 2019 is two
  rows, not one. Asset growth for re-contesting candidates therefore requires name matching
  that has not yet been implemented and is not published.
- **Rupee figures are nominal.** Assets are as declared in the year of filing and are not
  inflation-adjusted, so amounts are not directly comparable across elections.
- **Lok Sabha only, for now.** State assemblies are covered by the source but not yet
  collected.

## Sources not yet included

The MoSPI Flash Report on Central Sector Projects — the monthly series covering every
infrastructure project of ₹150 crore or more — is not currently collected. Its entire archive
is served from hosts whose TLS certificate expired in January 2026, and this project does not
fetch over unverified connections. The discovery code is written and the series is confirmed
available back to May 2007; collection begins when the certificate is renewed.

PRS Legislative Research MP Track (parliamentary attendance, questions and debates, 15th–18th
Lok Sabha) is planned and not yet collected.

## Corrections

If a figure here is wrong, please open an issue on the repository. Include the metric, the
party or election, and what you believe the correct value is.
