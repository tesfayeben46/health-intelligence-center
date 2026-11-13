# R-OPS-SEC: Security / Compliance Officer.
#
# Full audit control. Read-only on policies (can review but not modify
# alone; the two-person rule applies to changes). Approves break-glass
# requests (writes a break-glass-grant lease).

# Audit devices: full control; mutation requires sudo.
path "sys/audit" {
  capabilities = ["read", "list", "sudo"]
}
path "sys/audit/*" {
  capabilities = ["create", "read", "update", "delete", "sudo"]
}

# Policies: read-only review.
path "sys/policies/acl/*" {
  capabilities = ["read", "list"]
}
path "sys/policies/acl" {
  capabilities = ["list"]
}

# Break-glass grant path: writes a time-limited token granting one-time
# PHI access to an R-OPS-ENG. The grant itself is a kv entry that
# audit-traces who approved what + a wrapped token reference.
path "secret/data/break-glass/*" {
  capabilities = ["create", "read", "update", "list"]
}
path "secret/metadata/break-glass/*" {
  capabilities = ["read", "list"]
}

# Approve auth attempts that needed dual-control.
path "sys/control-group/authorize" {
  capabilities = ["create", "update"]
}

# Cannot read data-plane secrets (separation of duties).
path "secret/data/phi/*" {
  capabilities = ["deny"]
}
path "database/creds/*" {
  capabilities = ["deny"]
}

# Self-token introspection.
path "auth/token/lookup-self" {
  capabilities = ["read"]
}
path "auth/token/renew-self" {
  capabilities = ["update"]
}
