-- CMS store for the public dashboard's articles and newsroom (PHEM bulletins,
-- FMoH press releases, public-health advisories).
--
-- The public SPA reads these anonymously through the gateway's
-- /api/public/articles route (R-PUB-ANON, no Bearer), the same public_router
-- pattern as ops_governance.access_request. Content is seeded
-- from bundle/seed-pack/app/articles/*.yaml by the security-seed Job (idempotent
-- upsert, managed_by='seed'); runtime edits (managed_by='runtime') survive a
-- pack because pg_dump of nhdw_ops captures the table.
--
-- body_html is trusted, curated HTML rendered in the SPA via
-- dangerouslySetInnerHTML: authored content, never visitor input.
-- linked_widget optionally ties a card to a real public bi_public signal
-- (e.g. pub-region) so the newsroom is data-connected, not a pure CMS.

\connect nhdw_ops

CREATE TABLE IF NOT EXISTS ops_governance.public_article (
    id            text        PRIMARY KEY,                 -- stable slug id, e.g. a-001
    slug          text        NOT NULL UNIQUE,
    category      text        NOT NULL,                    -- PHEM | Press | Advisory | Programme | Research
    author        text        NOT NULL,
    title         text        NOT NULL,
    summary       text        NOT NULL,
    body_html     text        NOT NULL,                    -- curated HTML (dangerouslySetInnerHTML)
    tags          text[]      NOT NULL DEFAULT '{}',
    published_at  date        NOT NULL,
    linked_widget text,                                    -- optional bi_public signal (e.g. pub-region)
    managed_by    text        NOT NULL DEFAULT 'seed',     -- 'seed' | 'runtime' (reconciliation provenance)
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT public_article_category_chk
        CHECK (category IN ('PHEM','Press','Advisory','Programme','Research'))
);

CREATE INDEX IF NOT EXISTS ix_public_article_published
    ON ops_governance.public_article (published_at DESC);
CREATE INDEX IF NOT EXISTS ix_public_article_category
    ON ops_governance.public_article (category);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT ON ops_governance.public_article TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_governance.public_article TO ops_writer;
    END IF;
END $$;
