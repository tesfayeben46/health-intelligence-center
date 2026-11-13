# DHIS2 ingestor

Extracts aggregate HMIS and IDSR data from the DHIS2 Web API and writes it as Parquet to the `nhdw-raw` bucket of the data lake (SeaweedFS, S3 API). It is a one-shot dlt job: it runs one pipeline, writes a `_manifest.json` per table, and exits.

The DHIS2 instance it reads is the replica in `0_external/sources/DHIS2/`, populated with generated demonstration data (an Ethiopia-shaped metadata model, monthly HMIS values, weekly IDSR cases and woreda populations). See [docs/data-provenance.md](../../../docs/data-provenance.md).

## Layout

| Path | Purpose |
|---|---|
| `src/cli.py` | Entry point: `python -m src.cli <pipeline>` |
| `src/config.py` | All environment variables, read once into a `Config` |
| `src/destination.py` | dlt filesystem destination and the lake path layout |
| `src/manifest.py` | `_manifest.json` sidecar writer |
| `src/lineage.py` | Optional OpenMetadata lineage emitter (source table to raw view) |
| `src/pipelines/_client.py` | Thin DHIS2 Web API client (HTTP Basic, retries) |
| `src/pipelines/metadata.py` | `metadata` pipeline |
| `src/pipelines/data_value_sets.py` | `data-value-sets` pipeline |
| `tests/smoke_writer.py` | Writes five synthetic rows through the destination to check path, file name and manifest |

## Pipelines

| Verb | Tables | Load |
|---|---|---|
| `metadata` | `organisation_units`, `data_elements`, `data_element_groups`, `data_sets`, `category_combos`, `category_option_combos`, `indicators`, `programs` | Full refresh. Metadata is small and needed in full to interpret the facts. |
| `data-value-sets` | `data_value_sets` (each row tagged with its `data_set`) | Incremental on `lastUpdated`. dlt keeps the watermark; the first run starts at `DHIS2_INITIAL_LAST_UPDATED`. |
| `all` | Both, in sequence | |

`data-value-sets` lists every dataSet and calls `/api/dataValueSets` for the level-1 org unit with `children=true`. `/api/analytics.json` is not used: it serves DHIS2's pre-aggregated analytics tables, not raw data values.

## Output

```
s3://nhdw-raw/dhis2/<table>/year=YYYY/month=MM/day=DD/dhis2_<table>_<ts>_run-<id>_part-<file_id>.parquet
s3://nhdw-raw/dhis2/<table>/year=YYYY/month=MM/day=DD/_manifest.json
```

The date partition comes from one timestamp taken at run start, so every part of a run lands in the same partition. The manifest records run id, timings, file count and bytes; downstream checks rely on its shape. ClickHouse reads these files through the `raw_dhis2__*` views (`2_pipelines/staging/raw_views/raw_views.sql`).

## Running

In the cluster the ingestor runs as two Kubernetes Jobs defined in `1_infrastructure/k8s/base/ingestor-jobs.yaml`: `dhis2-ingest-metadata` (args `["metadata"]`) and `dhis2-ingest-dvs` (args `["data-value-sets"]`). `bundle/scripts/seed-realistic.sh` runs them with the other ingestors before the staging, vault and marts Jobs. Source credentials come from the `source-creds` Secret and object-store credentials from `minio-secrets`.

The image is `nhdw/dhis2-ingestor`, built by `bundle/scripts/build-images.sh` (`--only=dhis2-ingestor` builds just this one). Outside the cluster the same image runs with an env file based on `.env.example`:

```bash
docker run --rm --env-file .env nhdw/dhis2-ingestor:1.0.0 metadata
docker run --rm --env-file .env nhdw/dhis2-ingestor:1.0.0 -v data-value-sets
```

## Configuration

`.env.example` lists the source, object-store and pipeline variables. Lineage emission reads `OM_LINEAGE_API`, `OM_LINEAGE_JWT` and `OM_LINEAGE_TIMEOUT` as well (unset: no lineage is sent).

| Variable | Default | Meaning |
|---|---|---|
| `DHIS2_BASE_URL` | required | DHIS2 base URL (the Job uses `http://host.k3d.internal:8080`) |
| `DHIS2_USER`, `DHIS2_PASSWORD` | required | HTTP Basic credentials |
| `DHIS2_PAGE_SIZE` | `200` | Page size for metadata endpoints |
| `DHIS2_INITIAL_LAST_UPDATED` | `2020-01-01` | Watermark for the first `data-value-sets` run |
| `DHIS2_TIMEOUT` | `120` | HTTP timeout in seconds |
| `DHIS2_DEV_ROOT_OU` | empty | Restrict `data-value-sets` to one org-unit subtree |
| `DHIS2_DEV_START_DATE`, `DHIS2_DEV_END_DATE` | empty | Use a period bracket instead of the watermark |
| `DHIS2_DEV_MAX_ROWS` | `0` | Stop after N data values (`0` = unbounded) |
| `MINIO_ENDPOINT` | required | S3 endpoint of the lake (`http://seaweedfs-s3.nhdw.svc.cluster.local:8333`) |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | required | Object-store credentials |
| `MINIO_RAW_BUCKET` | `nhdw-raw` | Target bucket |
| `MINIO_REGION` | `et-addis-1` | S3 region name |
| `DLT_PIPELINE_NAME` | `dhis2` | Prefix of dlt's pipeline state |
| `OM_LINEAGE_API`, `OM_LINEAGE_JWT` | unset | Enable lineage emission to OpenMetadata; without them the emitter does nothing |

The `MINIO_*` names are historical; the object store is SeaweedFS.

## Notes

- dlt keeps incremental state under `DLT_PIPELINE_DIR` (`/app/state/dlt` in the image) and in the lake next to the data, so watermarks survive a fresh container.
- The `DHIS2_DEV_*` variables exist for bounded development pulls; full runs leave them empty.
- `tests/smoke_writer.py` is not copied into the image; mount `tests/` at `/app/tests` and run `python -m tests.smoke_writer` with the same env file. It reads the full `Config` (so the DHIS2 variables must be set) but never calls DHIS2, and writes to `s3://nhdw-raw/dhis2/smoke_test/`, which should be deleted afterwards.
