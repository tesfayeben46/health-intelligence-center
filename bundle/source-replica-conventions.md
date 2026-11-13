# Source replica conventions

The source replicas under `0_external/sources/` stand in for the systems the platform ingests from. They run outside the k8s cluster as Docker Compose stacks, expose each source's own protocol (SQL Server TDS, MySQL, PostgreSQL, the DHIS2 REST API, FHIR R4, HTTP), and hold demonstration data only (see [`docs/data-provenance.md`](../docs/data-provenance.md)). The ingestors connect to a replica exactly as they would to the production system; pointing them at a real source is a change of host, port and credentials in the `source-creds` Secret.

`bundle/scripts/sources-up.sh` brings every replica up and seeds it; see [`0_external/README.md`](../0_external/README.md) for what each replica runs and how it is seeded.

## Folder layout

Each source folder contains what that source needs, typically:

```
0_external/sources/<Source>/
├── README.md            what the source is, how it is seeded and verified
├── docker-compose.yml   self-contained stack (absent for mBrana and IDSR, see below)
├── .env.example         committed placeholders; sources-up.sh copies it to .env when missing
├── sample-data/         restore artifacts (gitignored; see SEED-DATA-NOTICE.md where present)
├── schema/ or init/     DDL and container init scripts, where the source needs them
└── scripts/             restore.sh, synthesize*.py, verify.sh and, for most sources, reset.sh
```

Two folders have no compose file: `mBrana/` restores its `HCMISWCloud` database into the HCMIS WDM SQL Server container, and `IDSR/` holds seeders that write surveillance and population data into DHIS2. `Climate/` is a small FastAPI service with a generator script rather than a database.

## Ports

Ports are published on all host interfaces so k3d pods can reach them through `host.k3d.internal`. On a shared host, bind them to a private interface or firewall them.

| Source | Container | Host port | Container port | Protocol |
|---|---|---|---|---|
| DHIS2 | `dhis2-web` | 8080 | 8080 | HTTP (REST API) |
| DHIS2 | `dhis2-db` | 5433 | 5432 | PostgreSQL (host-side debugging and restore) |
| HCMIS WDM and mBrana | `hcmiswdm-sqlserver` | 1433 | 1433 | TDS (databases `HCMISWDM` and `HCMISWCloud`) |
| EthioEMR | `ethioemr-mysql` | 33306 | 3306 | MySQL (OpenMRS schema; no OpenMRS web application) |
| MFR | `nhdw-mfr-fhir` | 8093 | 8080 | HTTP (FHIR R4) |
| eLMIS | `elmis-postgres` | 5434 | 5432 | PostgreSQL |
| Climate | `climate-api` | 8095 | 8000 | HTTP |

The related external services follow the same pattern: Fayda (`fayda-replica`, port 8096) and the assistant model server (`nhdw-ollama`, port 11434).

## Networks

Each stack has its own private bridge network (`<source>_net`). No stack joins a platform network: the replicas model the outside world, and in-cluster pods reach them only through `host.k3d.internal:<host port>`.

## Data volumes

Database state lives in named Docker volumes declared in each compose file, not in bind mounts:

| Source | Volumes |
|---|---|
| DHIS2 | `dhis2_db_data`, `dhis2_home` (plus `config/dhis.conf` bind-mounted read-only) |
| HCMIS WDM and mBrana | `hcmiswdm_data`, `hcmiswdm_backup` |
| EthioEMR | `ethioemr_mysql_data` |
| MFR | `mfr_h2`, `mfr_lucene` |
| eLMIS | `elmis_db_data` |

The Climate API bind-mounts `bundle/seeds/sources/climate/` read-only and serves the generated `weekly.csv` from it. Restore artifacts are copied into the containers by each source's `scripts/restore.sh` (`docker cp` or stdin).

`sources-up.sh down` stops the stacks and keeps the volumes; `sources-up.sh down --wipe` (or `bootstrap.sh teardown --wipe-sources`) deletes them, after which every source re-seeds from the bundle on the next bring-up. Do not wipe the MFR volumes on their own: re-seeding regenerates the FHIR ids the vault uses as the facility hub business key.

## Credentials

`.env.example` files and compose defaults carry `changeme-*` placeholders for local use. Each source defines an administrative login used only by its restore and seed scripts and a read-only `ingestor` login used by the pipeline. The reader passwords must match the `source-creds` Secret (`1_infrastructure/k8s/overlays/dev/secrets.yaml`) that the in-cluster ingestors read; the ingestors never embed credentials.

## Seeding contract

- A replica starts with `docker compose up -d` from its own folder and does not depend on any other source, except mBrana, which needs the HCMIS WDM container.
- Restore and seed scripts are idempotent. The SQL sources restore their artifact and then synthesise only when the database lacks rows stamped with the current synthesizer version and country profile, so a packed bundle keeps its rows and a factory bundle seeds itself.
- A replica knows nothing about the warehouse, dbt, the object store or the ingestors.

## Naming

- Container: `<source>-<service>` in lower kebab case (`dhis2-web`, `hcmiswdm-sqlserver`), except `nhdw-mfr-fhir`.
- Network: `<source>_net`.
- Environment variables: `<SOURCE>_` prefix in upper snake case.
