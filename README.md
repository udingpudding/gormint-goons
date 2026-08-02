# Gormint Goons

**An open data observatory for Indian governance.**

> **Status: work in progress.** The pipeline is being built in public. Parsers are
> incomplete, numbers are unreviewed, and nothing here should be cited yet. This banner
> comes down when the methodology page is finished.

Slice twenty years of Indian governance data by party, state, sector and year — criminal
cases declared by candidates, delivery on central infrastructure projects, and participation
in Parliament.

Every figure on the site traces back to a stored original document. Nothing is estimated,
modelled, or editorialised.

---

## What this is not

This project publishes **no composite score and no ranking of parties.** There is no "worst
party" number, because any such number is mostly a statement about how you chose to weight
its inputs. The site shows individual, defined metrics with explicit denominators, and lets
you do the comparing.

**Project delivery data is never broken down by political party.** A delayed central-sector
highway cannot be cleanly attributed to a state government, the central government, or the
contractor — so this project does not pretend otherwise. Infrastructure metrics are cut by
ministry, state and sector only. Party appears only where the link is documentary: a named
candidate, their party, and the affidavit they personally signed.

---

## Sources

All inputs are public. Attribution matters here, so it is stated up front rather than in a
footer.

| Pillar | Source | Status |
|---|---|---|
| Candidates & criminal cases | [MyNeta](https://myneta.info) — Association for Democratic Reforms (ADR) & National Election Watch | **Collected** — Lok Sabha winners, 2004–2024 |
| Infrastructure delivery | [Flash Report on Central Sector Projects](https://mospi.gov.in) — Ministry of Statistics & Programme Implementation (MoSPI) | **Blocked** — see below |
| Parliamentary participation | [MP Track](https://prsindia.org/mptrack) — PRS Legislative Research | Planned — 15th–18th Lok Sabha (2009–) |

ADR compiles candidate affidavits filed with the Election Commission of India. MoSPI Flash
Reports are compiled from the OCMS portal. Thanks are owed to all three organisations —
this project is a re-presentation of their work, not a replacement for it.

Note that the parliamentary pillar begins in 2009, not 2004: PRS MP Track does not cover the
14th Lok Sabha.

### Why MoSPI is not collected

The entire Flash Report archive — confirmed available monthly back to May 2007 — is served
from `ipm.mospi.gov.in`, whose TLS certificate **expired in January 2026**. Fetching it means
disabling certificate verification, and this project does not do that. The discovery code is
written and tested; collection begins if and when the certificate is renewed.

(The `uatipm.mospi.gov.in` mirror that search engines still index is a dead UAT host and
returns 404 for everything. The live archive is under `ipm.mospi.gov.in`.)

---

## How it works

A three-stage pipeline, built around the assumption that **document formats drift over
twenty years**:

1. **Archive** — fetch source documents and store them immutably, with a manifest recording
   URL, fetch time, SHA-256 and HTTP status. Never re-fetches an unchanged file.
2. **Parse** — era-versioned parsers turn raw documents into normalized rows. A 2007 PDF and
   a 2024 PDF are read by different parsers that emit the same schema.
3. **Transform** — normalized rows become derived metrics, published as Parquet.

Fetching and parsing are deliberately separate. A parser fix becomes a cheap local re-run
rather than re-downloading hundreds of files from government hosts, and every published
number stays traceable to a stored original.

The site is static. The browser queries the Parquet files directly using DuckDB-WASM, so
there is no backend and no database.

### Self-checking

Each MoSPI Flash Report states its own totals — project count, aggregate original cost,
aggregate revised cost. The parser's output must sum to the report's own figures or CI
fails. That gives an independent correctness check on every archived report.

---

## Repository layout

```
pipeline/
  archive/     fetchers and manifest
  parsers/     one module per (source, era)
  transform/   metrics and Parquet output
  tests/       golden files per parser era
data/
  raw/         immutable originals (not committed)
  normalized/  tidy intermediate
  public/      published Parquet the site reads
web/           Astro dashboard
docs/          methodology
```

## Running it

```sh
uv sync                                   # pipeline dependencies
uv run python -m pipeline archive --winners-only   # fetch listings
uv run python -m pipeline parse           # archived pages -> normalized rows
uv run python -m pipeline publish         # normalized rows -> data/public/
uv run pytest                             # tests

cd web && npm install && npm run dev      # the site, at localhost:4321
```

The three pipeline stages are separate commands on purpose. Archiving is slow, polite and
network-bound; parsing is fast and local and gets re-run every time a format surprise turns
up. Collapsing them would mean every parser fix cost another crawl.

---

## Scraping conduct

Requests are rate-limited, `robots.txt` is respected, an identifying User-Agent is sent, and
responses are cached so re-runs do not re-hit government servers. If you maintain one of
these sources and want something changed, please open an issue.

---

## Licence

Code is MIT. Derived datasets under `data/public/` are CC BY 4.0 and inherit the attribution
requirements of their upstream sources. See [LICENSE](LICENSE).
