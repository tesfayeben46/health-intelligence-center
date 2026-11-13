# SeaweedFS object store

SeaweedFS (4.45) is the platform's object store. Every consumer (ingestors via dlt, ClickHouse `s3()` views, the gateway, MLflow, JupyterHub project sync, Airflow, the ML runner) talks to it through its S3 API at `seaweedfs-s3.nhdw.svc.cluster.local:8333`.

Some names predate SeaweedFS and are kept for compatibility: the credential Secret `minio-secrets`, the `MINIO_*` environment variables, the ClickHouse named collection `nhdw_minio` and the gateway module `minio_client.py`. They all carry SeaweedFS values.

## Components

```
consumers ──S3──▶ seaweedfs-s3 :8333 ──▶ seaweedfs-filer :8888 ──▶ postgres-vault (metadata)
                  (served by the filer)         │
                                                └──▶ seaweedfs-volume-0 :8080 ──▶ PVC (bytes)
                                                            ▲
                                          seaweedfs-master :9333 (placement)
```

| Component | Kind | State | If it fails |
|---|---|---|---|
| `seaweedfs-master` | Deployment, `Recreate` | PVC `seaweedfs-master-data` (1 Gi) | no new volume placement; reads continue |
| `seaweedfs-volume` | StatefulSet + headless Service | PVC `data-seaweedfs-volume-0` (20 Gi): the object bytes | all object I/O stops |
| `seaweedfs-filer` | Deployment, `Recreate` | stateless; metadata in the `seaweedfs_filer` database on `postgres-vault` | S3 API down until it returns |
| `seaweedfs-s3` | Service selecting the filer pod | the S3 gateway runs inside the filer process (`filer -s3`) | down with the filer |
| `seaweedfs-init` | Job (`minio/mc` client) | none | buckets, versioning and lifecycle rules not provisioned |

The components fail separately, and a healthy S3 port in front of a failed filer returns errors that resemble bad credentials, so probe all of them, not just `:8333`.

Deployment choices encoded in the manifest:

- **Filer registers as its pod IP** (`-ip=$(POD_IP)`). If it registered under the Service name, the master's delayed removal of a terminated pod would also remove the replacement pod's entry from the lock ring, and every S3 write would fail with `no lock server found` while reads kept working.
- **S3 gateway in the filer process.** A standalone gateway refreshes filer addresses from the master on a fixed five-minute interval, so after a filer restart it can dial a stale address and fail writes while health checks stay green.
- **`Recreate` strategy** so two filers never run against one Postgres store.
- **`-max=200` volumes.** SeaweedFS creates a volume collection per bucket (7 volumes on the first write), so `-max` must exceed 7 * (buckets + 1).
- **`WEED_LEVELDB2_ENABLED=false`**, otherwise the filer keeps its embedded store and ignores the Postgres settings.

## Buckets

Created by the `seaweedfs-init` Job (`mc mb --ignore-existing`, so re-running is safe):

| Bucket | Versioning | Lifecycle |
|---|---|---|
| `nhdw-raw` | enabled | |
| `nhdw-processed` | | |
| `nhdw-archive` | enabled | |
| `nhdw-lake` | | |
| `nhdw-exports` | | expire after 90 days (`exports-ttl-90d`) |
| `nhdw-artifacts` | enabled | |
| `nhdw-backups` | enabled | expire after 365 days (`backups-ttl-365d`) |
| `nhdw-static` | | |
| `nhdw-logs` | | |
| `nhdw-docs` | | |

## Identities

The S3 IAM surface is the static `identities.json` key of the `seaweedfs-secrets` Secret (template in `1_infrastructure/k8s/overlays/dev/secrets.yaml.template`), mounted into the filer. There are no runtime user or policy calls.

| Identity | Access |
|---|---|
| `nhdw_root` | Admin, read, write, list, tagging on all buckets |
| `mlflow` | read, write, list, tagging on `nhdw-artifacts` |
| `jupyter-sync` | read, write, list on `nhdw-artifacts/projects/*` |
| anonymous | read and list on `nhdw-static` |

Two SeaweedFS rules apply when editing identities: a prefix grant must use the `bucket/prefix/*` form (the bare `bucket/prefix` form is not recognised and denies everything), and `Write` includes delete, so `jupyter-sync` can delete within `projects/*` (bounded by the prefix; versioning on `nhdw-artifacts` allows recovery).

## Filer metadata in Postgres

The filer's key-to-chunk map is the `filemeta` table in the `seaweedfs_filer` database on `postgres-vault`, created by `3_storage/postgres/init/23_seaweedfs_filer_db.sh`. Keeping it there puts the lake's index inside the existing Postgres backup. Two constraints the init script enforces: SeaweedFS does not create the table itself, and `name`/`directory` must be `COLLATE "C"` so S3 listings are byte-ordered.

## Where things are defined

| What | File |
|---|---|
| Chart template (source of truth) | `1_infrastructure/helm/nhdw/templates/seaweedfs.yaml` |
| Rendered base manifest (regenerate with `helm template`, do not edit) | `1_infrastructure/k8s/base/seaweedfs.yaml` |
| Tunables (image tag, replication, volume size) | `1_infrastructure/helm/nhdw/values.yaml`, key `seaweedfs:` |
| Admin key, filer DB password, identities | `seaweedfs-secrets` in `1_infrastructure/k8s/overlays/dev/secrets.yaml.template` |
| Filer database and table | `3_storage/postgres/init/23_seaweedfs_filer_db.sh` |

## Client requirement: empty-body uploads

botocore sends `Expect: 100-continue` on every PutObject and mishandles the response after a zero-byte body ([botocore#3755](https://github.com/boto/botocore/issues/3755)): the next request on that connection stalls about 8.5 s, and the object may be written twice by a silent retry. dlt writes zero-byte `init` markers, so ingestors are affected. Every boto3 and dlt consumer sets `BOTO_EXPERIMENTAL__NO_EMPTY_CONTINUE=true` (botocore 1.35 or later); `migrate/restore_from_backup.py` strips the header in code instead.

## Directory contents

| Path | Contents |
|---|---|
| `ops/backup-objstore.sh` | S3-level backup of every `nhdw-*` bucket through a port-forward with `mc mirror`, into `$BACKUP_ROOT/objstore-<UTC stamp>/` with `manifest/SHA256SUMS` and `manifest/counts.txt`. Called by `7_security/scripts/backup.sh`; never deletes. Connection details come from `bundle/scripts/lib/objstore.sh`. |
| `migrate/restore_from_backup.py` | Restores an S3-layer backup (`objects/` plus `manifest/stat.jsonl`) into an S3 endpoint, preserving Content-Type and user metadata; resumes by skipping objects already present with the same size. |
| `migrate/verify_against_manifest.py` | Checks an endpoint against a backup manifest: object count per bucket, size per key, sha256 per body. Exit 0 only if every bucket passes. |
| `migrate/decode_stream.py` | Decodes a base64 object stream produced inside a pod into `objects/` and writes `manifest/SHA256SUMS`. |
| `poc/` | Local Docker Compose test stack and the S3 compatibility suite used to qualify SeaweedFS; see [`poc/README.md`](poc/README.md). |

The `migrate/` tools take the endpoint and credentials from `S3_ENDPOINT`, `S3_ACCESS_KEY` and `S3_SECRET_KEY`; their defaults point at the local test stack.

## Operations

```bash
kubectl -n nhdw get pods -l part-of=seaweedfs
kubectl -n nhdw logs deploy/seaweedfs-filer | grep -i "store\|postgres\|collat"
kubectl -n nhdw port-forward svc/seaweedfs-master 9333:9333     # then http://localhost:9333/cluster/status
kubectl -n nhdw exec deploy/seaweedfs-filer -- weed shell -master=seaweedfs-master:9333 <<< "volume.list"
```

A full restore needs both the `postgres-vault` backup (filer metadata) and the volume PVC `data-seaweedfs-volume-0`; neither alone restores the lake. The S3-level export from `ops/backup-objstore.sh` is engine-neutral and restores per bucket with `mc mirror --overwrite objects/<bucket> <alias>/<bucket>`. `migrate/restore_from_backup.py` and `migrate/verify_against_manifest.py` additionally need the `manifest/stat.jsonl` and `manifest/ls.jsonl` files, which `backup-objstore.sh` does not write.
