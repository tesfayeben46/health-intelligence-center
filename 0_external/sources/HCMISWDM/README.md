# HCMIS WDM source replica

Stands in for HCMIS WDM (Health Commodity Management Information System, Warehouse Data Management), the EPSS supply-chain database for the central warehouse and regional hubs. The replica is a **SQL Server 2025 database with the HCMIS WDM schema only**; no HCMIS application runs. The same SQL Server container also hosts mBrana's `HCMISWCloud` database (see `../mBrana`). The HCMISWDM ingestor (`2_pipelines/ingestor/HCMISWDM`) reads the tables over TDS.

**All rows are synthetic.** The schema comes from a schema-only backup (`sample-data/HCMISWDM.bak`, distributed separately); every row is written by the synthesizers in `scripts/`.

## Layout

```
docker-compose.yml                 SQL Server 2025 Developer (container hcmiswdm-sqlserver), port 1433
.env.example                       sa and reader passwords; synthesis controls
sample-data/                       restore artifact HCMISWDM.bak (distributed separately)
scripts/restore.sh                 restores the schema and provisions nscd_reader and openfn_nscd
scripts/synthesize.py              Vaccine.* operational tables
scripts/synthesize_analytics.py    Dim.*, Commodity.* and Fact.* analytics tables
scripts/synthesize_dispatch.py     dispatch plan tables
scripts/verify.sh                  connectivity, reader access and row counts
scripts/reset.sh                   drops the volume, restores and synthesizes again
```

SQL Server 2025 is required: the backups carry internal database version 998, above what SQL Server 2019 or 2022 can restore.

## Seeding

A fresh clone cannot seed this source until `sample-data/HCMISWDM.bak` is supplied (see `sample-data/SEED-DATA-NOTICE.md`).

```bash
cd 0_external/sources/HCMISWDM
cp .env.example .env
docker compose up -d
bash scripts/restore.sh
python3 scripts/synthesize.py
python3 scripts/synthesize_analytics.py
python3 scripts/synthesize_dispatch.py
bash scripts/verify.sh
```

`bundle/scripts/sources-up.sh up hcmis` runs the same steps, then restores and synthesizes mBrana in the same instance. It skips synthesis when the restored database carries facts stamped (in `dbo.SynthMeta` and `dbo.DispatchSynthMeta`) with the current synthesizer versions and country profile.

## What the synthesizers write

All reference data comes from the shared entity model (`bundle/seeds/entity-model/_build/`). Shared keys are kept in description columns (`hub_id=…`, `facility_id=…`) so the warehouse joins HCMIS rows to the other sources.

| Script | Tables | Content |
|---|---|---|
| `synthesize.py` | `Vaccine.Environment`, `Unit`, `Item`, `Institution`, `DailyRecord`, `Received`, `Issued`, `Expired`, `PurchaseOrder` | EPSS hubs, EPI vaccines and every shared facility; weekly stock snapshots per hub, vaccine and unit, receipts, issues and expiries over `SYNTH_YEARS_HISTORY` years back from the run date; open and recent purchase orders (table created if absent). One transaction. |
| `synthesize_analytics.py` | `Dim.Mode`, `Program`, `Environment`, `Item`, `Date`; `Commodity.*`; `Fact.DailyRecord`, `Fact.Inventory` | Dimensions mirroring the Vaccine IDs, a daily calendar with Ethiopian dates from `periods.json`, the full commodity catalogue, and monthly facts by hub, commodity and mode. Each hub and commodity pair has a deterministic health profile (about 68% healthy, 20% tight, 8% low and 4% crisis-prone with contiguous zero-stock episodes), so stock-outs occur by construction. Loaded in bounded batches; the version marker is written last. |
| `synthesize_dispatch.py` | `Dim.DispatchHub`, `DispatchFacility`, `DispatchPeriod`; `Fact.Dispatch`, `Fact.DispatchRun` | The generated dispatch plan committed as `6_access-points/dashboards/internal-bi/ui/src/ct/data/plan.ts`, mirrored row for row. No randomness. |

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `HCMISWDM_SA_PASSWORD` | placeholder | `sa`, used by restore and the synthesizers; also the sa password of the shared instance mBrana uses |
| `HCMISWDM_NSCD_READER_PASSWORD`, `HCMISWDM_OPENFN_READER_PASSWORD` | placeholders | read-only logins `nscd_reader` and `openfn_nscd`; must match the `source-creds` Secret |
| `SYNTH_FACILITY_COUNT` | `all` | facilities loaded into `Vaccine.Institution` |
| `SYNTH_YEARS_HISTORY` | 3 | years of history |
| `SYNTH_RANDOM_SEED` | 20260624 | seed (the entity-model seed) |
| `SYNTH_FACT_DAILY_CAP` | 80000 | cap on `Fact.DailyRecord` rows |

## Notes

- `restore.sh` reads the logical file names from the backup, since they follow the backup's lineage rather than the database name, and re-maps database users orphaned by a restore.
- The engine is capped at 5 GB and the container at 6 GB; the analytics synthesizer commits in batches to stay within it.
- The port is published on `0.0.0.0:1433` so k3d pods reach it through `host.k3d.internal`.
