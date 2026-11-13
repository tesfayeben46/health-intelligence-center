#!/bin/bash
# Issue a server certificate from the NHDW intermediate CA into a target
# directory ready for nginx / haproxy / a Java keystore.
#
# Idempotent. Run it on initial bring-up and again every <max_ttl - 7d> to
# rotate before expiry. Requires a PKI mount with the NHDW intermediate CA.
#
# Usage:
#   issue-server-cert.sh <common-name> <output-dir> [alt-names] [ip-sans]
#
# Output layout:
#   <output-dir>/cert.pem       server certificate
#   <output-dir>/key.pem        private key (mode 600)
#   <output-dir>/fullchain.pem  cert + intermediate + root (mode 644)
#   <output-dir>/ca.pem         NHDW root CA (mode 644)
#   <output-dir>/issued.json    full OpenBao response (for serial / expiry)
set -euo pipefail

CN="${1:?usage: issue-server-cert.sh <common-name> <output-dir> [alt-names] [ip-sans]}"
OUT="${2:?usage: ... <output-dir> ...}"
ALT="${3:-localhost}"
IP="${4:-127.0.0.1}"

: "${OPENBAO_ADDR:=http://127.0.0.1:8200}"
: "${OPENBAO_TOKEN:?OPENBAO_TOKEN must be set}"

mkdir -p "$OUT"
echo "issuing cert: CN=$CN  alt=$ALT  ip=$IP  -> $OUT"

OB_ADDR="$OPENBAO_ADDR" OB_TOK="$OPENBAO_TOKEN" CN="$CN" ALT="$ALT" IP="$IP" OUT="$OUT" \
python3 <<'PY'
import json, urllib.request, pathlib, os

req = urllib.request.Request(
    f"{os.environ['OB_ADDR']}/v1/pki_int/issue/nhdw-server",
    data=json.dumps({
        "common_name": os.environ["CN"],
        "alt_names":   os.environ["ALT"],
        "ip_sans":     os.environ["IP"],
        "ttl":         "720h",
        "format":      "pem",
    }).encode(),
    method="POST",
    headers={"X-Vault-Token": os.environ["OB_TOK"],
             "Content-Type":  "application/json"},
)
resp = json.loads(urllib.request.urlopen(req).read())
d    = resp["data"]
out  = pathlib.Path(os.environ["OUT"])
(out / "issued.json").write_text(json.dumps(d, indent=2))
(out / "cert.pem").write_text(d["certificate"] + "\n")
(out / "key.pem" ).write_text(d["private_key"] + "\n")
chain = "\n".join([d["certificate"]] + d.get("ca_chain", [])) + "\n"
(out / "fullchain.pem").write_text(chain)

req2 = urllib.request.Request(
    f"{os.environ['OB_ADDR']}/v1/pki_root/ca/pem",
    headers={"X-Vault-Token": os.environ["OB_TOK"]})
(out / "ca.pem").write_text(urllib.request.urlopen(req2).read().decode())
(out / "key.pem").chmod(0o600)
print(f"  serial={d['serial_number']}")
print(f"  expires={d['expiration']}")
PY

echo "done"
