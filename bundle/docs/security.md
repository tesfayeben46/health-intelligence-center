# Security and identity

Keycloak handles authentication. OPA makes the authorization decisions, and the gateway enforces them.

## Identity

Keycloak runs two staff realms, one for ministry staff and analysts and one for privileged operators, plus a mock partner identity provider (EHIA) for federation. A stand-in for the Fayda national ID service provides citizen verification on the public portal.

Staff sign in with a password. TOTP enrolment is set per user in the security seed (`mfa: true`); the reference seed enables it for the security officer. All dashboards and analyst tools authenticate against the same Keycloak, so one session covers all of them.

Single sign-on depends on one host base being used consistently by the token issuer, each dashboard's redirect URIs, the gateway's accepted issuers and the seeded Keycloak clients. Moving the system to another domain means changing that host base.

## Authorization

Access is granted through roles. A role carries a geography scope (national or a region) and a programme scope (all, or a programme such as HIV or malaria).

When a dashboard requests mart data, the gateway builds a decision request from the caller's role (subject, data class, granularity, verb) and sends it to OPA. OPA returns allow or deny and, on allow, a row filter that the gateway applies before running the query. A regional officer sees only their region and a programme lead only their programme, and the UI cannot widen either: the filter is applied server-side.

Dashboards are assembled from a widget catalogue, and each role is granted a set of widgets and pages. Widget grants and OPA policies are kept consistent, so a role is not shown a widget whose data it cannot read.

## Column masking

Sensitive columns are classified in `ops_perm.column_classification`. A column without an entry is visible. Each entry has a sensitivity tier and a masking treatment:

- drop the column;
- hash it;
- show only the last characters (phone numbers);
- reduce precision (GPS coordinates to about 11 km, dates to the month);
- replace it with a stable pseudonym, so joins still work.

The policy decides who sees a classified column in clear. Each role has a reveal set (`column_access.reveal` in `bundle/roles/*.yaml`, compiled into the OPA policy). The gateway sends the queried relation's classified columns with every decision request, and OPA returns the ones the caller may not see. Analysts and decision makers reveal `restricted` (their maps use exact facility coordinates), the security officer also reveals `privileged`, and a low-trust federated login reveals nothing.

Masking applies on every read path: mart reads, widgets, the Supply Control Tower feed, exports, reports and ad-hoc SQL. In ad-hoc SQL, an explicit reference to a masked column is refused, because aliasing would defeat name-based masking; `SELECT *` returns the masked form. Superset connects to Trino with its own service identity, so gateway policy does not apply to Superset queries.

Masking fails closed. An unknown masking class drops the column, a missing HMAC key drops the column, and the gateway refuses to start if the live policy's reveal behaviour differs from the expected matrix. The classification store has three writers (the committed seed, data stewards and the OpenMetadata sync); each owns its own rows (`managed_by`) and cannot overwrite the others'.

## Tool access

Analyst and operator tools (dbt docs, Airflow, Superset, MLflow, Jupyter, Prometheus, the Kubernetes console) are opened from the dashboards using the shared Keycloak session and are restricted to the roles that need them. Tools with OIDC support check the role themselves. The others sit behind a role-aware reverse proxy (`6_access-points/api/tool-gate`), which also hides the launch button from users without the role. Opening a tool from a signed-in dashboard signs the user in without a prompt; a direct visit goes through the Keycloak login.

## Secrets

The repository contains no secret values. Templates hold placeholder tokens, and `bundle/scripts/gen-secrets.sh` fills them per deployment into a gitignored secrets file. Values supplied through the environment, or already present in an existing secrets file, are kept. The demo user password (`SEED_PASSWORD`) is generated when not supplied.
