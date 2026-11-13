# ClickHouse

ClickHouse is the platform's staging engine. It reads the Parquet lake in the object store (SeaweedFS, S3 API) through `s3()` views and exposes the `stg_*` staging views that the vault bridge copies into Postgres. The Data Vault and the marts live in Postgres (see `3_storage/postgres/README.md`).

| Database | Contents | Built by |
|---|---|---|
| `nhdw_staging` | `raw_<source>__<object>` views over lake Parquet and the dbt `stg_*` views on top of them. No rows are persisted. | `2_pipelines/staging/` (raw views + dbt-clickhouse) |
| `nhdw_marts` | Empty by default. Created by the image entrypoint (`CLICKHOUSE_DB`) and ensured by the raw-views job; the dbt marts project targets Postgres, not this database. | `1_infrastructure/k8s/base/clickhouse.yaml`, `2_pipelines/staging/scripts/apply_raw_views.py` |

## Kubernetes deployment

`1_infrastructure/k8s/base/clickhouse.yaml` runs `clickhouse/clickhouse-server:24.3` with a single login user `nhdw` (password from the `clickhouse-secrets` Secret). It uses two files from this directory:

| File | How the deployment uses it |
|---|---|
| `config/users.d/nhdw-named-collections.xml` | Packaged into the `clickhouse-users-d` ConfigMap (`1_infrastructure/k8s/base/kustomization.yaml`) and mounted into `/etc/clickhouse-server/users.d/`. Grants `nhdw` use of server-defined named collections; ClickHouse 24.x denies that by default. |
| `config/config.d/named_collections.xml.template` | Template for the `clickhouse-named-collections` Secret, defined in `1_infrastructure/k8s/overlays/dev/secrets.yaml.template` and mounted as `config.d/named_collections.xml`. It defines the `nhdw_minio` collection with the object-store credentials. |

Every raw view reads the lake through that collection, so no SQL carries credentials:

```sql
SELECT * FROM s3(nhdw_minio, url = 'http://.../nhdw-raw/<source>/<object>/**/*.parquet', format = 'Parquet')
```

The collection name `nhdw_minio` is historical; it points at the SeaweedFS S3 endpoint.

The databases and raw views are created by the `ch-raw-views` job (`2_pipelines/staging/scripts/apply_raw_views.py`, applying `2_pipelines/staging/raw_views/raw_views.sql`), not by the files in `init/`. That job ensures both databases exist, drops stale `raw_*`/`stg_*` views, probes which lake tables hold Parquet, and creates an empty typed view for an absent table so the pipeline yields zero-row outputs instead of failing.

## Local compose configuration

The remaining files configure a standalone ClickHouse container (named `clickhouse`) with per-workload users. The Kubernetes manifests do not mount them, and no compose file is kept in this directory.

| File | Purpose |
|---|---|
| `config/config.d/listen.xml` | Listen on all container interfaces so peers on the compose network can connect. |
| `config/users.d/default-user.xml` | Restrict the `default` user to localhost. |
| `config/users.d/profiles.xml` | Settings profiles and quotas: `staging` (4 GB, 4 threads, 30 min), `marts` (6 GB, 6 threads, 10 min), `reader` (2 GB, 2 threads, 2 min, `readonly=2`). |
| `init/02_users.sh`, `init/02_users.sql.template` | Entry-point script that substitutes passwords into the template and creates `staging_writer`, `mart_writer` and `bi_reader` with those profiles. |
| `.env.example` | Object-store credentials for the named collection (`MINIO_*` names are historical), the three user passwords and the data directory. |
| `scripts/verify.sh` | Smoke test: `/ping`, both databases present, an `s3()` read through the named collection, row counts for a set of raw views. |

### Service users (local compose)

| User | Profile | Grants |
|---|---|---|
| `staging_writer` | `staging` | DDL/DML on `nhdw_staging`, `s3` source, `NAMED COLLECTION ON nhdw_minio` |
| `mart_writer` | `marts` | DDL/DML on `nhdw_marts`, `SELECT` on `nhdw_staging`, `POSTGRES` source |
| `bi_reader` | `reader` | `SELECT` on `nhdw_marts`; staging is hidden because its views are not stable contracts |

The templates use `DROP USER IF EXISTS` + `CREATE USER`, so re-running them rotates passwords.

## Adding a source object

When an ingestor lands a new Parquet path under `nhdw-raw/<source>/<object>/`, add a `raw_<source>__<object>` view to `2_pipelines/staging/raw_views/raw_views.sql` and declare it in `2_pipelines/staging/models/_sources.yml`, which makes it a `source()` for the staging models. The `ch-raw-views` job applies it on its next run.
