# MFR ingestor

Extracts the Master Facility Registry (MFR) over its FHIR R4 API and writes it as Parquet to the `nhdw-raw` bucket of the data lake (SeaweedFS, S3 API). It is a one-shot dlt job with the same structure as the DHIS2 ingestor.

The registry it reads is the HAPI FHIR R4 replica in `0_external/sources/MFR/`. `0_external/sources/MFR/scripts/seed.py` posts Organization resources built from the shared demonstration entity model (a sample of the public facility list plus generated top-ups, with deterministically derived staffing figures). In this data the facilities are Organization resources; the replica holds no Location resources, so `facilities` is empty. See [docs/data-provenance.md](../../../docs/data-provenance.md).

## Layout

| Path | Purpose |
|---|---|
| `src/cli.py` | Entry point: `python -m src.cli facilities \| organizations \| all` |
| `src/config.py` | Environment variables, read once into a `Config` |
| `src/destination.py` | dlt filesystem destination and the lake path layout |
| `src/manifest.py` | `_manifest.json` sidecar writer |
| `src/lineage.py` | Optional OpenMetadata lineage emitter |
| `src/pipelines/_client.py` | FHIR client: paginated search over Bundle `next` links, retries, Basic or bearer auth |
| `src/pipelines/facilities.py` | Flatteners and the pipeline for both tables |

## Tables

| Verb / table | FHIR resource | Content |
|---|---|---|
| `facilities` | `Location` | One row per facility: identifiers, name, status, operational status, type, physical type, GPS position, address (flattened and raw), telecom, managing organization, parent |
| `organizations` | `Organization` | One row per organization: identifiers, name, alias, active flag, type, telecom, parent (`partOf`), staffing counts (physicians, nurses, health extension workers) from the staffing extension |

Both tables are full refresh (`replace`): a facility registry is slow-moving dimension data, and a full pull each run avoids stale dimension rows. Nested FHIR structures (identifiers, address, telecom) are kept as JSON strings, and each row also carries the complete resource in `raw_resource`, so nothing the source returned is lost. Column schemas are pinned in `facilities.py` so every Parquet file carries the same columns even when a batch has only NULLs in some of them.

`MFR_FHIR_SINCE` adds a `_lastUpdated` floor for an incremental pull. With `MFR_FHIR_MAX_PAGES=0` the client follows `link[next]` until the Bundle chain ends, so each resource type drains completely before the next starts; transient 502/503/504 and connection errors are retried. Each run logs throughput per resource and the auth mode, which shows whether Basic auth is being throttled compared with a bearer token.

## Output

```
s3://nhdw-raw/mfr/<table>/year=YYYY/month=MM/day=DD/mfr_<table>_<ts>_run-<id>_part-<hash>.parquet
s3://nhdw-raw/mfr/<table>/year=YYYY/month=MM/day=DD/_manifest.json
```

The vault loads these as `hub_mfr_facility` / `sat_mfr_facility` and `hub_mfr_organization` / `sat_mfr_organization`. The processed-zone Job drops `telecom` and `raw_resource` from `organizations` (`2_pipelines/staging/scripts/pii_policy.yaml`).

## Running

The Kubernetes Job `mfr-ingest` in `1_infrastructure/k8s/base/ingestor-jobs.yaml` runs the image with args `["all"]` against `http://host.k3d.internal:8093/fhir`. `bundle/scripts/seed-realistic.sh` runs it with the other ingestors.

The image is `nhdw/mfr-ingestor`, built by `bundle/scripts/build-images.sh`. Outside the cluster:

```bash
docker run --rm --env-file .env nhdw/mfr-ingestor:1.0.0 all
docker run --rm --env-file .env -e MFR_FHIR_MAX_PAGES=20 nhdw/mfr-ingestor:1.0.0 -v organizations
```

## Configuration

`.env.example` lists every variable.

| Variable | Default | Meaning |
|---|---|---|
| `MFR_FHIR_BASE_URL` | required | FHIR base URL (everything before `/Location`) |
| `MFR_FHIR_USER`, `MFR_FHIR_PASSWORD` | empty | HTTP Basic credentials, if required |
| `MFR_FHIR_TOKEN` | empty | Bearer token; takes precedence over Basic |
| `MFR_FHIR_PAGE_SIZE` | `200` | FHIR `_count` |
| `MFR_FHIR_MAX_PAGES` | `0` | Page cap per resource (`0` = follow every page) |
| `MFR_FHIR_SINCE` | empty | ISO 8601 floor on `_lastUpdated` (empty = full pull) |
| `MFR_FHIR_TIMEOUT` | `120` | HTTP timeout in seconds |
| `MFR_DEV_MAX_ROWS` | `0` | Row cap per resource (`0` = unbounded) |
| `MINIO_ENDPOINT` | required | S3 endpoint of the lake |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | required | Object-store credentials |
| `MINIO_RAW_BUCKET` | `nhdw-raw` | Target bucket |
| `MINIO_REGION` | `et-addis-1` | S3 region name |
| `DLT_PIPELINE_NAME` | `mfr` | Prefix of dlt's pipeline state |
| `OM_LINEAGE_API`, `OM_LINEAGE_JWT` | unset | Enable lineage emission to OpenMetadata |

The `MINIO_*` names are historical; the object store is SeaweedFS.

## Notes

The facility capacity, workforce and equipment marts also read MFR landing tables (including FHIR Device) that `bundle/scripts/seed-realistic.sh` loads directly from seed CSVs; those tables do not pass through this ingestor. See the Direct loads section of `docs/data-provenance.md`.
