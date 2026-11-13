# bundle/docs: served operator docs

Copies of the operator-facing documents shown on the admin console's Documentation page. `bundle/scripts/seed-docs.sh` publishes them to the `nhdw-docs` bucket at boot; keeping them inside the bundle makes the bundle self-contained.

Edit the originals in the top-level `docs/` folder and run `bundle/scripts/sync-docs.sh` (also run by `pack.sh`). The served set is defined in `seed-docs.sh` (`DOC_ROWS`) and `sync-docs.sh` (`SERVED`).
