#!/usr/bin/env bash
# sources-up.sh: bring up and seed the external replicas the warehouse depends on:
# the ingestion sources (DHIS2, an OpenMRS EMR, the EPSS SQL Server hosting HCMISWDM
# + mBrana, a HAPI FHIR facility registry, a climate CSV API, an eLMIS/OpenLMIS
# logistics extract), the Fayda OIDC IdP,
# and the LLM (Ollama) the dashboard assistant calls. These stand in for systems
# outside the platform: they run in docker compose, not k8s (in production they are
# operated by others), and publish on 0.0.0.0 so k3d pods reach them via host.k3d.internal (the LLM
# on :11434, fayda on :8096, each source on its own port).
#
# bootstrap.sh calls `sources-up.sh up` after the k3d cluster exists (so
# host.k3d.internal routing is valid) and before seed-realistic.
#
# Usage:
#   sources-up.sh up        # compose up + restore/seed every source + LLM + fayda
#   sources-up.sh down      # docker compose down every external stack (teardown)
#   sources-up.sh up llm    # (optional) just one target: llm|dhis2|ethioemr|hcmis|mfr|climate|elmis
#
# Host prereqs (documented in each source README): docker compose, python3 with the
# per-source deps (requests for DHIS2/MFR seeders; pyodbc/pymssql for the SQL-Server
# synthesize scripts). The reader passwords in each 0_external/sources/<src>/.env must match the
# `source-creds` Secret the in-cluster ingestors use.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="$REPO/0_external/sources"
EXT="$REPO/0_external"

log()  { printf '  %s\n' "$*"; }
step() { printf '\n▸ %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }

# Ensure a source has a usable .env (restore/seed scripts `source .env`). The committed
# .env.example carries changeme-* placeholders that already match source-creds for dev.
ensure_env() { local d="$1"; [ -f "$d/.env" ] || { [ -f "$d/.env.example" ] && cp "$d/.env.example" "$d/.env" && log "created $(basename "$d")/.env from .env.example"; }; return 0; }

compose() { docker compose -f "$1/docker-compose.yml" "${@:2}"; }

# Poll an HTTP endpoint until it returns (optionally matching a body substring).
wait_http() { # url pattern timeout_s
  local url="$1" pat="${2:-}" t="${3:-300}" i=0
  while [ "$i" -lt "$t" ]; do
    local body; body="$(curl -fs --max-time 5 "$url" 2>/dev/null)" && { [ -z "$pat" ] || printf '%s' "$body" | grep -q "$pat"; } && return 0
    sleep 5; i=$((i+5))
  done
  return 1
}

# Poll `docker inspect` health until healthy.
wait_healthy() { # container timeout_s
  local c="$1" t="${2:-240}" i=0
  while [ "$i" -lt "$t" ]; do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null)" = "healthy" ] && return 0
    sleep 5; i=$((i+5))
  done
  return 1
}

# Country guard. The seed-only-if-current contract compares a synth version. A
# packed dump full of Ethiopian rows stamped with the current version would make
# every branch below skip synthesis, and the warehouse would come up Ethiopian
# whichever profile was requested. Where the marker does not record the country,
# a non-default entity build forces re-synthesis: a packed dump is only trusted
# for the country it was packed for.
# Country trust for packed dumps: one definition, shared with the test.
. "$(dirname "${BASH_SOURCE[0]}")/lib-country-guard.sh"
country_guard_init
[ "$COUNTRY_FORCES_SYNTH" = "1" ] \
  && echo "  ▸ profile ${WANT_PROFILE} requested: packs are trusted only if stamped for it"

# The SYNTH_VERSION a synthesizer script declares (its data contract). Compared
# against the marker the synthesizer stamps into the DB (dbo.SynthMeta /
# openmrs.synth_meta): a restored pack made by an older synthesizer fails the
# seed-only-if-current check and is re-synthesised, so a stale .bak cannot mask
# a synthesizer change.
synth_ver() { grep -oE 'SYNTH_VERSION *= *[0-9]+' "$1" | head -1 | grep -oE '[0-9]+'; }

# per-source bring-up

up_llm() {
  # External Ollama: the dashboard assistant's model runtime (0_external/llm/).
  # It runs outside the cluster because on WSL2 a GPU cannot be used inside a
  # nested k3d pod, so a host-local ollama container is the working 14b-on-GPU
  # path. The gateway reaches it at host.k3d.internal:11434.
  step "LLM: external Ollama (dashboard assistant model runtime)"
  local d="$EXT/llm"; local files=(-f "$d/docker-compose.yml")
  # With a host GPU, layer the additive GPU overlay so ollama offloads the model.
  # CPU-only hosts run the base compose unchanged (3b). This is the same host-GPU
  # decision gpu-detect.sh uses to pick the gateway model.
  local have_gpu=0
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    have_gpu=1; files+=(-f "$d/docker-compose.gpu.yml")
    log "host GPU detected: bringing up ollama WITH the GPU overlay (14b)"
  else
    log "no host GPU: bringing up ollama on CPU (3b)"
  fi
  docker compose "${files[@]}" up -d >/dev/null 2>&1 || { warn "llm compose up failed"; return 1; }
  wait_http "http://127.0.0.1:11434/api/tags" "" 180 \
    || { warn "external ollama never became ready on :11434"; return 1; }
  log "external ollama healthy (host.k3d.internal:11434)"
  # Ensure the model(s) the gateway will use are present in the mounted weights.
  # Both 3b and 14b ship in 0_external/llm/models, so this is a no-op on a bundle
  # cold boot; it pulls (with a warning) only if a weight is missing.
  ensure_model() { # tag
    docker exec nhdw-ollama ollama list 2>/dev/null | grep -q "qwen2.5:$1" && { log "qwen2.5:$1 present in the weights"; return 0; }
    warn "qwen2.5:$1 not in the weights: NETWORK pull (FIX THE BUNDLE to ship it)"
    docker exec nhdw-ollama ollama pull "qwen2.5:$1" >/dev/null 2>&1 && log "qwen2.5:$1 pulled" || warn "qwen2.5:$1 pull failed"
  }
  ensure_model 3b
  [ "$have_gpu" = 1 ] && ensure_model 14b
  # Preload the model the gateway will use into (V)RAM and pin it resident, so the
  # first assistant request is not a cold load (which surfaces as "model
  # unreachable"). 14b on GPU hosts, 3b on CPU, matching the gateway's model pick.
  local warm_model="qwen2.5:3b"; [ "$have_gpu" = 1 ] && warm_model="qwen2.5:14b"
  curl -s --max-time 120 "http://127.0.0.1:11434/api/generate" \
    -d "{\"model\":\"$warm_model\",\"keep_alive\":\"24h\",\"prompt\":\"warmup\",\"stream\":false,\"options\":{\"num_predict\":1}}" \
    >/dev/null 2>&1 && log "$warm_model preloaded + pinned resident" \
    || warn "$warm_model preload failed (first assistant call will cold-start)"
}

up_fayda() {
  step "Fayda Replica: external OIDC IdP (national digital-ID integration demo)"
  compose "$EXT/fayda" up -d --build >/dev/null 2>&1 || { warn "fayda compose up failed"; return 1; }
  wait_healthy fayda-replica 120 && log "fayda-replica healthy (localhost:8096)" || warn "fayda-replica not healthy (continuing)"
}

up_climate() {
  step "Climate: CSV API (serves the mounted weekly.csv)"
  ensure_env "$SRC/Climate"
  # The weekly.csv it serves is a gitignored seed (bundle/seeds/sources/**/*.csv),
  # so it is absent on a fresh host and climate-api would crash-loop on
  # FileNotFoundError. Generate it if missing; the generator is committed even
  # though its output is not.
  CLIMATE_CSV="$REPO/bundle/seeds/sources/climate/weekly.csv"
  if [ ! -f "$CLIMATE_CSV" ]; then
    if [ "$WANT_PROFILE" != "et-v3" ]; then
      # The generator hardcodes Ethiopian region climate profiles, so a
      # non-default country has no climate data of its own. Skip the source rather
      # than serve Ethiopian rainfall under another flag; the profile catalog
      # disables the widgets that read it.
      warn "climate has no ${WANT_PROFILE} data (generator is Ethiopia-only): skipping"
      return 0
    fi
    log "weekly.csv missing: generating it (gitignored seed)"
    ( cd "$SRC/Climate" && python3 generate_climate.py ) \
      && log "climate weekly.csv generated" \
      || { warn "climate generate failed: cannot start climate-api"; return 1; }
  fi
  compose "$SRC/Climate" up -d >/dev/null 2>&1 || { warn "climate compose up failed"; return 1; }
  wait_healthy climate-api 120 && log "climate-api healthy" || warn "climate-api not healthy (continuing)"
}

up_mfr() {
  step "MFR: HAPI FHIR facility registry"
  ensure_env "$SRC/MFR"
  compose "$SRC/MFR" up -d >/dev/null 2>&1 || { warn "mfr compose up failed"; return 1; }
  wait_http "http://127.0.0.1:8093/fhir/metadata" "" 300 || { warn "MFR FHIR never became ready"; return 1; }
  log "MFR ready: seeding FHIR bundles"
  ( cd "$SRC/MFR" && MFR_FHIR_BASE="http://127.0.0.1:8093/fhir" python3 scripts/seed.py ) \
    && log "MFR seeded" || warn "MFR seed.py failed"
}

up_ethioemr() {
  step "EthioEMR: OpenMRS/MySQL"
  ensure_env "$SRC/EthioEMR"
  compose "$SRC/EthioEMR" up -d >/dev/null 2>&1 || { warn "ethioemr compose up failed"; return 1; }
  wait_healthy ethioemr-mysql 240 || { warn "ethioemr-mysql not healthy"; return 1; }
  log "ethioemr-mysql healthy: restoring dump + provisioning reader"
  ( cd "$SRC/EthioEMR" && bash scripts/restore.sh ) && log "EthioEMR restored" || warn "EthioEMR restore.sh failed"
  # Seed only if current (pack/unpack contract): the restore artifact is the
  # source of truth. A factory dump is schema-only and synthesis fills it; a
  # packed dump carries clinical rows (including runtime changes) and is
  # trusted, but only while its synth_meta version matches the synthesizer's
  # contract. Version drift means the script changed what it writes, so it
  # re-synthesises. A non-empty dump without a marker is trusted with a warning
  # (it may carry runtime clinical edits that a blind re-synthesis would destroy).
  MYQ() { docker exec ethioemr-mysql mysql -h127.0.0.1 -uroot -p"${ETHIOEMR_MYSQL_ROOT_PASSWORD:-changeme-ethioemr-root}" -N -e "$1" 2>/dev/null | tr -dc 0-9; }
  # text-safe twin: the country stamp is a word, and tr -dc 0-9 would eat it
  MYQT() { docker exec ethioemr-mysql mysql -h127.0.0.1 -uroot -p"${ETHIOEMR_MYSQL_ROOT_PASSWORD:-changeme-ethioemr-root}" -N -e "$1" 2>/dev/null | tr -cd 'a-zA-Z0-9._-'; }
  OBS=$(MYQ "SELECT COUNT(*) FROM openmrs.obs;")
  EMR_HAVE=$(MYQ "SELECT COALESCE(MAX(synth_version),0) FROM openmrs.synth_meta;")
  EMR_PROF=$(MYQT "SELECT COALESCE(MAX(profile),'') FROM openmrs.synth_meta;")
  EMR_WANT=$(synth_ver "$SRC/EthioEMR/scripts/synthesize.py")
  if profile_matches "${EMR_PROF:-}" && [ "${OBS:-0}" -gt 0 ] && [ "${EMR_HAVE:-0}" = "${EMR_WANT:-1}" ]; then
    log "EthioEMR already carries current clinical data (obs=${OBS}, synth v${EMR_HAVE}): skipping synthesize"
  elif [ "${OBS:-0}" -gt 0 ] && [ "${EMR_HAVE:-0}" = "0" ]; then
    warn "EthioEMR carries data (obs=${OBS}) but NO synth marker (pre-marker pack): trusting it; re-pack to stamp"
  else
    [ "${OBS:-0}" -gt 0 ] && log "EthioEMR synth-version drift (have v${EMR_HAVE:-0}, want v${EMR_WANT:-1}): re-synthesizing"
    ( cd "$SRC/EthioEMR" && python3 scripts/synthesize.py ) && log "EthioEMR synthesized" || warn "EthioEMR synthesize failed"
  fi
}

up_hcmis_mbrana() {
  # Ethiopian-only supply/reporting sources; the zm-v1 catalog disables their
  # widgets and mBrana's restore targets a container that does not exist in the
  # shared-instance setup. Skip for a non-default profile (same as climate).
  if [ "${NHDW_PROFILE:-et-v3}" != "et-v3" ] && [ "${NHDW_KEEP_SOURCES:-0}" != "1" ]; then
    warn "hcmis/mbrana have no ${NHDW_PROFILE} data: skipping (widgets are disabled)"
    return 0
  fi
  # One SQL Server hosts both HCMISWDM and HCMISWCloud (mBrana). Bring the instance
  # up via the HCMISWDM compose, restore HCMISWDM, then restore mBrana side-by-side.
  step "HCMISWDM + mBrana: shared SQL Server 2025"
  ensure_env "$SRC/HCMISWDM"; ensure_env "$SRC/mBrana"
  compose "$SRC/HCMISWDM" up -d >/dev/null 2>&1 || { warn "hcmiswdm compose up failed"; return 1; }
  wait_healthy hcmiswdm-sqlserver 300 || { warn "hcmiswdm-sqlserver not healthy"; return 1; }
  log "SQL Server healthy: restoring HCMISWDM"
  ( cd "$SRC/HCMISWDM" && bash scripts/restore.sh ) && log "HCMISWDM restored" || warn "HCMISWDM restore.sh failed"
  # Seed only if current: the golden .bak carries facts, but only facts stamped
  # with the current synthesizer contract count; a version-drifted (or
  # marker-less) pack is re-synthesised in place (idempotent DELETE + INSERT).
  SQLQ() { docker exec hcmiswdm-sqlserver /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "${HCMISWDM_SA_PASSWORD:-ChangeMe-MUST-be-strong-Aa1!}" -C -h -1 -Q "SET NOCOUNT ON; $1" 2>/dev/null | tr -dc 0-9; }
  SQLQT() { docker exec hcmiswdm-sqlserver /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "${HCMISWDM_SA_PASSWORD:-ChangeMe-MUST-be-strong-Aa1!}" -C -h -1 -Q "SET NOCOUNT ON; $1" 2>/dev/null | tr -cd 'a-zA-Z0-9._-'; }
  FACTS=$(SQLQ "SELECT COUNT(*) FROM HCMISWDM.Fact.DailyRecord")
  WDM_HAVE=$(SQLQ "SELECT ISNULL(MAX(SynthVersion),0) FROM HCMISWDM.dbo.SynthMeta")
  WDM_PROF=$(SQLQT "SELECT ISNULL(MAX(Profile),'') FROM HCMISWDM.dbo.SynthMeta")
  WDM_WANT=$(synth_ver "$SRC/HCMISWDM/scripts/synthesize_analytics.py")
  # Dispatch plan (Supply Control Tower) uses the same restore with its own marker,
  # so a pack that predates the dispatch loader re-synthesizes to add it.
  DISP_HAVE=$(SQLQ "SELECT ISNULL(MAX(SynthVersion),0) FROM HCMISWDM.dbo.DispatchSynthMeta")
  DISP_WANT=$(grep -oE 'DISPATCH_SYNTH_VERSION *= *[0-9]+' "$SRC/HCMISWDM/scripts/synthesize_dispatch.py" | head -1 | grep -oE '[0-9]+')
  if profile_matches "${WDM_PROF:-}" && [ "${FACTS:-0}" -gt 0 ] && [ "${WDM_HAVE:-0}" = "${WDM_WANT:-1}" ] && [ "${DISP_HAVE:-0}" = "${DISP_WANT:-1}" ]; then
    log "HCMISWDM already carries current facts (DailyRecord=${FACTS}, synth v${WDM_HAVE}, dispatch v${DISP_HAVE}): skipping synthesize"
  else
    [ "${FACTS:-0}" -gt 0 ] && log "HCMISWDM synth-version drift (analytics have v${WDM_HAVE:-0}/want v${WDM_WANT:-1}, dispatch have v${DISP_HAVE:-0}/want v${DISP_WANT:-1}): re-synthesizing"
    ( cd "$SRC/HCMISWDM" && python3 scripts/synthesize.py && python3 scripts/synthesize_analytics.py && python3 scripts/synthesize_dispatch.py ) \
      && log "HCMISWDM synthesized" || warn "HCMISWDM synthesize failed (facts may be empty: resilient views tolerate this)"
  fi
  log "restoring mBrana (HCMISWCloud) into the same instance"
  ( cd "$SRC/mBrana" && bash scripts/restore.sh ) && log "mBrana restored" || warn "mBrana restore.sh failed"
  MBR=$(SQLQ "SELECT COUNT(*) FROM HCMISWCloud.mFlowUSSD.SOHLogs")
  MBR_HAVE=$(SQLQ "SELECT ISNULL(MAX(SynthVersion),0) FROM HCMISWCloud.dbo.SynthMeta")
  MBR_PROF=$(SQLQT "SELECT ISNULL(MAX(Profile),'') FROM HCMISWCloud.dbo.SynthMeta")
  MBR_WANT=$(synth_ver "$SRC/mBrana/scripts/synthesize.py")
  if profile_matches "${MBR_PROF:-}" && [ "${MBR:-0}" -gt 0 ] && [ "${MBR_HAVE:-0}" = "${MBR_WANT:-1}" ]; then
    log "mBrana already carries current data (SOHLogs=${MBR}, synth v${MBR_HAVE}): skipping synthesize"
  else
    [ "${MBR:-0}" -gt 0 ] && log "mBrana synth-version drift (have v${MBR_HAVE:-0}, want v${MBR_WANT:-1}): re-synthesizing"
    ( cd "$SRC/mBrana" && python3 scripts/synthesize.py ) && log "mBrana synthesized" || warn "mBrana synthesize failed"
  fi
}

up_elmis() {
  step "eLMIS: OpenLMIS historical extract (Postgres)"
  ensure_env "$SRC/OpenLMIS"
  compose "$SRC/OpenLMIS" up -d >/dev/null 2>&1 || { warn "elmis compose up failed"; return 1; }
  wait_healthy elmis-postgres 240 || { warn "elmis-postgres not healthy"; return 1; }
  log "elmis-postgres healthy"
  # Seed only if empty, dump first with a synthesis fallback. The historical dump
  # is not redistributable and eLMIS has no upstream app to regenerate it, so when
  # the dump is absent (e.g. a fresh public checkout) the source is not left empty
  # (that would blank the supply and PCT views). Instead an equivalent Zambian
  # supply history is synthesised deterministically from the committed entity
  # model and facility registry (scripts/synthesize.sh). If the dump is present
  # it wins. A populated DB is always trusted.
  local dump="$SRC/OpenLMIS/sample-data/elmis_historical_data_simulated.sql.gz"
  PGQ() { docker exec elmis-postgres psql -U "${ELMIS_DB_USER:-elmis}" -d "${ELMIS_DB_NAME:-elmis}" -At -c "$1" 2>/dev/null | tr -dc 0-9; }
  local lines; lines=$(PGQ "SELECT count(*) FROM history.requisition_line;")
  if [ "${lines:-0}" -gt 0 ]; then
    log "eLMIS already carries history (requisition_line=${lines}): skipping restore"
  elif [ -f "$dump" ]; then
    log "restoring eLMIS extract ($(du -h "$dump" | cut -f1) gz / ~1.5 GB: 5-15 min)"
    ( cd "$SRC/OpenLMIS" && bash scripts/restore.sh ) >/dev/null 2>&1 \
      && log "eLMIS restored" || warn "eLMIS restore.sh failed"
  else
    log "eLMIS dump absent: synthesizing Zambian supply history (deterministic, ~2 min)"
    ( cd "$SRC/OpenLMIS" && bash scripts/synthesize.sh ) \
      && log "eLMIS synthesized" || { warn "eLMIS synthesize failed"; return 1; }
  fi
  # Reader provisioning is separate from the data restore: a persisted volume can
  # carry data without the reader login, and the skip-restore path would leave the
  # ingestor unable to log in. Ensure it on every run.
  ensure_elmis_reader
}

# Idempotent: create/repair the read-only `ingestor` login the pipeline connects
# as, and (re)grant it read on the historical star. Safe to call whenever
# elmis-postgres is up and carries the schema.
ensure_elmis_reader() {
  local envf="$SRC/OpenLMIS/.env"
  [ -f "$envf" ] && { set -a; . "$envf"; set +a; }
  local U="${ELMIS_READER_USER:-ingestor}" PW="${ELMIS_READER_PASSWORD:-changeme-elmis-reader}"
  local DB="${ELMIS_DB_NAME:-elmis}" DBU="${ELMIS_DB_USER:-elmis}"
  docker exec -i elmis-postgres psql -v ON_ERROR_STOP=1 -U "$DBU" -d "$DB" >/dev/null 2>&1 <<SQL && log "eLMIS reader '$U' ensured" || warn "eLMIS reader provisioning skipped (schema not ready?)"
DO \$do\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='$U') THEN CREATE ROLE "$U" LOGIN PASSWORD '$PW';
  ELSE ALTER ROLE "$U" LOGIN PASSWORD '$PW'; END IF;
END \$do\$;
GRANT CONNECT ON DATABASE $DB TO "$U";
GRANT USAGE ON SCHEMA reference, history, mutation TO "$U";
GRANT SELECT ON ALL TABLES IN SCHEMA reference, history, mutation TO "$U";
ALTER DEFAULT PRIVILEGES IN SCHEMA reference, history, mutation GRANT SELECT ON TABLES TO "$U";
SQL
}

up_dhis2() {
  step "DHIS2: the slow one (Liquibase schema init, ~5-10 min) + Ethiopia + IDSR seed"
  ensure_env "$SRC/DHIS2"
  # dhis.conf is a bind-mounted file. If docker starts dhis2-web before the file
  # exists, docker creates a directory at that path, and every recreate then
  # crash-loops with "dhis.conf (Is a directory)". Repair it: a directory here
  # is never valid.
  DHCONF="$SRC/DHIS2/config/dhis.conf"
  if [ -d "$DHCONF" ]; then
    warn "dhis.conf is a directory (docker auto-created it before the file existed), removing"
    # Docker creates this dir as root, so a non-root runner cannot remove it
    # without escalation. Try plain, then sudo; the real config is restored from
    # git right after (the file is committed, so a fresh checkout has it).
    rmdir "$DHCONF" 2>/dev/null || rm -rf "$DHCONF" 2>/dev/null \
      || sudo rm -rf "$DHCONF" 2>/dev/null \
      || { warn "could not remove the stray dhis.conf directory (root-owned; needs sudo)"; return 1; }
    # Restore the committed file if the removal left nothing (git has it).
    if [ ! -f "$DHCONF" ] && git -C "$REPO" show "HEAD:0_external/sources/DHIS2/config/dhis.conf" >/dev/null 2>&1; then
      git -C "$REPO" checkout -- 0_external/sources/DHIS2/config/dhis.conf 2>/dev/null \
        && log "restored dhis.conf from git"
    fi
  fi
  if [ ! -f "$DHCONF" ]; then
    warn "dhis.conf missing at $DHCONF: DHIS2 cannot start without it"; return 1
  fi
  compose "$SRC/DHIS2" up -d >/dev/null 2>&1 || { warn "dhis2 compose up failed"; return 1; }
  # Packed-state restore: if pack.sh exported a pg dump and the DB has no data
  # values yet, restore it (then restart the web app so caches and migrations realign).
  DHIS2_DUMP="$SRC/DHIS2/sample-data/dhis2_seeded.pgdump"
  if [ -f "$DHIS2_DUMP" ]; then
    for i in $(seq 1 24); do docker exec dhis2-db pg_isready -U dhis -d postgres -q 2>/dev/null && break; sleep 5; done
    DV=$(docker exec dhis2-db psql -U dhis -d dhis2 -At -c "SELECT count(*) FROM datavalue" 2>/dev/null | tr -dc 0-9)
    if [ "${DV:-0}" -eq 0 ]; then
      log "restoring packed DHIS2 state ($(du -h "$DHIS2_DUMP" | cut -f1))"
      ( cd "$SRC/DHIS2" && bash scripts/restore.sh ) && { docker restart dhis2-web >/dev/null 2>&1; log "DHIS2 restored from pack"; } \
        || warn "DHIS2 packed restore failed: continuing to live-seed path"
    else
      log "DHIS2 already carries data (datavalue=${DV}): packed dump not needed"
    fi
  fi
  # DHIS2 answers /api/system/info once migrations finish (auth required; uses admin).
  wait_http "http://admin:district@127.0.0.1:8080/api/system/info.json" "version" 900 \
    || { warn "DHIS2 never became ready (schema init can take >10 min)"; return 1; }
  # Skip the expensive seed if IDSR is already present (idempotent re-runs).
  # DHIS2 has no synth marker to stamp a country into, so the coarse gate
  # remains here: any non-default profile re-seeds it.
  if [ "$COUNTRY_FORCES_SYNTH" = "0" ] && curl -fs "http://admin:district@127.0.0.1:8080/api/dataElements.json?filter=code:like:IDSR&fields=id&paging=false" 2>/dev/null | grep -q '"id"'; then
    log "DHIS2 already seeded (IDSR data elements present): skipping seed_ethiopia.py"
    return 0
  fi
  log "DHIS2 ready: seeding Ethiopia metadata + HMIS + IDSR weekly surveillance"
  ( cd "$SRC/DHIS2" && python3 scripts/seed_ethiopia.py ) && log "DHIS2 seeded (incl. IDSR)" || warn "seed_ethiopia.py failed"
}

# teardown
down_all() { # $1 = "--wipe" to also delete the volumes (factory reset)
  local vol_flag=""
  [ "${1:-}" = "--wipe" ] && vol_flag="-v"
  step "Stopping all external source stacks${vol_flag:+ + WIPING volumes (factory reset)}"
  for d in DHIS2 EthioEMR HCMISWDM MFR Climate OpenLMIS; do
    [ -f "$SRC/$d/docker-compose.yml" ] && { compose "$SRC/$d" down $vol_flag >/dev/null 2>&1 && log "$d down${vol_flag:+ (volumes wiped)}" || warn "$d down failed"; }
  done
  [ -f "$EXT/fayda/docker-compose.yml" ] && { compose "$EXT/fayda" down $vol_flag >/dev/null 2>&1 && log "fayda down" || warn "fayda down failed"; }
  # External LLM (0_external/llm/) bind-mounts ./models (no named volume), so `down
  # -v` never wipes the bundled weights. `down` without the GPU overlay is enough.
  [ -f "$EXT/llm/docker-compose.yml" ] && { docker compose -f "$EXT/llm/docker-compose.yml" down $vol_flag >/dev/null 2>&1 && log "llm down" || warn "llm down failed"; }
  # mBrana shares HCMISWDM's instance (no compose of its own); handled above.
}

# main
ACTION="${1:-up}"; ONLY="${2:-}"
case "$ACTION" in
  up)
    echo "== sources-up: bringing up external source replicas =="
      # A country switch that leaves the previous country's source containers
      # running seeds nothing: their databases already hold that country's data
      # and are healthy, so restore.sh skips and the synthesizers never run, and
      # the warehouse keeps the old country while every k8s tier was wiped (the
      # k8s PVC wipe, nhdw_fresh_volumes, does not reach host docker). A
      # non-default country with no per-source scope therefore wipes the source
      # stacks first, so they re-initialise empty and the stamped synthesizers run.
      if [ "$COUNTRY_FORCES_SYNTH" = "1" ] && [ -z "$ONLY" ] && [ "${NHDW_KEEP_SOURCES:-0}" != "1" ]; then
        echo "  â¸ profile ${WANT_PROFILE}: resetting source containers so the new country is synthesized"
        down_all --wipe
      fi
    if [ -n "$ONLY" ]; then
      case "$ONLY" in
        llm) up_llm ;; climate) up_climate ;; mfr) up_mfr ;; ethioemr) up_ethioemr ;;
        hcmis|hcmiswdm|mbrana) up_hcmis_mbrana ;; dhis2) up_dhis2 ;;
        elmis|openlmis) up_elmis ;;
        *) echo "unknown source: $ONLY" >&2; exit 2 ;;
      esac
    else
      # LLM + fayda first (fast; the assistant + auth want them early). Then the
      # data sources; DHIS2 (slowest) last so its long wait overlaps nothing critical.
      up_llm; up_fayda; up_climate; up_mfr; up_ethioemr; up_hcmis_mbrana; up_elmis; up_dhis2
    fi
    echo; echo "== sources-up: done =="
    ;;
  down) down_all "${2:-}" ;;
  *) echo "usage: sources-up.sh up|down [--wipe] | up [source]" >&2; exit 2 ;;
esac
