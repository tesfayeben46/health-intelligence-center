# EthioEMR sample data

This directory holds the restore artifact for the EthioEMR replica. It is excluded from the repository and distributed separately (see `SEED-DATA-NOTICE.md`).

| File | Content |
|---|---|
| `clean_openmrs.sql` | MySQL 8.0 dump of an OpenMRS database: schema (core, Bahmni-family and FHIR2 module tables), concept dictionary and module configuration. It contains no patient records. |

All patients, visits, encounters, observations and orders in the replica are generated afterwards by `../scripts/synthesize.py`. A dump written back by `bundle/scripts/pack.sh` also contains those generated rows, stamped in `openmrs.synth_meta`.

`../scripts/restore.sh` reads the file from here (a symlink to it also works) and imports it into `ethioemr-mysql`.
