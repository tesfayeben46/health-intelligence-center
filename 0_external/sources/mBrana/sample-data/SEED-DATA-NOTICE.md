# mBrana restore artifact

| File | Required | Content |
|---|---|---|
| `HCMISWCloud.bak` | Yes | SQL Server 2025 backup carrying the HCMISWCloud (mBrana) schema |

The file is excluded from the repository and distributed separately. Without it `../scripts/restore.sh` stops and the database does not exist, so the pipeline has nothing to ingest from mBrana. Place the file in this directory; `bundle/scripts/sources-up.sh` then restores it into the shared HCMIS WDM SQL Server and runs `../scripts/synthesize.py`, which generates every row.

A backup written back by `bundle/scripts/pack.sh` also contains previously generated rows, stamped with the synthesizer version. It contains no operational data. See `docs/data-provenance.md`.
