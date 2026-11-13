# eLMIS (OpenLMIS) source replica

## What this source is

**eLMIS** is the electronic Logistics Management Information System built on **OpenLMIS**: the requisition-and-resupply system health facilities use to report consumption and request commodities up the supply chain. It records what was requested, what was on hand and what was shipped, per facility, programme and reporting period.

In the platform it is a supply-chain source beside HCMIS WDM (EPSS warehouse movements) and mBrana (USSD stock-on-hand reports). Where those two describe the warehouse and the phone-reported snapshot, eLMIS describes the formal requisition cycle.

The replica is **PostgreSQL 16** (`elmis-postgres`) holding a historical extract; no OpenLMIS application runs. The OpenLMIS ingestor (`2_pipelines/ingestor/OpenLMIS`, verb `history`) reads the tables over the Postgres wire.

## What this replica contains: read this first

> **The data is Zambian, not Ethiopian.** The reference data is that of the Zambia eLMIS deployment: 2,807 facilities, a Country -> Province -> District hierarchy over Zambia's ten provinces, and programmes including the CHAZ (Churches Health Association of Zambia) mirrors. The requisitions fall in 2017 and 2018.
>
> The rest of the warehouse is keyed to the Ethiopian shared entity model in `bundle/seeds/entity-model/_build/` (893 `FAC-*` facilities under `ET-*` regions, period window 2023-01-01 to 2026-06-30).
>
> **These do not join.** Facility codes do not overlap, the geographic levels differ (Province/District against Region/Zone/Woreda) and the date ranges do not intersect. The source is therefore carried as a standalone lane; see [Integration](#integration).

### Where the data comes from

The replica is populated in one of two ways, both demonstration data:

1. **Historical extract** (`sample-data/elmis_historical_data_simulated.sql.gz`, 236 MB gzipped, about 1.5 GB plain; distributed separately). A pattern-preserving mutation of a historical Zambian eLMIS extract, produced by scenario `pattern-v1` (contract `zambia-elmis.pattern-preserving-mutation.v1`, algorithm `correlated-signal-hmac-v1`). The scenario is recorded in the `mutation` schema and summarised in `provenance-manifest-summary.json`.
2. **Fallback generator** (`scripts/synthesize.sh`, wrapping `scripts/synthesize_zm_history.py`). When the extract is absent, it generates a Zambian supply history from committed files: the Zambian entity model (`bundle/seeds/entity-model/entity-model.zm.yaml`) and facility registry (`bundle/seeds/entity-model/sample-data/facilities_zm_elmis.json`). Requisitions, quantities and facility names are generated deterministically from the entity-model seed, by default for the 18 months to 2018-08, and written to the same 16 tables as `schema-only.sql`. A fresh clone therefore seeds this source without any external artifact.

### Shape of the extract

This is not a live OpenLMIS operational schema. It is a historical extract already reshaped into three schemas:

| Schema | Purpose |
|---|---|
| `reference` | Conformed dimensions: facility, product, programme, geography, periods |
| `history` | Fact tables: requisitions and their line items |
| `mutation` | Provenance of the scenario that produced the extract (immutable; a trigger raises on any write) |

Row counts in the historical extract (`row-counts.txt`):

| Table | Rows |
|---|---|
| `history.requisition_line` | 7,543,506 |
| `history.requisition` | 73,926 |
| `reference.facility_programme_schedule` | 16,403 |
| `reference.facility` | 2,807 |
| `reference.programme_product` | 1,142 |
| `reference.product` | 1,100 |
| `reference.reporting_period` | 173 |
| `reference.geographic_zone` | 120 |
| `reference.product_category` | 51 |
| `reference.product_form` | 20 |
| `reference.facility_type` | 16 |
| `reference.product_group` | 12 |
| `reference.programme` | 11 |
| `reference.processing_schedule` | 10 |
| `reference.dosage_unit` | 9 |
| `reference.geographic_level` | 3 |
| `mutation.validation_metric` | 194 |
| `mutation.scenario_manifest` | 1 |

`history.requisition_line` is the grain that matters: one row per product per requisition, carrying the OpenLMIS calculation chain (`beginning_balance`, `quantity_received`, `quantity_dispensed`, `stock_in_hand`, `stockout_days`, `normalized_consumption`, `amc`, `max_stock_quantity`, `packs_to_ship`).

**Time coverage.** `reference.reporting_period` carries 173 periods across three schedules: `MONTHLY` (132, March 2008 to February 2019), `WEEKLY1` (39) and `WEEKLY` (2). The facts are far narrower: requisitions fall in 52 periods from 2017-09-01 to 2018-08-31. The rest of the period dimension is empty scaffolding; do not read the 2008 to 2019 span as data coverage.

Programmes present: `EM` (Essential Medicine), `HIV` (Test Kits), `ARV`, `LAB`, `TB`, `CDH` (Cancer Diseases Hospital), `SUFP`, plus the CHAZ mirrors `c_em`, `c_lab`, `c_hiv`, `c_arv`.

## Layout

```
docker-compose.yml                  postgres:16 (container elmis-postgres), port 5434
.env.example                        database and reader credentials, dump path, image tag
schema-only.sql                     DDL of the reference, history and mutation schemas
provenance-manifest-summary.json    scenario that produced the historical extract
row-counts.txt                      row counts of the historical extract
sample-data/                        historical extract (distributed separately)
scripts/restore.sh                  streams the extract into psql; provisions the ingestor role
scripts/synthesize.sh               fallback: schema-only.sql + generated data + ingestor role
scripts/synthesize_zm_history.py    the fallback generator
scripts/verify.sh                   reader connectivity, port, row counts, integrity checks
scripts/reset.sh                    reset script for an OpenLMIS v3 distribution (see Notes)
```

## Usage

```bash
cd 0_external/sources/OpenLMIS
cp .env.example .env
docker compose up -d
bash scripts/restore.sh        # with the extract in sample-data/ (5 to 15 minutes)
bash scripts/synthesize.sh     # or, without it
bash scripts/verify.sh
```

`bundle/scripts/sources-up.sh up elmis` keeps a populated database, otherwise restores the extract if present and falls back to the generator. It then ensures the read-only `ingestor` role on every run.

Port 5434 (5433 is the DHIS2 database), published on `0.0.0.0` so cluster pods reach it through `host.k3d.internal:5434`.

### Restore notes

- The dump is streamed (`zcat | psql`); 1.5 GB is never unpacked to disk or copied into the container.
- It carries the `\restrict` / `\unrestrict` psql meta-commands introduced in the August 2025 patch releases. `psql` older than 16.10 aborts on them, so `restore.sh` detects this and strips the two lines from the stream. With `ELMIS_PG_IMAGE_TAG` at 16 or 17 it never needs to.
- Indexes and foreign keys come at the tail of a plain `pg_dump`, so the last minutes of the restore are index builds.
- `restore.sh` runs `ANALYZE` afterwards; a plain-SQL restore leaves the planner without statistics.
- `synchronous_commit=off` trades durability for restore speed on this disposable, read-only replica. Do not copy it into the warehouse.

## Integration

eLMIS is carried as the standalone historical lane `elmis_hist`. The Zambian identities are preserved end to end and nothing is re-keyed to the Ethiopian entity model. The lane runs through the same layers as every source: ingestor, lake, ClickHouse staging, landing, a raw-vault area of its own and `bi_supply.mart_elmis_hist_*` marts, with the eLMIS consumption forecaster in `bundle/seed-pack/app/ml/`. The only bridge to master data is the MDM candidate table for commodities (`bi_mdm.mdm_commodity_match_candidates`); no link exists until a steward accepts a candidate.

Facility-named marts are classified sensitive in the mart attributes.

## Notes

- `scripts/reset.sh` targets an OpenLMIS v3 reference distribution (`openlmis-ref-distro/`, `docker-compose.override.yml`) that is not part of this directory; reset this replica with `docker compose down -v` followed by a restore or synthesis.
