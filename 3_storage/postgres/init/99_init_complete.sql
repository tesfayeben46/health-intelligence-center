-- The initdb sentinel. Must sort last.
--
-- Postgres runs /docker-entrypoint-initdb.d once, on an empty volume, and a
-- failing script aborts the sequence; the container then restarts onto the
-- now-existing volume and never runs the remaining scripts. The database
-- answers readiness probes while missing every schema from the failure point
-- on (for example, an empty SEAWEEDFS_FILER_DB_PASSWORD stops the sequence at
-- 23 and nothing later runs).
--
-- This table exists only if every script before it succeeded. The boot's
-- wait-for-postgres step requires it, so "ready" means fully initialized,
-- and a failed init is reported at the wait, naming this sentinel, instead of
-- surfacing as a random missing-schema error downstream.
\connect nhdw_vault

CREATE TABLE IF NOT EXISTS public.initdb_sentinel (
    completed_at timestamptz NOT NULL DEFAULT now(),
    scripts_note text NOT NULL
);
DELETE FROM public.initdb_sentinel;
INSERT INTO public.initdb_sentinel (scripts_note)
VALUES ('every /docker-entrypoint-initdb.d script before 99 completed');
