#!/usr/bin/env bash
# predeploy-check.sh: read-only pre-flight before anything deploys to the live
# host (a merge to develop or main, a deploy re-run, ci-redeploy.sh by hand).
#
# It inspects the host rather than the code. A deploy's `actions/checkout`
# cleans the self-hosted runner's work dir, so any external stack (DHIS2,
# ollama, source replicas) started from there loses its files. It changes
# nothing.
#
#   A  runner work dir   nothing may live in it; checkout must be able to clean it
#   B  deploy history    what the deploy will really apply, and since when
#   C  live baseline     what is already broken before the deploy
#   D  DDL + backups     init SQL in the change set needs a fresh nhdw_ops backup
#
# Usage:  bundle/scripts/predeploy-check.sh [target-ref]     (default origin/develop)
#   --workspace-only   run section A only (the guard deploy.yml runs before checkout)
# Env:    NHDW_RUNNER_WS   runner work dir (default: the nhdw runner's)
#         NHDW_BACKUP_DIR  where pre-deploy backups land (default ~/nhdw-backups)
#         NS               k8s namespace (default nhdw)
# Exit:   0 = no FAIL (WARNs are printed for the person deciding), 1 = FAIL
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WS="${NHDW_RUNNER_WS:-/opt/actions-runner/_work/national-data-intelligence-platform/national-data-intelligence-platform}"
BACKUP_DIR="${NHDW_BACKUP_DIR:-$HOME/nhdw-backups}"
NS="${NS:-nhdw}"
GH_REPO="national-data-intelligence/national-data-intelligence-platform"
ONLY_WS=0; TARGET="origin/develop"
for a in "$@"; do case "$a" in --workspace-only) ONLY_WS=1 ;; *) TARGET="$a" ;; esac; done

FAILS=0; WARNS=0
pass() { printf '  PASS  %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; WARNS=$((WARNS+1)); }
fail() { printf '  FAIL  %s\n' "$*"; FAILS=$((FAILS+1)); }
info() { printf '        %s\n' "$*"; }

# A. runner work dir
echo "A  runner work dir: $WS"
if command -v docker >/dev/null; then
  # a bind mount from the work dir: checkout deletes files a container is using
  mounts=$(docker ps -aq | xargs -r docker inspect -f '{{.Name}} {{range .Mounts}}{{.Source}} {{end}}' \
    | grep -F "$WS" | awk '{print $1}' | tr -d / | sort -u | tr '\n' ' ')
  [ -z "$mounts" ] && pass "no container mounts files from the work dir" \
                   || fail "containers mounting files from the work dir: $mounts"
  # started from it (compose working dir) without a mount: its compose file can vanish
  started=$(docker ps -aq | xargs -r docker inspect -f '{{.Name}} {{index .Config.Labels "com.docker.compose.project.working_dir"}}' \
    | grep -F "$WS" | awk '{print $1}' | tr -d / | sort -u | tr '\n' ' ')
  [ -z "$started" ] && pass "no compose stack was started from the work dir" \
                    || warn "compose stacks started from the work dir (data is in volumes; manage them from /opt/nhdw-external): $started"
else
  warn "docker not available, container check skipped"
fi
units=$(grep -lF "$WS" /etc/systemd/system/*.service 2>/dev/null | xargs -r -n1 basename | tr '\n' ' ')
[ -z "$units" ] && pass "no systemd unit runs from the work dir" \
                || fail "systemd units run scripts from the work dir (checkout deletes them mid-run): $units"
if [ -d "$WS" ]; then
  owner=$(stat -c %U "$WS")
  foreign=$(find "$WS" -xdev ! -user "$owner" -print 2>/dev/null | head -3)
  [ -z "$foreign" ] && pass "everything in the work dir is owned by $owner (checkout can clean it)" \
                    || fail "files not owned by $owner; checkout cannot clean them, e.g. ${foreign//$'\n'/, }"
else
  pass "work dir absent (checkout will create it)"
fi
[ "$ONLY_WS" = 1 ] && { echo; echo "FAIL=$FAILS WARN=$WARNS"; [ "$FAILS" = 0 ]; exit; }

if [ -d "$WS" ] && git -C "$REPO" rev-parse -q --verify "$TARGET^{commit}" >/dev/null; then
  extra=$(comm -13 <(git -C "$REPO" ls-tree -r --name-only "$TARGET" | sort) \
                   <(cd "$WS" && find . -path ./.git -prune -o -type f -print 2>/dev/null | sed 's#^\./##' | sort))
  n=$(printf '%s' "$extra" | grep -c . || true)
  [ "$n" = 0 ] && pass "no files in the work dir outside git (checkout deletes nothing of value)" \
               || { warn "$n file(s) in the work dir are not in $TARGET; checkout will DELETE them:"
                    printf '%s\n' "$extra" | awk -F/ '{print $1"/"$2}' | sort | uniq -c | sort -rn | head -5 | sed 's/^/        /'; }
fi

# B. deploy history
echo; echo "B  deploy history"
git -C "$REPO" fetch -q origin 2>/dev/null || warn "git fetch failed; using local refs"
TSHA=$(git -C "$REPO" rev-parse "$TARGET" 2>/dev/null) || { fail "unknown target $TARGET"; TSHA=""; }
last=$(curl -fs "https://api.github.com/repos/$GH_REPO/actions/workflows/deploy.yml/runs?status=success&per_page=1" \
  | python3 -c 'import sys,json;r=json.load(sys.stdin)["workflow_runs"];print(r[0]["head_sha"],r[0]["created_at"]) if r else print("")' 2>/dev/null)
LSHA=${last%% *}; LWHEN=${last#* }
if [ -z "$last" ]; then
  warn "could not read the last successful deploy from GitHub"
else
  days=$(( ( $(date +%s) - $(date -d "$LWHEN" +%s) ) / 86400 ))
  info "last SUCCESSFUL deploy: ${LSHA:0:8} at $LWHEN ($days days ago)"
  [ "$days" -le 7 ] && pass "CI deployed within the last week" \
                    || warn "no successful CI deploy for $days days; live was changed by other means since"
  if [ -n "$TSHA" ] && git -C "$REPO" cat-file -e "$LSHA^{commit}" 2>/dev/null; then
    info "$(git -C "$REPO" rev-list --count "$LSHA..$TSHA") commit(s) since then; ci-redeploy rebuilds only components the push touched, but builds them from the whole $TARGET tree"
  fi
  boot=$(uptime -s)
  [ "$(date -d "$boot" +%s)" -gt "$(date -d "$LWHEN" +%s)" ] \
    && warn "host rebooted ($boot) after the last successful deploy; services may have been started by hand" \
    || pass "no reboot since the last successful deploy"
fi

# C. live baseline
echo; echo "C  live baseline (what is broken before deploying)"
if kubectl -n "$NS" get deploy >/dev/null 2>&1; then
  short=$(kubectl -n "$NS" get deploy -o jsonpath='{range .items[*]}{.metadata.name} {.spec.replicas} {.status.availableReplicas}{"\n"}{end}' \
    | awk '$2>0 && ($3=="" || $3<$2){print $1}' | tr '\n' ' ')
  [ -z "$short" ] && pass "every deployment has its replicas available" \
                  || warn "deployments not available before the deploy: $short"
  crash=$(kubectl -n "$NS" get pods --no-headers 2>/dev/null | awk '$3 ~ /CrashLoop|ImagePull|ErrImage/{print $1" ("$3")"}')
  [ -z "$crash" ] || printf '%s\n' "$crash" | sed 's/^/        /'
  if [ -n "$last" ]; then
    newer=$(kubectl -n "$NS" get rs -o jsonpath='{range .items[?(@.spec.replicas>0)]}{.metadata.ownerReferences[0].name} {.metadata.creationTimestamp}{"\n"}{end}' \
      | while read -r d ts; do [ "$(date -d "$ts" +%s)" -gt "$(date -d "$LWHEN" +%s)" ] && echo "$d"; done | sort -u | tr '\n' ' ')
    [ -z "$newer" ] && pass "live workloads all date from CI deploys" \
                    || warn "rolled out OUTSIDE CI since the last good deploy (a deploy may overwrite them): $newer"
  fi
else
  warn "kubectl cannot reach namespace $NS; live baseline skipped"
fi

# D. DDL + backups
echo; echo "D  DDL and backups"
if [ -n "$TSHA" ] && [ -n "${LSHA:-}" ] && git -C "$REPO" cat-file -e "$LSHA^{commit}" 2>/dev/null; then
  ddl=$(git -C "$REPO" diff --name-only "$LSHA..$TSHA" -- 3_storage/postgres/init | xargs -r -n1 basename | tr '\n' ' ')
  if [ -z "$ddl" ]; then
    pass "no init SQL in the change set"
  else
    info "init SQL the deploy will apply: $ddl"
    fresh=$(find "$BACKUP_DIR" -maxdepth 2 -name 'nhdw_ops-*.dump' -mmin -360 2>/dev/null | head -1)
    [ -n "$fresh" ] && pass "nhdw_ops backup from the last 6 h: $fresh" \
                    || fail "no nhdw_ops backup from the last 6 h in $BACKUP_DIR (pg_dump it before deploying)"
  fi
  info "areas the deploy will touch:"
  git -C "$REPO" diff --name-only "$LSHA..$TSHA" | awk -F/ '{print $1"/"$2}' | sort | uniq -c | sort -rn | head -12 | sed 's/^/          /'
else
  warn "cannot diff last deploy..target; DDL and blast radius unknown"
fi

echo; echo "RESULT  FAIL=$FAILS  WARN=$WARNS  (read every WARN before saying go)"
[ "$FAILS" = 0 ]
