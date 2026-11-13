# mBrana source replica

Stands in for mBrana, the community-level commodity reporting platform: health extension workers report stock on hand, requests and shipment receipts from their phones (USSD), and the platform's `HCMISWCloud` database stores them beside shared masters (institutions, products, administrative units). The mBrana ingestor (`2_pipelines/ingestor/mBrana`) reads the tables over TDS.

The replica is **the `HCMISWCloud` schema restored into the HCMIS WDM SQL Server container** (`hcmiswdm-sqlserver`, port 1433). mBrana has no compose file of its own; start the instance from `../HCMISWDM`.

**All rows are synthetic.** The schema comes from a schema-only backup (`sample-data/HCMISWCloud.bak`, distributed separately); every row is written by `scripts/synthesize.py`.

## Layout

```
.env.example            sa and reader passwords; synthesis controls
sample-data/            restore artifact HCMISWCloud.bak (distributed separately)
scripts/restore.sh      restores HCMISWCloud into hcmiswdm-sqlserver; provisions ingestor and openfn_mbrana
scripts/synthesize.py   generates the rows
scripts/verify.sh       connectivity, reader access and row counts
scripts/reset.sh        rebuild script (see Notes)
```

## Seeding

A fresh clone cannot seed this source until `sample-data/HCMISWCloud.bak` is supplied (see `sample-data/SEED-DATA-NOTICE.md`).

```bash
docker compose -f 0_external/sources/HCMISWDM/docker-compose.yml up -d
cd 0_external/sources/mBrana
cp .env.example .env               # MBRANA_SA_PASSWORD must equal HCMISWDM_SA_PASSWORD
bash scripts/restore.sh
python3 scripts/synthesize.py
bash scripts/verify.sh
```

`bundle/scripts/sources-up.sh up hcmis` restores and synthesizes mBrana right after HCMIS WDM. It skips synthesis when `dbo.SynthMeta` carries the current `SYNTH_VERSION` and country profile and `mFlowUSSD.SOHLogs` has rows.

## What the generator writes

Everything is drawn from the shared entity model (`bundle/seeds/entity-model/_build/`) and loaded in one transaction, with the version marker in the same transaction.

| Area | Tables |
|---|---|
| Lookups | `mBrana.SiteStatus`, `mBrana.TrendStatus`, `Commodity.CommodityType`, `Institution.OwnershipType`, `USM.EnvironmentGroup` |
| Geography | `dbo.Region`, `dbo.Zone`, `dbo.Woreda` (capped at `MBRANA_SYNTH_WOREDA_LIMIT`, default 200, covering every region), `AdministrativeUnit.AdministrativeUnit` as a `hierarchyid` tree |
| Masters | `USM.Environment` (EPSS hubs), `Commodity.Product` (shared commodities), `dbo.Institution` (shared facilities; `facility_id` in `Town`, MFR id in `IvrID`), `mBrana.InstitutionEnvironment` |
| Mobile flow | `mFlowUSSD.Commodities`, `Institutions`, `Users`, `SOHLogs`, `Requests`, `RequestLines`, `ASNs`, `ASNLines` |

The mobile flow covers public health posts and health centres. Only about 58% of registered sites report at all (`MBRANA_SYNTH_ACTIVE_FRAC`), with varying month-to-month engagement, so completeness for this source is lower than for DHIS2 or HCMIS by construction. Stock reports cover the last `MBRANA_SYNTH_MONTHS_HISTORY` months of the shared calendar. The `RRF.*` tables are cleared but not populated.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MBRANA_SA_PASSWORD` | placeholder | `sa` of the shared instance; must equal `HCMISWDM_SA_PASSWORD` |
| `MBRANA_READER_PASSWORD`, `MBRANA_OPENFN_PASSWORD` | placeholders | read-only logins `ingestor` and `openfn_mbrana`; must match the `source-creds` Secret |
| `MBRANA_SYNTH_MONTHS_HISTORY` | 24 | months of mobile reports |
| `MBRANA_SYNTH_SEED` | 42 (script default 20260624) | random seed |
| `MBRANA_SYNTH_WOREDA_LIMIT`, `MBRANA_SYNTH_FACILITY_LIMIT` | 200, 893 | geography and institution caps (read by the script; not in `.env.example`) |

`MBRANA_SYNTH_WOREDA_COUNT` and `MBRANA_SYNTH_FACILITIES_PER_WOREDA` are still read but no longer drive geography.

## Notes

- The ingestor connects to the shared instance on port 1433 with `database=HCMISWCloud`. `MBRANA_PORT=1434` in `.env.example` is not served by any container.
- `scripts/reset.sh` expects a compose file and a `mbrana-sqlserver` container in this directory, neither of which exists; rebuild through `../HCMISWDM` and `scripts/restore.sh` instead.
