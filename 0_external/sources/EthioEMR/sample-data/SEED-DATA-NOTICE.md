# EthioEMR restore artifact

| File | Required | Content |
|---|---|---|
| `clean_openmrs.sql` | Yes | MySQL dump of an OpenMRS database: schema, concept dictionary and module configuration |

The file is excluded from the repository and distributed separately. Without it `../scripts/restore.sh` stops and the source stays empty, so the pipeline has nothing to ingest from EthioEMR and the dashboards show no EMR data. Place the file in this directory; `bundle/scripts/sources-up.sh` then restores it and runs `../scripts/synthesize.py`, which generates all patients, visits, encounters and observations.

A dump written back by `bundle/scripts/pack.sh` also contains previously generated rows, stamped with the synthesizer version. It contains no real patient data. See `docs/data-provenance.md`.
