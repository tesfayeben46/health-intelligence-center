# Role data permissions

Each `r-*.yaml` file defines one role's data permissions: which subjects, data classes, granularities, sensitivities and verbs the role may use, and which sensitivity tiers its columns reveal unmasked. These files are the source of the platform's OPA data policy.

## How they are used

1. `bundle/scripts/seed-app-security.sh` mounts the files into the security-seed Job, and `7_security/scripts/load_security_seed.py` normalises the `permissions` tuples into `ops_perm.permission` and `ops_perm.role_permission` (a tuple shared by several roles becomes one permission row with several links).
2. `bundle/scripts/regen-opa-policy.sh` (bootstrap step 7.1, and `ci-redeploy.sh` when these files or `7_security/opa/` change) runs `7_security/opa/scripts/compile.py --from-db`, which emits one `role_r_<id>.rego` per role, gates the result with `opa test`, and publishes it to the `opa-policies` ConfigMap. A failing test leaves the running OPA unchanged.
3. `compile.py` without `--from-db` compiles these YAML files directly into `7_security/opa/policies/`; the committed rego there is that output.

Role metadata (id, `phase_activated`, `intentional_deny`) and the `column_access.reveal` set are read from the YAML in both modes. Permissions edited through the permission-admin API live in the store and take effect at the next regeneration.

The role catalogue, capabilities and dashboard grants are separate: see `bundle/seed-pack/app/security/` (`roles.yaml`, `role_grants.yaml`).

## File shape

```yaml
role:
  id: R-DEC
  name: "Decision-Maker"
  group: DecisionMaker
  identity_provider: MoH-Keycloak
  mfa_required: true
  mfa_factor: TOTP
  phase_activated: 1
  audit_class: standard
  intentional_deny: false        # optional; true for deny-by-design roles
  column_access:
    reveal: [restricted]          # optional; sensitivity tiers shown unmasked
  permissions:
    - subject: PT                 # PT, HP, FAC, PRD, PRG, ...
      data_class: [DC.ENC]
      members: all
      granularity: deidentified   # raw | aggregate | deidentified | metadata
      sensitivity: []
      verbs: [read]
      purpose_of_use: [public-health]
```

## Roles

| File | Role | Group | Data access |
|---|---|---|---|
| `r-pub-anon.yaml` | R-PUB-ANON | Public | Aggregate programme and facility reads for the public dashboard |
| `r-prod-submit.yaml` | R-PROD-SUBMIT | Producer | Own-facility reads and submissions |
| `r-prod-clin.yaml` | R-PROD-CLIN | Producer | Deny-all on marts (data reached through a SMART-on-FHIR path) |
| `r-dec.yaml` | R-DEC | DecisionMaker | Scoped de-identified and aggregate reads; the scoped R-RHB-*, R-PHO-* and R-PD-* roles route here |
| `r-cons-ana.yaml` | R-CONS-ANA | Consumer | De-identified patient data and raw programme and facility marts |
| `r-cons-sci.yaml` | R-CONS-SCI | Consumer | Model-catalogue metadata only; data through the workshop sandbox |
| `r-cons-res.yaml` | R-CONS-RES | Consumer | Deny-all on internal marts |
| `r-cons-prt.yaml` | R-CONS-PRT | Consumer | Aggregate programme outcomes only |
| `r-stew.yaml` | R-STEW | Steward | Deny-all on data subjects (metadata work only) |
| `r-ops-eng.yaml` | R-OPS-ENG | Operator | Metadata on every subject |
| `r-ops-adm.yaml` | R-OPS-ADM | Operator | Data-quality class only |
| `r-ops-sec.yaml` | R-OPS-SEC | Operator | Incident investigation across sensitivity tiers |
| `r-super.yaml` | R-SUPER | Privileged | Universal access (break-glass; audited) |

## Changing a role

1. Edit the YAML.
2. Run `python3 7_security/opa/scripts/compile.py` and the OPA test suite (`7_security/opa/policies/authz_test.rego`) before committing.
3. Changes that touch patient-level or sensitive permissions should be reviewed by both a data steward and a security officer.

See [`docs/security.md`](../../docs/security.md) for the authorisation model.
