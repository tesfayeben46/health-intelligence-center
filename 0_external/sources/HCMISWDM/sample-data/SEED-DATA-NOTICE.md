# HCMIS WDM restore artifact

| File | Required | Content |
|---|---|---|
| `HCMISWDM.bak` | Yes | SQL Server 2025 backup carrying the HCMIS WDM schema |

The file is excluded from the repository and distributed separately. Without it `../scripts/restore.sh` stops and the database does not exist, so the pipeline has nothing to ingest from HCMIS WDM and the supply dashboards show no warehouse data. Place the file in this directory; `bundle/scripts/sources-up.sh` then restores it and runs the three synthesizers in `../scripts/`, which generate every row.

A backup written back by `bundle/scripts/pack.sh` also contains previously generated rows, stamped with the synthesizer versions. It contains no operational EPSS data. See `docs/data-provenance.md`.
