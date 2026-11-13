# OpenLMIS ingestor

One image for the OpenLMIS source family, with two lanes:

| Lane | Verb | Source | Lake prefix | Status |
|---|---|---|---|---|
| eLMIS v2 history | `history` | eLMIS history database on PostgreSQL 16, read directly | `nhdw-raw/elmis_hist/` | Primary: run by every bring-up |
| OpenLMIS v3 API | `reference-data`, `requisitions`, `stock`, `orders`, `all` | OpenLMIS v3 REST API (OAuth2) | `nhdw-raw/openlmis/` | Present but suspended: its Job ships with `suspend: true` and no v3 instance is deployed by default |

Both lanes write Parquet and `_manifest.json` sidecars to the `nhdw-raw` bucket of the data lake (SeaweedFS, S3 API) through the same dlt destination as the other ingestors.

## Data

The history lane reads the replica in `0_external/sources/OpenLMIS/`: a PostgreSQL database with Zambian eLMIS data for 2017 and 2018, either a pattern-preserving mutation of a historical extract (distributed separately) or, when that extract is absent, the output of the committed generator `0_external/sources/OpenLMIS/scripts/synthesize_zm_history.py`. Both are demonstration data. The data is Zambian and does not join the Ethiopian entity model, so it is carried as a separate source (`elmis_hist`) all the way into the vault and marts, and its facilities are never conflated with OpenLMIS v3 entities. See [docs/data-provenance.md](../../../docs/data-provenance.md) and `0_external/sources/OpenLMIS/README.md`.

## Layout

| Path | Purpose |
|---|---|
| `src/cli.py` | Entry point: `python -m src.cli <verb>` |
| `src/config.py` | Environment variables for both lanes; API credentials are checked only by the API verbs |
| `src/destination.py` | dlt filesystem destination; the source prefix is a parameter (`openlmis` or `elmis_hist`) |
| `src/manifest.py` | `_manifest.json` sidecar writer |
| `src/lineage.py` | Optional OpenMetadata lineage emitter |
| `src/pipelines/_runner.py` | Shared harness: run dlt resources, then write a manifest per produced table |
| `src/pipelines/history.py` | History lane: dlt `sql_database` over SQLAlchemy |
| `src/pipelines/_client.py` | v3 API client: OAuth2 password grant, one re-auth on 401, retries, Spring-style paging |
| `src/pipelines/reference_data.py`, `requisitions.py`, `stock.py`, `orders.py` | v3 API pipelines |
| `run-history-from-host.sh` | Runs the history lane from the VM host with the same image, through a port-forward to `seaweedfs-s3`; for use before the image is imported into the cluster |

## History lane

`history` copies 16 tables, each replaced on every run; the history is immutable, so a re-run rewrites identical data. The largest table, `history.requisition_line`, streams through dlt's pyarrow backend. The `mutation` schema (provenance and validation metrics of the mutated extract) is not copied.

| Schema | Tables (lake name `<schema>_<table>`) |
|---|---|
| `reference` | `geographic_level`, `geographic_zone`, `facility_type`, `facility`, `product_category`, `product_form`, `product_group`, `dosage_unit`, `product`, `programme`, `programme_product`, `processing_schedule`, `reporting_period`, `facility_programme_schedule` |
| `history` | `requisition`, `requisition_line` |

```
s3://nhdw-raw/elmis_hist/<schema_table>/year=YYYY/month=MM/day=DD/elmis_hist_<schema_table>_<ts>_run-<id>_part-<file_id>.parquet
s3://nhdw-raw/elmis_hist/<schema_table>/year=YYYY/month=MM/day=DD/_manifest.json
```

Downstream, `stg_elmis_hist__*` staging models feed the `elmis_hist` raw vault and the eLMIS and supply-planning (`mart_pct_*`) marts.

## OpenLMIS v3 API lane

| Verb | Tables (`s3://nhdw-raw/openlmis/<table>/...`) |
|---|---|
| `reference-data` | `facilities`, `geographic_zones`, `programs`, `orderables`, `processing_periods`, `lots` |
| `requisitions` | `requisitions`, with line items unnested by dlt into `requisitions__requisition_line_items` |
| `stock` | `stock_cards` (one paged sweep of `/api/stockCards`, with embedded facility, program, orderable, lot and stock on hand) |
| `orders` | `orders`, `proofs_of_delivery` (fetched per order id, because the unfiltered endpoint returns 500) |
| `all` | All four pipelines |

Every table is replaced on each run: the data is small, and requisitions change in place along their status workflow, so a snapshot per run is the correct shape. The lane needs an OpenLMIS v3 instance at `OPENLMIS_BASE_URL` and `OPENLMIS_*` credentials in the `source-creds` Secret; neither is provided by the default install.

## Running

Both lanes are Kubernetes Jobs in `1_infrastructure/k8s/base/ingestor-jobs.yaml`:

- `elmis-hist-ingest`, args `["history"]`, reading `host.k3d.internal:5434`, database `elmis`. `bundle/scripts/seed-realistic.sh` runs it with the other ingestors.
- `openlmis-ingest`, args `["all"]`, base URL `http://host.k3d.internal:8086`. The Job is created suspended and is not scheduled.

The image is `nhdw/openlmis-ingestor`, built by `bundle/scripts/build-images.sh`. Outside the cluster:

```bash
docker run --rm --env-file .env nhdw/openlmis-ingestor:1.0.0 history
bash 2_pipelines/ingestor/OpenLMIS/run-history-from-host.sh   # host network + port-forward
```

## Configuration

`.env.example` lists every variable.

| Variable | Default | Lane | Meaning |
|---|---|---|---|
| `OPENLMIS_HIST_HOST` | empty (required) | history | PostgreSQL host |
| `OPENLMIS_HIST_PORT` | `5434` | history | PostgreSQL port |
| `OPENLMIS_HIST_DB` | `elmis_historical` | history | Database (the Job sets `elmis`) |
| `OPENLMIS_HIST_USER`, `OPENLMIS_HIST_PASSWORD` | `elmis_reader`, password required | history | Read-only login |
| `OPENLMIS_BASE_URL` | empty (required) | API | OpenLMIS v3 base URL |
| `OPENLMIS_USER`, `OPENLMIS_PASSWORD` | empty (required) | API | OAuth2 password-grant user |
| `OPENLMIS_OAUTH_CLIENT`, `OPENLMIS_OAUTH_SECRET` | `user-client`, `changeme` | API | OAuth2 client (HTTP Basic on `/api/oauth/token`) |
| `OPENLMIS_PAGE_SIZE` | `200` | API | Page size for paged endpoints |
| `OPENLMIS_TIMEOUT` | `120` | API | HTTP timeout in seconds |
| `MINIO_ENDPOINT` | required | both | S3 endpoint of the lake |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | required | both | Object-store credentials |
| `MINIO_RAW_BUCKET` | `nhdw-raw` | both | Target bucket |
| `MINIO_REGION` | `et-addis-1` | both | S3 region name |
| `DLT_PIPELINE_NAME` | `openlmis` | both | Prefix of dlt's pipeline state |
| `OM_LINEAGE_API`, `OM_LINEAGE_JWT` | unset | both | Enable lineage emission to OpenMetadata |

The `MINIO_*` names are historical; the object store is SeaweedFS.
