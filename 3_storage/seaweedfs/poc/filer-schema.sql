-- SeaweedFS filer metadata schema for the PostgreSQL store.
--
-- Two constraints:
--
-- 1. The table is not auto-created. The filer fails on first start with
--    'relation "filemeta" does not exist' unless this has been applied, so a
--    deployment needs it as an init step (3_storage/postgres/init/
--    23_seaweedfs_filer_db.sh).
--
-- 2. COLLATE "C" on `name` and `directory` is required. Under a locale
--    collation such as en_US.utf8 the filer warns that S3 list order is not
--    byte-lexicographic and falls back to a slower sort. S3 clients that merge
--    sorted listings can then report spurious differences, which surface as
--    phantom data drift in a pipeline instead of as an error.
CREATE TABLE IF NOT EXISTS filemeta (
    dirhash   BIGINT,
    name      VARCHAR(65535) COLLATE "C",
    directory VARCHAR(65535) COLLATE "C",
    meta      bytea,
    PRIMARY KEY (dirhash, name)
);
