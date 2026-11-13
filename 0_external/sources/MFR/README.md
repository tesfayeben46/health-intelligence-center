# MFR source replica

Stands in for Ethiopia's Master Facility Registry, which exposes facilities as FHIR R4 `Organization` resources. The replica is a **HAPI FHIR R4 JPA server** (`hapiproject/hapi:v8.0.0`) on an H2 store. The MFR ingestor (`2_pipelines/ingestor/MFR`) pulls `/fhir/Organization` as it would from the real registry.

The data is demonstration data from the shared entity model (`bundle/seeds/entity-model/_build/`): the facility list is a sample of the public facility list plus generated top-ups, and staffing figures are derived deterministically. See `docs/data-provenance.md`.

## Layout

```
docker-compose.yml          HAPI FHIR R4 (container nhdw-mfr-fhir), port 8093
.env.example                MFR_FHIR_BASE, MFR_DB_PASSWORD
scripts/wait-for-ready.sh   blocks until /fhir/metadata answers
scripts/seed.py             posts every Organization in transaction bundles (idempotent)
scripts/verify.sh           counts by Organization type and a sample
```

## Usage

```bash
cd 0_external/sources/MFR
cp .env.example .env
docker compose up -d
bash scripts/wait-for-ready.sh
python3 scripts/seed.py
bash scripts/verify.sh
```

`bundle/scripts/sources-up.sh up mfr` runs the same steps. A fresh clone seeds this FHIR replica with no external artifact. The warehouse's MFR data does not come from this replica, though; see the note below.

## Resource model

`seed.py` creates one Organization per country, region, zone, woreda and facility in the entity model (14 regions, 137 zones, 617 woredas and 893 facilities in the Ethiopian sample), linked by `partOf` (facility -> woreda -> zone -> region -> country; a woreda without a matching zone, or a facility without a matching woreda, points to its region). Each level has its own identifier system:

| Level | System URI | Example value |
|---|---|---|
| Country | `https://mfr.moh.gov.et/country-id` | `ET` |
| Region | `https://mfr.moh.gov.et/region-id` | `ET-OR` |
| Zone | `https://mfr.moh.gov.et/zone-id` | `ZON-OR-…` |
| Woreda | `https://mfr.moh.gov.et/woreda-id` | `WOR-OR-…` |
| Facility | `https://mfr.moh.gov.et/facility-id` | `FAC-OR-HC-…` |

Facilities also carry:

- `https://mfr.moh.gov.et/upstream-id` (secondary identifier) when the sample row has an MFR id;
- an `ownership` extension (`public`, `private`, `ngo`, `military`);
- a `staffing` extension with physician, nurse and HEW counts, generated from the facility id and tier.

Bundles use conditional create (`ifNoneExist`) and conditional `partOf` references, so re-running is safe.

## Notes

- The H2 store and Lucene index live in named volumes. Do not run `docker compose down -v`: re-seeding regenerates the server-assigned `fhir_id`, which is the vault hub business key, and orphans the vault.
- The warehouse's facility capacity, workforce and equipment marts read the `nhdw_landing.stg_mfr__*` tables, which `bundle/scripts/seed-realistic.sh` loads directly from seed CSVs in `bundle/seeds/sources/mfr/`. Those CSVs are distributed separately; without them the MFR marts are empty. The MFR ingestor's output is not used by the vault (see `docs/data-provenance.md`, Direct loads).
- The container runs as root because named volumes default to root ownership; change this before exposing the server.
- The port is published on `0.0.0.0:8093` so k3d pods reach it through `host.k3d.internal`.
