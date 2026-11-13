# HCMIS WDM ingestor

Extracts the EPSS warehouse database (HCMIS WDM, database `HCMISWDM` on SQL Server) and writes each table as Parquet to the `nhdw-raw` bucket of the data lake (SeaweedFS, S3 API). HCMIS WDM has no REST or FHIR API, so the ingestor reads the tables over TDS with dlt's `sql_database` source, pyodbc and Microsoft ODBC Driver 18.

The database it reads is the replica in `0_external/sources/HCMISWDM/`: the HCMIS WDM schema restored from a schema-only backup (distributed separately), with every row written by the generators in `0_external/sources/HCMISWDM/scripts/` (stock events, issues, receipts, stock-out episodes, the vaccine module and the dispatch plan). See [docs/data-provenance.md](../../../docs/data-provenance.md).

## Layout

| Path | Purpose |
|---|---|
| `src/cli.py` | Entry point: `python -m src.cli extract [--only a,b]` |
| `src/config.py` | Environment variables and the SQLAlchemy URL |
| `src/pyodbc_setup.py` | Registers pyodbc converters for `hierarchyid`, `geometry` and `geography` (returned as hex strings) |
| `src/destination.py` | dlt filesystem destination and the lake path layout |
| `src/manifest.py` | `_manifest.json` sidecar writer |
| `src/lineage.py` | Optional OpenMetadata lineage emitter |
| `src/pipelines/tables.py` | Explicit table allowlist |
| `src/pipelines/extract.py` | The `extract` pipeline |

## Tables

The allowlist in `src/pipelines/tables.py` is the complete list; each lake table is named `<schema>_<table>` in lower case (for example `fact_dailyrecord`).

| Schema | Tables |
|---|---|
| `Commodity` | `CommodityType`, `Country`, `SupplierType`, `Category`, `UnitOfIssue`, `DosageForm`, `Program`, `Manufacturer`, `Supplier`, `Product` |
| `Dim` | `Mode`, `Program`, `Environment`, `Item`, `Date`, and the dispatch plan dimensions `DispatchHub`, `DispatchFacility`, `DispatchPeriod` |
| `Fact` | `DailyRecord` (the canonical stock-on-hand fact), `Inventory`, and the dispatch plan facts `Dispatch`, `DispatchRun` |
| `Vaccine` | `Environment`, `Unit`, `Item`, `Institution`, `DailyRecord`, `Received`, `Issued`, `Expired`, `PurchaseOrder` |

Every table is replaced on each run. `replace` truncates the table's lake prefix and rewrites it, so re-runs are idempotent and reflect the current source. dlt's `merge` is not used because it degrades to append on the plain-Parquet filesystem destination, and `replace` combined with an incremental cursor would keep only the delta. A large table would move to append with a primary key and an incremental cursor, as the EthioEMR ingestor does. The `cursor` field in `TableSpec` is not read by `extract.py`.

## Output

```
s3://nhdw-raw/hcmiswdm/<schema_table>/year=YYYY/month=MM/day=DD/hcmiswdm_<schema_table>_<ts>_run-<id>_part-<hash>.parquet
s3://nhdw-raw/hcmiswdm/<schema_table>/year=YYYY/month=MM/day=DD/_manifest.json
```

The processed-zone Job drops supplier contact columns from `commodity_supplier` (`2_pipelines/staging/scripts/pii_policy.yaml`).

## Running

The Kubernetes Job `hcmiswdm-ingest` in `1_infrastructure/k8s/base/ingestor-jobs.yaml` runs the image with args `["extract"]`. It connects to `host.k3d.internal:1433` as the read-only login `nscd_reader`, which `0_external/sources/HCMISWDM/scripts/restore.sh` provisions; the password comes from the `source-creds` Secret.

The image is `nhdw/hcmiswdm-ingestor`, built by `bundle/scripts/build-images.sh`; it bundles ODBC Driver 18. Outside the cluster:

```bash
docker run --rm --env-file .env nhdw/hcmiswdm-ingestor:1.0.0 extract
docker run --rm --env-file .env nhdw/hcmiswdm-ingestor:1.0.0 extract --only fact_dailyrecord,vaccine_issued
```

## Configuration

`.env.example` lists the source, object-store and pipeline variables. Lineage emission reads `OM_LINEAGE_API`, `OM_LINEAGE_JWT` and `OM_LINEAGE_TIMEOUT` as well (unset: no lineage is sent).

| Variable | Default | Meaning |
|---|---|---|
| `HCMISWDM_HOST` | required | SQL Server host |
| `HCMISWDM_PORT` | `1433` | SQL Server port |
| `HCMISWDM_DATABASE` | required | Database (`HCMISWDM`) |
| `HCMISWDM_USER`, `HCMISWDM_PASSWORD` | required | Read-only login |
| `HCMISWDM_DRIVER` | `ODBC Driver 18 for SQL Server` | ODBC driver name |
| `HCMISWDM_TRUST_SERVER_CERTIFICATE` | `yes` | Accept the replica's self-signed certificate; set `no` with a CA-signed one |
| `HCMISWDM_TIMEOUT` | `120` | Connection timeout in seconds |
| `MINIO_ENDPOINT` | required | S3 endpoint of the lake |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | required | Object-store credentials |
| `MINIO_RAW_BUCKET` | `nhdw-raw` | Target bucket |
| `MINIO_REGION` | `et-addis-1` | S3 region name |
| `DLT_PIPELINE_NAME` | `hcmiswdm` | Prefix of dlt's pipeline state |
| `OM_LINEAGE_API`, `OM_LINEAGE_JWT` | unset | Enable lineage emission to OpenMetadata |

The `MINIO_*` names are historical; the object store is SeaweedFS.
