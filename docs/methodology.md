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

## Denominators

Rates are published against **candidates analysed**, not candidates who stood.

ADR can only analyse an affidavit it can obtain and read. Some are missing, illegible, or
filed too late. In the 2004 Lok Sabha, for instance, 457 of the 543 winners were analysed.
Every rate on this site is therefore a share of the analysed population, and the count is
printed beside the percentage so the gap is visible rather than implied.

Two denominators are always published side by side:

| Cohort | Denominator |
|---|---|
| `contested` | Candidates a party fielded and ADR analysed |
| `won` | The subset who were elected |

These routinely differ and neither is the "real" number. A party can field many candidates
with declared cases while electing few, or the reverse — and that gap is one of the more
informative things in the dataset.

### Small parties

No percentage is published for a party that fielded fewer than **10 analysed candidates** in
an election. A party with three candidates, all with a declared case, is factually "100%",
and placing that beside a party that fielded four hundred invites a comparison the numbers
cannot support. The underlying counts are still published; only the rate is withheld.

## What is deliberately not published

**No composite score. No ranking of parties.** A blended "governance index" is mostly a
statement about the weights chosen to build it. Publish one and every conversation becomes an
argument about those weights rather than about the data. This site publishes individually
defined metrics with stated denominators and lets the reader do the comparing.

**No party attribution for infrastructure delivery.** When project delivery data is added, it
will be broken down by ministry, state and sector — never by political party. A delayed
central-sector highway cannot be cleanly attributed to a state government, the central
government, or the contractor, and assigning it to one of them would be an editorial act
dressed as a measurement. Party appears only where the link is documentary: a named
candidate, their party, and the affidavit they personally signed.

**Median, not mean, for assets.** A single billionaire candidate would otherwise move a
party's headline figure by an order of magnitude.

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

## Known limitations

- **Candidates analysed is not candidates fielded.** Stated above; it is the single most
  important caveat on this site.
- **No state column, so no geography.** The listing pages give a constituency name but not
  the state it sits in, and constituency names are not unique: Aurangabad is a seat in both
  Bihar and Maharashtra, Maharajganj in both Bihar and Uttar Pradesh, Hamirpur in both
  Himachal Pradesh and Uttar Pradesh. Grouping by constituency name alone would silently
  merge them. State-level analysis therefore is not published, and will not be until the
  constituency-to-state mapping is collected from the per-state listings.
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
