# eLMIS restore artifact

| File | Required | Content |
|---|---|---|
| `elmis_historical_data_simulated.sql.gz` | No | Plain `pg_dump`, 236 MB gzipped (about 1.5 GB plain): a pattern-preserving mutation of a historical Zambian eLMIS extract |

The file is excluded from the repository and distributed separately. It is optional. When present, `../scripts/restore.sh` streams it into `elmis-postgres`. When absent, `bundle/scripts/sources-up.sh` runs `../scripts/synthesize.sh`, which generates a Zambian supply history deterministically from committed files (the Zambian entity model `bundle/seeds/entity-model/entity-model.zm.yaml` and the facility registry `facilities_zm_elmis.json`) into the same tables. The ingestor, vault and marts run unchanged on either.

The extract is derived from an operational Zambian eLMIS extract by a pattern-preserving mutation (`../provenance-manifest-summary.json`): values are altered while their statistical patterns are kept, so it is not the operational data itself. The generator makes the source reproducible from a fresh clone and contains no operational records. The facility registry both use (`facilities_zm_elmis.json`) is real reference data; see `NOTICE.md` at the repository root. See `../README.md` and `docs/data-provenance.md`.
