-- API tokens (Personal Access Tokens) for programmatic data access.
--
-- A PAT lets a user (or service account) call the nhdw-gateway directly
-- without a Keycloak login round-trip: notebooks, Power BI, automated
-- report generators, dataset exports.
--
-- The token format is:   nhdw_pat_<8charprefix>_<48charsecret>
--   - prefix: stored in clear so the admin UI can list tokens
--   - secret: stored as sha256(secret) only; irretrievable once issued
--
-- A token's effective permission set is intersected with the owner's
-- 4-axis grants: a PAT can be narrower than the owner but never wider. The intersection is computed at request time so revoking the
-- owner's role immediately narrows every PAT they hold.

\connect nhdw_ops

CREATE TABLE IF NOT EXISTS ops_perm.api_token (
    id                serial      PRIMARY KEY,
    owner_kc_sub      uuid        REFERENCES ops_identity."user"(kc_sub) ON DELETE CASCADE,
    -- For service-account tokens: owner_kc_sub is null and role_code is set.
    -- The token's role is the SA's role; no intersection (the SA already
    -- exists as a Keycloak service-account user).
    role_code         text        REFERENCES ops_perm.role(code) ON DELETE RESTRICT,
    name              text        NOT NULL,                   -- human label, e.g. "powerbi-finance-2026"
    prefix            text        NOT NULL UNIQUE,            -- 8-char public part
    secret_hash       text        NOT NULL,                   -- sha256 of the secret
    scope             jsonb,                                  -- {dashboards:[],widgets:[],filters:{},marts:[]}; null = full owner perms
    rate_limit_per_min int        NOT NULL DEFAULT 120,
    created_at        timestamptz NOT NULL DEFAULT now(),
    created_by_kc_sub uuid        REFERENCES ops_identity."user"(kc_sub),
    expires_at        timestamptz,
    last_used_at      timestamptz,
    revoked_at        timestamptz,
    revoke_reason     text,
    CONSTRAINT api_token_owner_xor_role CHECK (
        (owner_kc_sub IS NOT NULL) OR (role_code IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS api_token_owner_idx ON ops_perm.api_token (owner_kc_sub) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS api_token_prefix_idx ON ops_perm.api_token (prefix);

-- Per-request audit for token-attributed queries ("what did this PAT do
-- last week"). Truncated on rotation; Loki holds the long-term HTTP audit.
CREATE TABLE IF NOT EXISTS ops_perm.api_token_audit (
    id           bigserial   PRIMARY KEY,
    token_id     int         NOT NULL REFERENCES ops_perm.api_token(id) ON DELETE CASCADE,
    ts           timestamptz NOT NULL DEFAULT now(),
    method       text        NOT NULL,
    path         text        NOT NULL,
    status_code  int         NOT NULL,
    bytes_out    int,
    duration_ms  int,
    client_ip    inet,
    user_agent   text
);
CREATE INDEX IF NOT EXISTS api_token_audit_token_idx ON ops_perm.api_token_audit (token_id, ts DESC);

-- Helper view: tokens with derived "active" flag and a redacted display value.
CREATE OR REPLACE VIEW ops_perm.v_api_token AS
SELECT
    t.id, t.owner_kc_sub, t.role_code, t.name, t.prefix,
    t.scope, t.rate_limit_per_min,
    t.created_at, t.expires_at, t.last_used_at,
    t.revoked_at, t.revoke_reason,
    (t.revoked_at IS NULL AND (t.expires_at IS NULL OR t.expires_at > now())) AS is_active,
    -- Public display "nhdw_pat_<prefix>_----"; never includes the secret hash.
    'nhdw_pat_' || t.prefix || '_------------------------------------------------' AS display
FROM ops_perm.api_token t;

GRANT SELECT ON ops_perm.v_api_token TO ops_gateway;
GRANT SELECT, INSERT, UPDATE ON ops_perm.api_token, ops_perm.api_token_audit TO ops_gateway;
GRANT USAGE ON SEQUENCE ops_perm.api_token_id_seq, ops_perm.api_token_audit_id_seq TO ops_gateway;
