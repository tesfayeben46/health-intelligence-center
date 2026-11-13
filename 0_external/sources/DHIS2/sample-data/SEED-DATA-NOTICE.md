# DHIS2 restore artifact

| File | Required | Content |
|---|---|---|
| `dhis2_seeded.pgdump` | No | Custom-format `pg_dump` of a seeded DHIS2 database, written by `bundle/scripts/pack.sh` |

The file is excluded from the repository and distributed separately. It is optional: without it, `bundle/scripts/sources-up.sh` seeds DHIS2 through the REST API with `../scripts/seed_ethiopia.py`, which generates all metadata and values (and runs the IDSR and population seeders). When the file is present and the database holds no data values, `../scripts/restore.sh` restores it instead, which is faster.

The dump contains only generated demonstration data. See `docs/data-provenance.md`.
