# Admin console browser regressions

Playwright walkthroughs that run against a deployed admin console. Each test
signs in through Keycloak (privileged-ops realm), walks the pages a given role
sees, and fails on any page error.

| Test | Covers |
|---|---|
| `test_steward_console.mjs` | R-STEW (`steward.ops`): the nav is narrowed to the steward's grants (no operator pages without a granted widget), the MDM queue with its entity toggle and decision-timing copy, the master-detail panel (members, golden attributes, identifiers), and Column Governance with both tabs. |
| `test_dq_incidents.mjs` | R-SUPER (`root`): the Quality & Lineage page renders the incidents section and governance KPIs from detector findings, including the stale-ingest flags and the MDM backlog flag. |
| `test_access_provisioning.cjs` | An R-OPS-ADM operator approves an access request end to end: public submit, admin review, approve and provision, then the Keycloak account, role, data scope and set-password email (Mailpit). Backend outcomes are checked inside the cluster with `kubectl exec`, so run it on the host. |

## Running

The tests need Playwright and network access to the deployment.

```bash
docker run --rm --network host -v "$PWD/6_access-points/dashboards/admin/tests:/t" \
  mcr.microsoft.com/playwright:v1.49.0-jammy bash -c \
  'cd /tmp && npm init -y >/dev/null && npm i --silent playwright@1.49.0 >/dev/null \
   && cp /t/test_steward_console.mjs . && SEED_PASSWORD=... node test_steward_console.mjs'
```

`test_access_provisioning.cjs` documents its own invocation
(`NODE_PATH=<playwright-install>/node_modules node tests/test_access_provisioning.cjs`).

## Configuration

| Variable | Used by | Meaning |
|---|---|---|
| `ADMIN_BASE` | the two `.mjs` tests | Admin console URL (default `https://admin.127.0.0.1.nip.io`). |
| `SEED_PASSWORD` | all tests | Password of the seeded test accounts. Required; there is no default. |
