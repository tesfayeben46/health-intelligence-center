#!/usr/bin/env bash
# test-rebuild-guards.sh: assert that each guard the from-scratch rebuild
# depends on is present in the code. One grep per guard catches an edit that
# silently failed to apply or a later change that removed it.
set -uo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
P=0; F=0
ok(){ P=$((P+1)); echo "  ok: $1"; }
no(){ F=$((F+1)); echo "  FAIL: $1"; }
has(){ grep -q "$2" "$R/$1" && ok "$3" || no "$3: missing in $1"; }
absent(){ grep -q "$2" "$R/$1" && no "$3: still present in $1" || ok "$3"; }

echo "== boot integrity =="
has 3_storage/postgres/init/99_init_complete.sql "initdb_sentinel" "initdb sentinel exists (catches half-init)"
has bundle/scripts/bootstrap.sh "initdb sentinel is missing" "bootstrap waits on the sentinel, not just Ready"
has bundle/scripts/bootstrap.sh "secrets.yaml is missing keys" "bootstrap gates on secrets parity"

echo "== images =="
has bundle/scripts/cache-images.sh 'tar -tf "\$tar.tmp"' "cache saves are verified before rename"
has bundle/scripts/cache-images.sh "failed to load (corrupt?)" "a corrupt tar self-heals to a remote pull"

echo "== country switch is real =="
has bundle/scripts/lib-country-guard.sh "profile_matches" "shared country guard exists"
has bundle/scripts/sources-up.sh "resetting source containers" "a country switch resets host source containers"
has bundle/scripts/sources-up.sh "profile_matches" "sources-up compares the stamp"
has 1_infrastructure/ansible/roles/seed/tasks/main.yml "NHDW_PROFILE" "the seed role passes the profile down"

echo "== the specific walls this rebuild hit =="
has 1_infrastructure/ansible/roles/seed/tasks/main.yml "RESOLVES from inside the cluster" "host-alias check tests resolution, not k3d internals"
has 1_infrastructure/ansible/roles/deploy/tasks/main.yml "adopt-into-helm.sh" "deploy adopts pre-existing objects before helm upgrade"
has bundle/scripts/sources-up.sh "dhis.conf is a directory" "dhis.conf directory-mount is repaired"
has bundle/scripts/sources-up.sh "sudo rm -rf" "the dhis.conf repair can escalate for a root-owned dir"
has bundle/scripts/seed-realistic.sh "PF_TRIES" "source preflight retries a cold source"
has bundle/scripts/seed-realistic.sh "JOBTMP" "job manifests use a private per-run tmp dir"
has bundle/scripts/sources-up.sh "weekly.csv missing: generating" "climate regenerates its gitignored seed if absent"
has bundle/scripts/seed-realistic.sh "PF_SKIP" "preflight skips sources the profile does not provide"
has bundle/scripts/seed-realistic.sh "_keep+=" "the ingestor list drops sources the profile does not provide"
has bundle/scripts/sources-up.sh "hcmis/mbrana have no" "Ethiopian-only supply sources are skipped for a non-default profile"
has bundle/scripts/sources-up.sh "synthesizing Zambian supply history" "eLMIS synthesizes when the dump is absent (no lost-artifact hard stop)"
has bundle/scripts/sources-up.sh "ensure_elmis_reader" "eLMIS reader-provisioning is decoupled from the data restore and run every up"
has 0_external/sources/OpenLMIS/scripts/synthesize.sh "reference, history, mutation" "eLMIS synthesizer wrapper is committed"
has bundle/scripts/profile-swap-worker.sh "NHDW_USERS_YAML.*/seeds/security/.*seed" "profile swap repoints the keycloak cast at the resolved users file (no orphaned configmap)"
has bundle/scripts/seed-realistic.sh "was TTL-collected after finishing" "wait_job does not hang on a TTL-collected phantom job"
has bundle/scripts/bootstrap.sh "Clear finalizers on any PVC" "teardown force-clears PVC finalizers so a redeploy is not blocked by a Terminating PVC"
absent 1_infrastructure/k8s/base/ingestor-jobs.yaml "ttlSecondsAfterFinished: 1800" "ingestor jobs are not TTL-collected mid-run (a slow sibling can take ~49min)"

echo "== dhis.conf ships (was gitignored -> docker made a directory) =="
git -C "$R" ls-files --error-unmatch 0_external/sources/DHIS2/config/dhis.conf >/dev/null 2>&1 \
  && ok "the DHIS2 demo config is tracked" || no "dhis.conf is not tracked: docker will make a directory"

echo
echo "  $P passed, $F failed"
[ "$F" -eq 0 ]
