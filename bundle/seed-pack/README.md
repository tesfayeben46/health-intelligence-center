# Seed pack

The application data the platform is initialised with, kept separate from the code that consumes it. Everything here is sample or demonstration content (see [`docs/data-provenance.md`](../../docs/data-provenance.md)): fictional users, illustrative articles, generated programme figures, and ML packages that train on the demonstration data.

The principle is that schema and loaders belong to the system and data belongs to the pack: the numbered DDL and migrations stay in `3_storage/postgres/init/`, the loaders in `7_security/scripts/` and `2_pipelines/`, and only the data they consume lives here. Source-system data is not in the pack; the source replicas seed themselves (see `bundle/scripts/sources-up.sh` and `0_external/README.md`).

## Layout

```
seed-pack/
├── manifest.yaml          what is seeded, in what order, and which system versions the pack targets
└── app/
    ├── security/          permission model, identity sources and sample users (see security/README.md)
    ├── articles/          public-dashboard newsroom articles (illustrative sample content)
    ├── program-demo/      synthetic per-programme figures for the facility-detail BI components
    ├── workshop-groups/   workshop group rosters and invite/request samples (shell + SQL)
    ├── ml/                platform ML packages, trained at boot, and their deployment rows
    └── project-review/    three sample community projects used by the graduation seed
```

## How it is loaded

`bundle/scripts/bootstrap.sh` runs the seeders in order; each reads its part of this directory:

| Part | Seeder | Target |
|---|---|---|
| `app/security/` | `seed-app-security.sh` (via `7_security/scripts/load_security_seed.py`) | `ops_perm`, `ops_identity` |
| `app/articles/` | `seed-app-security.sh` (via `7_security/scripts/load_public_articles.py`) | `ops_governance.public_article` |
| `app/program-demo/` | `seed-app-security.sh` | `nhdw_vault` (`bi_facility` tables) |
| `app/workshop-groups/` | `seed-app-security.sh` | `nhdw_ops` workshop tables |
| `app/ml/` | `seed-ml.sh`, then `seed-model-projects.sh` | MLflow registry, `ops_ml`, workshop projects |
| `app/project-review/` | `seed-graduation.sh` | workshop projects and review records, through the gateway API |

Every seeder is idempotent and convergent, so re-running bootstrap does not duplicate data.

## Manifest and compatibility

`manifest.yaml` conforms to [`../scripts/seed-pack-engine/manifest.schema.json`](../scripts/seed-pack-engine/manifest.schema.json). It declares the pack version (`seed_pack_version`), the compatible system range (`targets_system`) and an ordered list of entries, each with a `kind` (`pg-dump`, `mysql-dump`, `sqlserver-bak`, `sql`, `csv`, `loader`, `kcadm`, `api`, `mc`). Some entries name a target layout (`sources/*`, `app/keycloak`, `app/projects`, ...) whose data currently lives with the corresponding seeder rather than in this directory.

`seed-app-security.sh` refuses to seed when the pack's `targets_system` excludes the version in the repository's `VERSION` file. To check a pack by hand:

```bash
python3 bundle/scripts/seed-pack-engine/validate.py bundle/seed-pack/manifest.yaml \
        --system-version "$(cat VERSION)"
```

Exit 0 means the manifest is structurally valid and compatible. `bundle/scripts/seed-pack-engine/engine.py` reads the same manifest and prints (`--dry-run`) or runs the plan entry by entry; bootstrap does not use it.

## Changing seed data

1. Edit the data files.
2. Bump `seed_pack_version`, and adjust `targets_system` if the data depends on a schema change.
3. Re-run the matching seeder (or bootstrap). No application image rebuild is needed.
