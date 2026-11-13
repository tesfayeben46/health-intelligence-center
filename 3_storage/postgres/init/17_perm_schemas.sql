-- Permission model schema in the nhdw_ops database.
--
-- Adds:
--   1. Schema `ops_perm` holding the 4-axis control model
--      (dashboard / global-filter / widget / widget-filter access),
--      app capabilities, audit, and the mart_attrs catalog that the
--      gateway uses to enrich OPA input.
--   2. An `ops_identity.identity_source` table for federated identity
--      sources (Fayda / Google / SAML mock / direct), plus optional Fayda
--      verification columns on the existing access_request table.
--
-- Additive apart from in-place column upgrades (ADD COLUMN IF NOT EXISTS);
-- it drops no table. Idempotent (IF NOT EXISTS / ON CONFLICT DO NOTHING
-- throughout) and re-applied on every boot by seed-app-security.
--
-- The seed loader 7_security/scripts/load_security_seed.py reads YAML from
-- bundle/seed-pack/app/security/ and idempotently inserts catalog rows and
-- role grants. 18_security_seed.sh calls it.
--

\connect nhdw_ops

-- ops_perm schema
CREATE SCHEMA IF NOT EXISTS ops_perm;
COMMENT ON SCHEMA ops_perm IS
    '4-axis permission control model + app capabilities + permission audit. '
    'DB-side source of truth for dashboard/widget/filter visibility decisions; '
    'OPA stays for mart-level decisions only.';


-- Catalog tables (seeded from bundle/seed-pack/app/security/)

CREATE TABLE IF NOT EXISTS ops_perm.dashboard (
    id          text        PRIMARY KEY,
    title       text        NOT NULL,
    description text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE ops_perm.dashboard IS
    'Catalog of the 4 production dashboards (public, internal-bi, admin, workshop) '
    'plus the future demo control room. Seeded from dashboards.yaml.';


CREATE TABLE IF NOT EXISTS ops_perm.page (
    id            text        PRIMARY KEY,                   -- e.g. 'internal-bi/disease'
    dashboard_id  text        NOT NULL REFERENCES ops_perm.dashboard (id) ON DELETE CASCADE,
    title         text        NOT NULL,
    ordering      int         NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_page_dashboard ON ops_perm.page (dashboard_id, ordering);
COMMENT ON TABLE ops_perm.page IS
    'Pages within each dashboard. Filters and widget grants are page-scoped, '
    'not dashboard-scoped, because the same widget can be allowed on one '
    'page and denied on another.';


-- Sections are the layout layer between a page and its widgets: a full-width
-- titled band (title/eyebrow), stacked by `ordering`, inside which widgets flow
-- in a CSS grid (`columns` wide, `dense` = auto-flow so a permission-removed
-- widget's space is filled by its neighbours). Layout is data, not shell code:
-- adding or reordering a section or moving a widget is a seed change, no rebuild.
CREATE TABLE IF NOT EXISTS ops_perm.section (
    id           text        PRIMARY KEY,                  -- '<dashboard>/<page_slug>/<section_slug>'
    page_id      text        NOT NULL REFERENCES ops_perm.page (id) ON DELETE CASCADE,
    title        text        NOT NULL,
    eyebrow      text,                                     -- small uppercase kicker above the title
    description  text,                                     -- optional sub-line
    ordering     int         NOT NULL DEFAULT 0,           -- vertical stack order within the page
    columns      int         NOT NULL DEFAULT 12,          -- grid track count for widget layout
    dense        boolean     NOT NULL DEFAULT true,        -- grid-auto-flow: dense (reflow to fill gaps)
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_section_page ON ops_perm.section (page_id, ordering);
COMMENT ON TABLE ops_perm.section IS
    'Layout band within a page. Widgets are placed into a section (page_widget.section_id) '
    'at a col_span; a section whose granted widgets are all denied collapses.';


CREATE TABLE IF NOT EXISTS ops_perm.widget (
    id          text        PRIMARY KEY,                     -- e.g. 'hiv-cascade'
    title       text        NOT NULL,
    kind        text        NOT NULL CHECK (kind IN
                  ('chart','table','kpi','map','flow','funnel','heatmap','treemap','assistant','custom')),
    placement   text        NOT NULL DEFAULT 'page.section'  -- 'page.section' | 'chrome.fab' | 'chrome.sidebar' | 'chrome.topbar'
                            CHECK (placement IN ('page.section','chrome.fab','chrome.sidebar','chrome.topbar')),
    requires    jsonb,                                       -- {subject, verb, granularity, sensitivity}
                                                             -- only for mart-backed widgets; null = no OPA mart check
    description text,
    -- Plugin widgets: a 'plugin' widget is a reviewed community Web Component
    -- (not an in-tree src/widgets/<id> folder). The shell learns it is a plugin
    -- from `source`, loads its bundle (bundle_key -> object-store widget-bundles/,
    -- served same-origin by /api/plugins/{id}/bundle), mounts `component_type`
    -- (the custom-element tag), re-renders on `reacts_to` dims, and fetches its
    -- data via the generic declarative binding in `data_binding` (no per-widget
    -- server code). Core widgets leave these null / source='core'.
    source          text NOT NULL DEFAULT 'core',            -- 'core' | 'plugin'
    component_type  text,                                    -- plugin custom-element tag (null for core)
    reacts_to       jsonb,                                   -- ["geography","programme",…] named filter dims
    bundle_key      text,                                    -- object-store key of the approved JS bundle
    data_binding    jsonb,                                   -- declarative mart binding (see plugin_fetcher)
    created_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE ops_perm.widget IS
    'Catalog of every widget that can appear on any page. The `requires` jsonb '
    'is the OPA payload for mart-backed widgets; null for synthetic/static widgets. '
    'source=plugin rows are reviewed community Web Components (§4.5).';
-- Warm-init ALTERs (idempotent) so an existing widget table gains the plugin
-- columns, matching this file's page_widget ADD COLUMN IF NOT EXISTS precedent.
ALTER TABLE ops_perm.widget ADD COLUMN IF NOT EXISTS source         text NOT NULL DEFAULT 'core';
ALTER TABLE ops_perm.widget ADD COLUMN IF NOT EXISTS component_type text;
ALTER TABLE ops_perm.widget ADD COLUMN IF NOT EXISTS reacts_to      jsonb;
ALTER TABLE ops_perm.widget ADD COLUMN IF NOT EXISTS bundle_key     text;
ALTER TABLE ops_perm.widget ADD COLUMN IF NOT EXISTS data_binding   jsonb;
-- Guarded CHECK (added out-of-line so warm re-runs never error on a dup name).
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'widget_source_check') THEN
        ALTER TABLE ops_perm.widget
            ADD CONSTRAINT widget_source_check CHECK (source IN ('core','plugin'));
    END IF;
END $$;


-- Page<->widget placement catalog: which pages each widget may render on.
-- Backend source of truth for placement. role_widget grants are page-scoped;
-- the security-seed loader
-- expands a role's dashboard-level widget grant across the pages a widget is
-- cataloged on here. Chrome widgets (placement=chrome.*) are cataloged on every
-- page of their dashboard.
CREATE TABLE IF NOT EXISTS ops_perm.page_widget (
    page_id    text NOT NULL REFERENCES ops_perm.page (id) ON DELETE CASCADE,
    widget_id  text NOT NULL REFERENCES ops_perm.widget (id) ON DELETE CASCADE,
    section_id text REFERENCES ops_perm.section (id) ON DELETE SET NULL,  -- band on the page (null = page default band)
    ordering   int  NOT NULL DEFAULT 0,                                   -- order within the section
    col_span   int  NOT NULL DEFAULT 6,                                   -- grid tracks the widget spans
    row_span   int  NOT NULL DEFAULT 1,
    PRIMARY KEY (page_id, widget_id)
);
-- Warm-DB upgrade: add the section/span columns if the table pre-existed.
ALTER TABLE ops_perm.page_widget ADD COLUMN IF NOT EXISTS section_id text REFERENCES ops_perm.section (id) ON DELETE SET NULL;
ALTER TABLE ops_perm.page_widget ADD COLUMN IF NOT EXISTS col_span int NOT NULL DEFAULT 6;
ALTER TABLE ops_perm.page_widget ADD COLUMN IF NOT EXISTS row_span int NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS ix_page_widget_page ON ops_perm.page_widget (page_id, ordering);
CREATE INDEX IF NOT EXISTS ix_page_widget_section ON ops_perm.page_widget (section_id, ordering);
COMMENT ON TABLE ops_perm.page_widget IS
    'Placement catalog: which pages a widget may render on. role_widget grants '
    'are page-scoped; the loader expands dashboard-level grants across these pages.';


CREATE TABLE IF NOT EXISTS ops_perm.filter (
    id            text        PRIMARY KEY,                   -- 'region', 'period', 'programme', 'severity'
    title         text        NOT NULL,
    source        text        NOT NULL CHECK (source IN ('static','mart_query','ops_db_query')),
    source_ref    text,                                      -- 'marts.dim_geography' | 'ops_artifact.workshop_group' | null for static
    static_values jsonb,                                     -- only when source='static': ["2024","2025",...]
    created_at    timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE ops_perm.filter IS
    'Catalog of every filter (global or local). source enum tells the resolver '
    'where to fetch the canonical value set: a static enum, a live mart query, '
    'or an ops-DB query. The role''s allowed_values is intersected with this.';


-- Structural filter offerings (WHICH filters a page / widget offers)
-- Structure, not authorization: these say a page's global-filter bar offers
-- region/period/programme, or a widget offers artifact_tier/project,
-- independent of role. Which values a user may pick is authorization,
-- resolved separately as role_scope ∩ user_scope (ops_perm.role_scope holds
-- the per-role value caps).
CREATE TABLE IF NOT EXISTS ops_perm.page_filter (
    page_id   text NOT NULL REFERENCES ops_perm.page (id) ON DELETE CASCADE,
    filter_id text NOT NULL REFERENCES ops_perm.filter (id) ON DELETE CASCADE,
    ordering  int  NOT NULL DEFAULT 0,
    PRIMARY KEY (page_id, filter_id)
);
COMMENT ON TABLE ops_perm.page_filter IS
    'STRUCTURAL: which filters a page''s global-filter bar offers (role-independent). '
    'Value caps come from role_scope ∩ user_scope in the resolver (#50b).';

CREATE TABLE IF NOT EXISTS ops_perm.widget_filter (
    dashboard_id text NOT NULL REFERENCES ops_perm.dashboard (id) ON DELETE CASCADE,
    page_id      text NOT NULL REFERENCES ops_perm.page (id) ON DELETE CASCADE,
    widget_id    text NOT NULL REFERENCES ops_perm.widget (id) ON DELETE CASCADE,
    filter_id    text NOT NULL REFERENCES ops_perm.filter (id) ON DELETE CASCADE,
    ordering     int  NOT NULL DEFAULT 0,
    PRIMARY KEY (dashboard_id, page_id, widget_id, filter_id)
);
COMMENT ON TABLE ops_perm.widget_filter IS
    'STRUCTURAL: which filters a widget offers locally (role-independent). '
    'Value caps come from role_scope ∩ user_scope in the resolver (#50b).';


-- Runtime catalog overrides
-- Operator-level enable/disable and placement tweaks applied on top of the
-- reconciled catalog at resolve time. Runtime-only: the seed loader never
-- touches this table, so overrides survive every re-seed/reconcile.
-- Security property: an override can only subtract visibility (disable) or
-- re-layout (placement); it can never grant access the role model did not
-- already give (grants still gate first in the resolver).
-- Soft-orphan marker: a core widget that disappeared from the authored
-- catalog is stamped, not deleted; its role_widget grants and placements are
-- preserved so re-adding the widget restores everything. The resolver hides
-- orphaned widgets; the explicit admin purge (manage_catalog) hard-deletes them.
ALTER TABLE ops_perm.widget ADD COLUMN IF NOT EXISTS orphaned_at timestamptz;

CREATE TABLE IF NOT EXISTS ops_perm.catalog_override (
    kind       text        NOT NULL CHECK (kind IN
                             ('dashboard','page','section','widget','placement')),
    scope_id   text        NOT NULL,     -- the entity id; 'placement' uses '<page_id>::<widget_id>'
    enabled    boolean     NOT NULL DEFAULT true,
    overrides  jsonb,                    -- placement tweaks: {col_span,row_span,ordering,section_id}
    note       text,
    updated_by uuid        REFERENCES ops_identity.user (kc_sub),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, scope_id)
);
COMMENT ON TABLE ops_perm.catalog_override IS
    'Admin Dashboard-Control state: runtime enable/disable + placement '
    'overrides over the catalog. Never written by the seed loader; survives '
    're-seed. Subtract/re-layout only; never grants.';


-- mart_attrs: per-mart attributes the gateway adds to every OPA input. The
-- rego policies require data_class, granularity and sensitivity on each
-- input; without them OPA denies every request, and in advisory mode the
-- gateway would then allow everything while appearing to gate.
CREATE TABLE IF NOT EXISTS ops_perm.mart_attrs (
    schema_name         text        NOT NULL,
    table_name          text        NOT NULL,
    subject             text        NOT NULL CHECK (subject IN
                          ('PT','HP','FAC','PRD','PRG')),         -- OPA resource.subject
    data_class          text        NOT NULL CHECK (data_class IN
                          ('DC.ENC','DC.CND','DC.OBS','DC.PRO','DC.PRF','DC.STK','DC.MED','DC.AEFI')),
    granularity         text        NOT NULL CHECK (granularity IN ('raw','aggregate','deidentified')),
    default_sensitivity text        NOT NULL DEFAULT 'default',
    description         text,
    PRIMARY KEY (schema_name, table_name)
);
COMMENT ON TABLE ops_perm.mart_attrs IS
    'Catalog the gateway joins on every /api/marts/{schema}/{table} request to '
    'enrich the OPA input with data_class + granularity + default sensitivity. '
    'Without this, OPA decisions silently fail-open.';

-- column_classification: per-column sensitivity and masking policy (the
-- column-grain sibling of mart_attrs, which stays one row per mart). Keyed on
-- the deployed dbt relation names (the namespace Trino/information_schema and
-- the fetchers see), not mart_attrs' on-the-wire aliases, because masking is
-- applied against real result columns; a wrong namespace silently reveals.
-- Absence of a row = sensitivity 'default' (visible): this is a denylist of
-- columns needing protection, not an allowlist of viewable ones. Rows are
-- written by three authors, disambiguated by managed_by:
--   'seed'          bundle/seed-pack/app/security/column_classification.yaml
--   'steward'       the admin stewardship console (reserved)
--   'openmetadata'  the OpenMetadata classification sync, which must never
--                   overwrite 'seed' or 'steward' rows.
CREATE TABLE IF NOT EXISTS ops_perm.column_classification (
    schema_name    text    NOT NULL,
    table_name     text    NOT NULL,
    column_name    text    NOT NULL,
    sensitivity    text    NOT NULL DEFAULT 'restricted'
                     CHECK (sensitivity IN ('default','restricted','privileged')),
    mask_class     text    NOT NULL DEFAULT 'drop'
                     CHECK (mask_class IN ('none','drop','hash','partial','generalize','tokenize')),
    data_class     text,                    -- optional finer DC.* tag (free-form; fetchers use classes mart_attrs does not enumerate)
    pii            boolean NOT NULL DEFAULT false,
    purpose_unmask text[]  NOT NULL DEFAULT '{}',   -- purposes that may reveal it (PBAC growth path)
    managed_by     text    NOT NULL DEFAULT 'seed'
                     CHECK (managed_by IN ('seed','steward','openmetadata')),
    note           text,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (schema_name, table_name, column_name)
);
CREATE INDEX IF NOT EXISTS ix_column_classification_table
    ON ops_perm.column_classification (schema_name, table_name);
COMMENT ON TABLE ops_perm.column_classification IS
    'Per-column sensitivity + mask method on real dbt relation names. Feeds the '
    'OPA masking obligation; consumed by the gateway masking layer on every read path.';
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT ON ops_perm.column_classification TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        GRANT SELECT ON ops_perm.column_classification TO ops_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        -- the seed loader and the OpenMetadata classification sync write here
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_perm.column_classification TO ops_writer;
    END IF;
END $$;


-- Roles

CREATE TABLE IF NOT EXISTS ops_perm.role (
    id                serial      PRIMARY KEY,
    code              text        UNIQUE NOT NULL,           -- 'R-DEC', 'R-CONS-ANA', custom 'R-RHB-OROMIA'
    name              text        NOT NULL,
    description       text,
    combine_strategy  text        NOT NULL DEFAULT 'union'   -- when a user has multiple roles
                                  CHECK (combine_strategy IN ('union','intersection')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops_perm.role_capability (
    role_id         int  NOT NULL REFERENCES ops_perm.role (id) ON DELETE CASCADE,
    capability_code text NOT NULL,                            -- 'manage_groups', 'approve_grad', 'edit_roles', ...
    PRIMARY KEY (role_id, capability_code)
);
COMMENT ON TABLE ops_perm.role_capability IS
    'Orthogonal capability axis: app-level powers (create group, approve graduation, '
    'edit roles). Independent of the 4-axis data-access model.';


-- The 4-axis grants

-- A · Dashboard access
CREATE TABLE IF NOT EXISTS ops_perm.role_dashboard (
    role_id      int  NOT NULL REFERENCES ops_perm.role (id) ON DELETE CASCADE,
    dashboard_id text NOT NULL REFERENCES ops_perm.dashboard (id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, dashboard_id)
);

-- B · Global-filter access: no table here; value caps live in
--      ops_perm.role_scope and the offered filters in page_filter.

-- C · Widget access per page (a role's visible widgets on a specific page).
-- Page-scoped (dashboard->page->widget hierarchy): the same widget can be
-- granted on one page and denied on another. dashboard_id is kept denormalized
-- so existing dashboard-level queries (WHERE dashboard_id=..) still return the
-- UNION across pages (back-compat for callers that don't pass page_id). The
-- security-seed loader expands a role's dashboard-level widget grant across the
-- ops_perm.page_widget placements.
CREATE TABLE IF NOT EXISTS ops_perm.role_widget (
    role_id      int  NOT NULL REFERENCES ops_perm.role (id) ON DELETE CASCADE,
    dashboard_id text NOT NULL REFERENCES ops_perm.dashboard (id) ON DELETE CASCADE,
    page_id      text NOT NULL REFERENCES ops_perm.page (id) ON DELETE CASCADE,
    widget_id    text NOT NULL REFERENCES ops_perm.widget (id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, dashboard_id, page_id, widget_id)
);
CREATE INDEX IF NOT EXISTS ix_role_widget_dashboard ON ops_perm.role_widget (dashboard_id);
CREATE INDEX IF NOT EXISTS ix_role_widget_page ON ops_perm.role_widget (page_id);

-- D · Widget-filter access: no table here; value caps live in
--      ops_perm.role_scope and the offered filters in widget_filter.


-- User assignments
-- FKs to ops_identity.user (kc_sub uuid), created in 05_ops_schemas.sql.

CREATE TABLE IF NOT EXISTS ops_perm.user_role (
    kc_sub      uuid        NOT NULL REFERENCES ops_identity.user (kc_sub) ON DELETE CASCADE,
    role_id     int         NOT NULL REFERENCES ops_perm.role (id) ON DELETE CASCADE,
    source      text        NOT NULL CHECK (source IN ('jit_keycloak','admin_grant')),
    granted_at  timestamptz NOT NULL DEFAULT now(),
    granted_by  uuid        REFERENCES ops_identity.user (kc_sub),
    expires_at  timestamptz,
    is_active   boolean     NOT NULL DEFAULT true,
    PRIMARY KEY (kc_sub, role_id)
);
COMMENT ON TABLE ops_perm.user_role IS
    'Multi-role users. JIT rows (source=jit_keycloak) get upserted on every '
    'login from Keycloak realm-role claims; admin-grant rows (source=admin_grant) '
    'survive JIT refresh. The merge policy lives in the resolver.';


-- Audit + versioning

CREATE TABLE IF NOT EXISTS ops_perm.policy_revision (
    id          serial      PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    created_by  uuid        REFERENCES ops_identity.user (kc_sub),
    note        text,
    bundle_url  text                                          -- where the rego/data bundle for this revision was uploaded
);
COMMENT ON TABLE ops_perm.policy_revision IS
    'One row per coherent permission change batch. Lets us roll back to a prior '
    'snapshot + ties the corresponding MinIO OPA bundle to a revision id.';


CREATE TABLE IF NOT EXISTS ops_perm.audit_permission_change (
    id          bigserial   PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    actor_sub   uuid        REFERENCES ops_identity.user (kc_sub),
    revision_id int         REFERENCES ops_perm.policy_revision (id),
    table_name  text        NOT NULL,                         -- 'role_widget', 'role', ...
    op          text        NOT NULL CHECK (op IN ('insert','update','delete')),
    pk          jsonb       NOT NULL,                         -- composite-PK-safe row identifier
    before      jsonb,
    after       jsonb
);
CREATE INDEX IF NOT EXISTS ix_audit_permission_change_ts ON ops_perm.audit_permission_change (ts DESC);
CREATE INDEX IF NOT EXISTS ix_audit_permission_change_actor ON ops_perm.audit_permission_change (actor_sub, ts DESC);



-- ops_identity additions: identity sources

CREATE TABLE IF NOT EXISTS ops_identity.identity_source (
    id              serial      PRIMARY KEY,
    code            text        UNIQUE NOT NULL,             -- 'fayda', 'google', 'ehia-saml', 'moh-staff-direct'
    name            text        NOT NULL,
    kind            text        NOT NULL CHECK (kind IN ('direct','oidc','saml','social','fayda')),
    protocol        text,                                    -- 'oidc' | 'saml' | NULL for direct
    status          text        NOT NULL DEFAULT 'available' CHECK (status IN ('active','available','disabled')),
    config_json     jsonb,                                   -- entityID, endpoints, client_id, attribute mappings
    keycloak_alias  text,                                    -- the IdP alias inside the Keycloak realm
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE ops_identity.identity_source IS
    'Catalog of federated identity providers. Mirrors what Keycloak knows about; '
    'admin UI does CRUD here AND calls Keycloak Admin API to apply the change.';




-- access_request: Fayda verification columns (optional)
-- Set by the AccessRequestModal "Verify with Fayda" flow.

ALTER TABLE ops_governance.access_request
    ADD COLUMN IF NOT EXISTS fayda_id     text,
    ADD COLUMN IF NOT EXISTS verified_via text;              -- 'fayda' | 'google' | 'saml:ehia' | NULL


-- Bootstrap revision row 1 (for FK NOT NULL on later seed rows)

INSERT INTO ops_perm.policy_revision (id, note)
VALUES (1, 'initial seed load: schema bootstrap')
ON CONFLICT (id) DO NOTHING;

-- Align the sequence with the existing rows so nextval = max(id)+1. It must
-- be max-aware: this file is re-applied on every boot (seed-app-security), and
-- an unconditional setval(..., 1) would reset the sequence under live
-- revision rows and cause a duplicate key on the next admin write.
SELECT setval('ops_perm.policy_revision_id_seq',
              GREATEST((SELECT COALESCE(MAX(id), 1) FROM ops_perm.policy_revision), 1),
              true);


-- Grants for the gateway/reader/writer roles
-- ops_gateway, ops_writer, ops_reader are created in
-- 06_ops_users.sql.template. Without explicit grants on the new schema
-- the gateway gets 'permission denied for schema ops_perm' at runtime.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT USAGE ON SCHEMA ops_perm TO ops_gateway, ops_reader;
        GRANT SELECT ON ALL TABLES IN SCHEMA ops_perm TO ops_gateway, ops_reader;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ops_perm TO ops_gateway;
        ALTER DEFAULT PRIVILEGES IN SCHEMA ops_perm
            GRANT SELECT ON TABLES TO ops_gateway, ops_reader;
        ALTER DEFAULT PRIVILEGES IN SCHEMA ops_perm
            GRANT USAGE, SELECT ON SEQUENCES TO ops_gateway;
        -- The admin UI's CRUD endpoints need the gateway to INSERT/UPDATE/
        -- DELETE on the role tables and write audit rows.
        -- ops_perm.role_scope is not granted here: it is created (and granted)
        -- by 25_perm_scope_migration.sql, which runs after this file on a fresh
        -- initdb. Granting it here would reference a missing relation, roll
        -- back the DO block and abort initdb.
        GRANT INSERT, UPDATE, DELETE ON
            ops_perm.role, ops_perm.role_capability,
            ops_perm.role_dashboard, ops_perm.role_widget,
            ops_perm.user_role, ops_perm.catalog_override,
            ops_perm.policy_revision, ops_perm.audit_permission_change
            TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT USAGE ON SCHEMA ops_perm TO ops_writer;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ops_perm TO ops_writer;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ops_perm TO ops_writer;
        ALTER DEFAULT PRIVILEGES IN SCHEMA ops_perm
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ops_writer;
        ALTER DEFAULT PRIVILEGES IN SCHEMA ops_perm
            GRANT USAGE, SELECT ON SEQUENCES TO ops_writer;
    END IF;

    -- Also grant on the new ops_identity additions
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_gateway') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_identity.identity_source TO ops_gateway;
        GRANT SELECT ON ops_identity.identity_source TO ops_reader;
        -- New tables use serial PKs, so the gateway needs the sequences too
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ops_identity TO ops_gateway;
        ALTER DEFAULT PRIVILEGES IN SCHEMA ops_identity
            GRANT USAGE, SELECT ON SEQUENCES TO ops_gateway;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_writer') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON ops_identity.identity_source TO ops_writer;
    END IF;
END $$;
