# SeaweedFS compatibility suite

A local Docker Compose stack and a set of scripts that check the S3 behaviour the platform relies on against SeaweedFS. The stack is isolated (own bridge network, high ports, no cluster attachment) and is not the deployment; see [`../README.md`](../README.md) for that. Results below were recorded against `chrislusf/seaweedfs` 4.45.

## Contents

| File | Purpose |
|---|---|
| `docker-compose.yml` | master, volume and filer (with in-process S3 on 18333), default leveldb2 filer store |
| `docker-compose.pgfiler.yml` | overlay that runs the filer against a PostgreSQL metadata store |
| `filer-schema.sql` | `filemeta` table with `COLLATE "C"` (the same schema `3_storage/postgres/init/23_seaweedfs_filer_db.sh` creates) |
| `identities.json` | test identities (admin, prefix-scoped, bucket-scoped, anonymous) |
| `compat_test.py` | Q1-Q9 and Q16; honours `S3_ENDPOINT`, and `--admin-only` skips the identity checks when run against a deployed endpoint |
| `extended_test.py` | Q10-Q13 |
| `prefix_probe.py` | tries each prefix-grant spelling (Q6) |
| `dlt_probe.py` | Q15: a real dlt load with the ingestor destination settings; `DLT_STRIP_EXPECT=1` enables the botocore workaround |

## Run

```bash
# Stage A: default (leveldb2) filer store
docker compose up -d
python3 compat_test.py

# Stage B: PostgreSQL filer store
docker compose down -v
docker compose -f docker-compose.yml -f docker-compose.pgfiler.yml up -d
docker exec -i swpoc-filerdb psql -U seaweedfs -d seaweedfs_filer < filer-schema.sql
docker compose -f docker-compose.yml -f docker-compose.pgfiler.yml restart filer
python3 compat_test.py

# Teardown
docker compose -f docker-compose.yml -f docker-compose.pgfiler.yml down -v
```

The ClickHouse check (Q14) used a separate `swpoc-clickhouse` container outside the compose file; remove it with `docker rm -f swpoc-clickhouse`.

## Results

Both filer stores gave the same results.

| # | Check | Result |
|---|---|---|
| Q1 | boto3 SigV4 with path-style addressing, gateway client config unchanged | pass |
| Q2 | presigned GET and PUT | pass; HTTP 200, bytes identical |
| Q3 | server-side `copy_object` across buckets | pass |
| Q4 | dlt partitioned key layout; prefix and delimiter listing | pass; deep keys preserved, `CommonPrefixes` correct |
| Q5 | ETag format and stability | pass; content MD5, stable across re-upload |
| Q6 | prefix-scoped credentials | pass with the `bucket/prefix/*` syntax (below) |
| Q6b | grant write without delete | fail: SeaweedFS `Write` includes delete |
| Q7 | bucket-level isolation between identities | pass |
| Q8 | versioning: enable, list versions, recover an overwrite | pass; prior version recovered byte-exact |
| Q9 | anonymous read on `nhdw-static`, denied elsewhere | pass; 200 on static, 403 on artifacts |
| Q10 | lifecycle expiry rule (`PutBucketLifecycleConfiguration`) | pass; accepted and read back (expiry itself not observed) |
| Q11 | multipart upload and download, 24 MB | pass; byte-exact, ETag has a `-3` part suffix |
| Q12 | user metadata and Content-Type survive put and server-side copy | pass |
| Q13 | pagination past 1,000 keys in byte order | pass; 1,100 keys across 2 pages |
| Q14 | ClickHouse `s3()`: write Parquet, read back, recursive `**/*.parquet` glob, named collection | pass; 8,500 rows, exact checksum on all paths |
| Q15 | real dlt 1.4.0 load with the ingestor destination settings, region `et-addis-1`, scoped credential | pass; one Parquet file at the contract path, readable by ClickHouse |
| Q16 | data PUT after a zero-byte PUT on one connection | pass only with the `Expect` header suppressed (below) |

### Prefix grant syntax (Q6)

| Grant | Result |
|---|---|
| `Read:nhdw-artifacts` | bucket-wide |
| `Read:nhdw-artifacts/projects` | denies everything |
| `Read:nhdw-artifacts/projects/` | denies everything |
| `Read:nhdw-artifacts/projects/*` | scoped to the prefix |

An unrecognised grant fails closed, so a mistyped identity looks like an outage, not a security hole.

### Volumes per bucket

SeaweedFS creates a separate volume collection per bucket and allocates 7 volumes on the first write, whatever the data size; the default collection takes 7 more. When the volume server's `-max` is exhausted, writes do not error: each put backs off inside the gateway and the client eventually hangs. Size `-max` above 7 * (buckets + 1) and watch `Free` on the master's `/dir/status`.

### Zero-byte PUT and botocore (Q16)

botocore sends `Expect: 100-continue` on every PutObject. When the body is empty, the server correctly omits the interim `100 Continue`, and botocore then misreads the next response on that connection ([botocore#3755](https://github.com/boto/botocore/issues/3755)); the next request stalls and is silently retried on a new connection, so the object can be written twice. dlt uploads files (which carry the header) and writes zero-byte `init` markers first, so dlt loads are affected.

| Client | Data PUT after empty PUT |
|---|---|
| boto3 default | 8,478 ms |
| boto3 with `Expect` stripped | 6 ms |
| curl | 5 ms |

Mitigations: set `BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE=true` (botocore 1.35 or later), or strip the header in code:

```python
s3.meta.events.register("before-send.s3.PutObject", lambda request, **kw: request.headers.pop("Expect", None) and None)
```

### PostgreSQL filer store

Stage B passed identically to stage A, with metadata confirmed in `filemeta`. Two requirements: the schema is not auto-created (a filer against an empty database fails with `relation "filemeta" does not exist`), and `COLLATE "C"` is needed on `name`/`directory`, otherwise S3 list order is not byte-lexicographic and clients that merge sorted listings report spurious differences.

### dlt image notes

dlt 1.4.0 imports `pkg_resources`, which `setuptools >= 81` no longer ships, so ingestor images pin `setuptools<81`; `python:3.12-slim` does not include setuptools at all.

## Not covered

- Lifecycle enforcement (the rule is accepted; expiry was not observed).
- OIDC / `AssumeRoleWithWebIdentity`.
- Behaviour under the cluster's resource limits (the stack runs unconstrained).
