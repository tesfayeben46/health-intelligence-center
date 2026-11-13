#!/usr/bin/env bash
# gen-tls.sh: issue a locally trusted wildcard certificate for *.$HOST_BASE
# (default *.127.0.0.1.nip.io) so browsers accept the ingress hostnames.
#
# The CA (bundle/tls/rootCA.pem) is generated once and reused, so it is imported
# into the browser or OS trust store only once (see the printed hint). The leaf
# certificate is reissued on each run and loaded into the TLS secret
# nhdw-wildcard-tls, which every ingress references. Idempotent.
#
# Usage: bundle/scripts/gen-tls.sh [--namespace nhdw]
#   HOST_BASE=<host>   wildcard host base for the cert (default 127.0.0.1.nip.io).
#                      The deploy role passes the inventory's nhdw_host_base so the
#                      certificate matches the ingress hostnames on a public host.
set -euo pipefail
NS="nhdw"
HOST_BASE="${HOST_BASE:-127.0.0.1.nip.io}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CERTDIR="$REPO/bundle/tls"
mkdir -p "$CERTDIR"

if [ ! -f "$CERTDIR/rootCA.pem" ]; then
  openssl genrsa -out "$CERTDIR/rootCA.key" 4096 2>/dev/null
  openssl req -x509 -new -nodes -key "$CERTDIR/rootCA.key" -sha256 -days 3650 \
    -out "$CERTDIR/rootCA.pem" -subj "/CN=NHDW Local Dev CA/O=NHDW Demo" 2>/dev/null
  echo "  ok: created local CA: import ONCE: $CERTDIR/rootCA.pem"
fi

cat > "$CERTDIR/wild.cnf" <<CNF
[req]
distinguished_name = dn
[dn]
[ext]
subjectAltName = @alt
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
[alt]
DNS.1 = *.${HOST_BASE}
DNS.2 = ${HOST_BASE}
CNF
openssl genrsa -out "$CERTDIR/wild.key" 2048 2>/dev/null
openssl req -new -key "$CERTDIR/wild.key" -out "$CERTDIR/wild.csr" \
  -subj "/CN=*.${HOST_BASE}" 2>/dev/null
openssl x509 -req -in "$CERTDIR/wild.csr" -CA "$CERTDIR/rootCA.pem" -CAkey "$CERTDIR/rootCA.key" \
  -CAcreateserial -out "$CERTDIR/wild.crt" -days 825 -sha256 \
  -extensions ext -extfile "$CERTDIR/wild.cnf" 2>/dev/null

# On a fresh cluster this runs before the kustomize apply that creates the
# namespace. Create it here so the secret apply cannot fail with NotFound, which
# would leave ingresses silently serving Traefik's default certificate.
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n "$NS" create secret tls nhdw-wildcard-tls \
  --cert="$CERTDIR/wild.crt" --key="$CERTDIR/wild.key" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
echo "  ok: nhdw-wildcard-tls loaded: ingresses now serve the trusted wildcard cert"
echo "  -> one-time browser trust: import bundle/tls/rootCA.pem into your OS/browser trusted-root store"
