# DHIS2 source replica

A DHIS2 2.40 instance (official `dhis2/core` image, PostGIS backend) that stands in for a national DHIS2. The DHIS2 ingestor (`2_pipelines/ingestor/DHIS2`) reads it through the standard Web API.

The software is real DHIS2. **The data is synthetic**: an Ethiopia-shaped metadata model and generated values, written through the REST API by the seed scripts below. No operational data is included.

## Layout

```
docker-compose.yml        dhis2-web (dhis2/core) + dhis2-db (postgis/postgis:14-3.3)
config/dhis.conf          DHIS2_HOME configuration (demo credentials, replica only)
scripts/seed_ethiopia.py  builds the model via REST: 5-level org-unit tree, data elements,
                          programme groups, the "HMIS Monthly" data set, monthly values over
                          36 months (~680k, capped at 800k); also runs the IDSR and population seeders
scripts/restore.sh        restores a packed state (sample-data/dhis2_seeded.pgdump) instead of seeding
scripts/verify.sh         post-seed sanity checks
scripts/extract-war.py    utility for unpacking a DHIS2 WAR
../IDSR/                  weekly IDSR surveillance seeder and population seeder (write into this DHIS2)
```

## Bring-up

Normally started by `bundle/scripts/sources-up.sh up dhis2`, which:

1. starts the compose stack (first boot runs the Liquibase schema init, 5 to 10 minutes);
2. restores `sample-data/dhis2_seeded.pgdump` if one is present and the database is empty;
3. otherwise seeds through the API with `scripts/seed_ethiopia.py` (idempotent; deterministic UIDs).

Manual equivalent:

```bash
docker compose -f 0_external/sources/DHIS2/docker-compose.yml up -d
curl -s http://127.0.0.1:8080/api/ping            # "pong" once migrations finish
python3 0_external/sources/DHIS2/scripts/seed_ethiopia.py
```

Regenerate DHIS2 analytics after a restore if DHIS2's own charts are needed (the platform's pipeline does not depend on them):

```bash
curl -u admin:<password> -X POST http://127.0.0.1:8080/api/resourceTables/analytics
```

## What the generated data contains

- Org units, data elements and programmes come from the shared demonstration entity model (`bundle/seeds/entity-model`).
- HMIS values are drawn around plausible coverage midpoints with persistent per-facility reporting behaviour, so completeness and timeliness vary realistically.
- IDSR weekly cases run to 2026-W27 and include a fixed set of injected outbreaks; case counts are coupled to the generated climate series with a lag (`../IDSR/seed_idsr_dhis2.py`). Analyses that "find" these outbreaks or the rainfall relationship are recovering signals the generator placed there.
- Woreda populations are derived deterministically (`../IDSR/seed_population_dhis2.py`) and are not census figures.

See `docs/data-provenance.md` for how this source reaches the warehouse.

## Notes

- Ports 8080 (web) and 5433 (database) are published so in-cluster pods can reach the replica through `host.k3d.internal`. Bind them to a private interface on any shared host.
- `config/dhis.conf` and the compose defaults carry replica-only demo credentials; change them for any non-local use.
