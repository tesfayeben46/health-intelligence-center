# Climate ingestor

Pulls weekly meteorological observations (rainfall, temperature, humidity per region and ISO week) from the Climate API and writes them as Parquet to the `nhdw-raw` bucket of the data lake (SeaweedFS, S3 API). It is a one-shot dlt job with the same structure as the DHIS2 ingestor.

The API it reads is the small FastAPI service in `0_external/sources/Climate/`, which serves a deterministic, generated series (`generate_climate.py`, fixed seed) shaped like a public climate API. The data is demonstration data; see [docs/data-provenance.md](../../../docs/data-provenance.md). The marts use it as the climate covariate for disease surveillance (`mart_idsr_climate_drivers`, `mart_ml_disease_input`).

## Layout

| Path | Purpose |
|---|---|
| `src/cli.py` | Entry point: `python -m src.cli weekly` (`all` is an alias) |
| `src/config.py` | Environment variables, read once into a `Config` |
| `src/destination.py` | dlt filesystem destination and the lake path layout |
| `src/manifest.py` | `_manifest.json` sidecar writer |
| `src/lineage.py` | Optional OpenMetadata lineage emitter (source table to raw view) |
| `src/pipelines/weekly.py` | The `weekly` pipeline |

## Pipeline

`weekly` pages `GET /api/v1/climate/weekly` (CSV, `offset`/`limit`) and yields one row per region and week with `rainfall_mm`, `temp_celsius` and `humidity_pct` parsed as doubles. It is incremental on `week_start`: dlt stores the last value and sends it as `updated_since` on the next run, so only newer weeks are pulled. Rows are appended with primary key `(region_id, iso_week)`.

To reload everything, clear the dlt state; `CLIMATE_INITIAL_UPDATED_SINCE` sets the floor for a first run.

## Output

```
s3://nhdw-raw/climate/weekly/year=YYYY/month=MM/day=DD/climate_weekly_<ts>_run-<id>_part-<file_id>.parquet
s3://nhdw-raw/climate/weekly/year=YYYY/month=MM/day=DD/_manifest.json
```

ClickHouse reads the files through `raw_climate__weekly`, staged as `stg_climate__weekly` and loaded into the vault as `hub_climate_week` and `sat_climate_week`.

## Running

The Kubernetes Job `climate-ingest` in `1_infrastructure/k8s/base/ingestor-jobs.yaml` runs the image with args `["weekly"]` against `http://host.k3d.internal:8095`. `bundle/scripts/seed-realistic.sh` runs it with the other ingestors.

The image is `nhdw/climate-ingestor`, built by `bundle/scripts/build-images.sh`. Outside the cluster:

```bash
docker run --rm --env-file .env nhdw/climate-ingestor:1.0.0 weekly
```

## Configuration

`.env.example` lists every variable.

| Variable | Default | Meaning |
|---|---|---|
| `CLIMATE_API_BASE_URL` | `http://host.k3d.internal:8095` | Climate API base URL |
| `CLIMATE_PAGE_SIZE` | `10000` | Rows per request |
| `CLIMATE_TIMEOUT` | `60` | HTTP timeout in seconds |
| `CLIMATE_INITIAL_UPDATED_SINCE` | empty | ISO-date floor on `week_start` for the first run (empty = full history) |
| `CLIMATE_DEV_MAX_ROWS` | `0` | Stop after N rows (`0` = unbounded) |
| `MINIO_ENDPOINT` | `http://seaweedfs-s3.nhdw.svc.cluster.local:8333` | S3 endpoint of the lake |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | empty | Object-store credentials |
| `MINIO_RAW_BUCKET` | `nhdw-raw` | Target bucket |
| `MINIO_REGION` | `et-addis-1` | S3 region name |
| `DLT_PIPELINE_NAME` | `climate` | Prefix of dlt's pipeline state |
| `OM_LINEAGE_API`, `OM_LINEAGE_JWT` | unset | Enable lineage emission to OpenMetadata |

The `MINIO_*` names are historical; the object store is SeaweedFS.
