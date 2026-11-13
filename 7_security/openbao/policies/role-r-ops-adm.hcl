# R-OPS-ADM: System Administrator.
#
# Manages OpenBao itself: mounts, auth methods, policies. Policies that grant
# PHI access need R-OPS-SEC co-signature (two-person rule, enforced outside
# this policy).

# Full sys/* except audit (audit modification is reserved for R-OPS-SEC).
path "sys/mounts/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "sys/auth/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "sys/policies/acl/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}
path "sys/policies/acl" {
  capabilities = ["list"]
}
path "sys/health" {
  capabilities = ["read"]
}
path "sys/seal-status" {
  capabilities = ["read"]
}

# Audit devices: read-only. Mutation reserved for R-OPS-SEC.
path "sys/audit" {
  capabilities = ["read", "sudo"]
}

# Cannot read data-plane secrets. Cannot use database creds.
# These deny paths are explicit for clarity.
path "secret/data/phi/*" {
  capabilities = ["deny"]
}
path "database/creds/*-reader" {
  capabilities = ["deny"]
}

# Self-token introspection.
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}
