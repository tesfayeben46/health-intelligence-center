# Metadata: OpenMetadata, lineage and classification

OpenMetadata (OM) holds the catalogue and lineage. It is not in the enforcement or serving path: OPA reads the committed `ops_perm` seed, and every pipeline and dashboard works with OM scaled to zero.

## Components

| Component | Location | Notes |
|---|---|---|
| OpenMetadata server 2.0.2 | `1_infrastructure/k8s/base/openmetadata.yaml`, and the Helm chart behind `values.openmetadata.enabled` | a `db-migrate` init container runs OM's schema migrations; UI and API on port 8585 |
| OpenSearch 2.19.6 | `base/opensearch.yaml` | single node, security plugin off, 1 GiB heap, used only by OM; yellow health is expected on one node |
| Metadata database | `nhdw_openmetadata` on postgres-vault | created by `3_storage/postgres/init/37_openmetadata_db.sh`; user `openmetadata_writer` |
| Ingestion DAG | `5_orchestrator/dags/openmetadata_ingestion.py` | daily at 05:00; connectors for Postgres, ClickHouse, the data lake, dbt and Airflow; a failed connector does not fail the run |
| Bootstrap | `bundle/scripts/seed-openmetadata.sh` | waits for OM, validates the bot token, triggers the DAG once |
| Lineage emitters | `2_pipelines/ingestor/*/src/lineage.py` | source system to raw view; active only when `OM_LINEAGE_API` and `OM_LINEAGE_JWT` are set |
| Bridge emitter | `2_pipelines/vault/bridge/lineage.py` | ClickHouse `nhdw_staging.stg_*` to Postgres `nhdw_landing.stg_*`, the hop no dbt project can infer; same activation rule |
| dbt exposures | `bundle/scripts/gen-exposures.py` writes `2_pipelines/marts/models/_exposures.yml` | mart to widget lineage, derived from the gateway's `fetchers.py`; rerun after widget changes, then rebuild the marts image |
| Classification sync | `bundle/scripts/sync-om-classification.py` | OM tags to `ops_perm.column_classification` and a committed seed file; writes only `managed_by='openmetadata'` rows; dry run by default, writes with `--apply` and `OM_CLASS_SYNC_ENABLED=true` |
| Dashboard connector | `bundle/scripts/sync-om-dashboards.py` | pages as OM dashboards, widgets as OM charts, mart to widget lineage; runs from `seed-openmetadata.sh` and after widget or page changes |

OM is served at `https://openmetadata.<hostBase>` with Keycloak SSO (`moh-staff`) and linked from the admin Documentation page for R-STEW, R-OPS-ADM and R-SUPER. OM administrators are the `admin` bootstrap principal and `superadmin`, set through `AUTHORIZER_ADMIN_PRINCIPALS`.

## Single sign-on

OM uses the confidential client `moh-staff/openmetadata`, seeded by `setup-keycloak.py`. The OM server exchanges the authorisation code at `/callback` and sets a session cookie. Discovery and JWKS calls go to Keycloak's internal service; only the browser uses the public host.

1. `OIDC_SERVER_URL` must be OM's public base URL. With the default (`localhost:8585`) sign-in fails with "Redirect URI must exactly match a trusted redirect URI".
2. The Keycloak client must not have a PKCE attribute. Keycloak applies `pkce.code.challenge.method` to the server-side flow, which sends no challenge. Keycloak merges client attributes on update, so clearing it means setting it to `""`.
3. OM stores its authentication and authorizer configuration in its database on first boot and uses that copy afterwards. After changing authentication settings, run `DELETE FROM openmetadata_settings WHERE configtype IN ('authenticationConfiguration','authorizerConfiguration');` and restart OM.

The public PKCE client mode is not usable with OM 2.0.2: the SPA starts the flow but does not complete the exchange at `/auth/callback` (open-metadata/OpenMetadata#26311).

The HikariCP pool is capped at 10 connections (`DB_CONNECTION_POOL_MAX_SIZE`); OM's default of 100 can exhaust Postgres `max_connections`.

## First deployment

1. Init script 37 creates the database; `openmetadata-secrets` comes from the secrets template.
2. Deploy OpenSearch and OpenMetadata; the Keycloak seed creates the client.
3. An OM administrator issues the ingestion bot token (Settings, Bots, ingestion-bot) and stores it:
   `kubectl -n nhdw patch secret openmetadata-secrets --type merge -p '{"stringData":{"INGESTION_BOT_TOKEN":"<token>"}}'`
4. Run `bundle/scripts/seed-openmetadata.sh`.

## Publishing access points to OM

Each access point (dashboard, report surface or BI tool) publishes its structure to OM as metadata, through a sync script modelled on `bundle/scripts/sync-om-dashboards.py`:

1. One OM `dashboardService` per access point (`CustomDashboard`, named after the access point).
2. Pages become OM dashboards and widgets become OM charts, read from the access point's own catalogue files.
3. Lineage edges from each mart to the widget chart and to the page dashboard, derived from the registry the access point serves from (for Internal BI, `fetchers.py`, parsed by `gen-exposures.py`). With these edges the graph runs from source system to individual widget.
4. The sync reads repository files and writes OM metadata only. It does not touch the gateway, OPA or `ops_perm`.
5. When `OM_API` or `OM_JWT` is unset the sync does nothing; API errors are logged as warnings and the script exits 0.
6. Writes are idempotent (createOrUpdate), with a `--dry-run` flag, and the sync runs from `seed-openmetadata.sh`. The same script sets the lineage view depth (8 upstream, 3 downstream) by writing the OM settings row and restarting OM when the value changes.
7. Every widget declares the marts it reads. Registry widgets are bound in `fetchers.py`; a widget served by its own route (the Control Tower in `routes/ct.py`) declares `WIDGET_MARTS` next to its queries. `bundle/scripts/test_gen_exposures.py` fails when a catalogue widget has no binding, when a route declares a mart it does not query, or when a fetcher joins a mart whose access attributes (`mart_attrs.yaml`) differ from its declared mart.

Lineage hops and their producers:

| Hop | Producer |
|---|---|
| source system to lake raw view | ingestor emitters |
| raw view to ClickHouse staging | dbt connector (staging project) |
| staging to Postgres landing | bridge emitter |
| landing to vault to marts | dbt connector (vault and marts projects) |
| marts to pages and widgets | dashboard connector |

Emitters write when data moves. After enabling one on an existing deployment, call the emitter for tables already landed to backfill.

Run `gen-exposures.py` and the dashboard sync after adding or moving widgets or pages; the first updates the dbt manifest, the second updates OM.

## Limits

- The Airflow lineage backend is not installed; the Airflow connector catalogues DAGs without task-level lineage.
- dbt docs remain available alongside OM on the Documentation page.
- Connector configurations use `awsRegion: us-east-1` because OM only accepts AWS region names; SeaweedFS ignores it.
- The data lake connector catalogues individual lake files; table-level lineage runs through the raw views.
