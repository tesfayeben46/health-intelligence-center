#!/usr/bin/env bash
# lib-country-guard.sh: which country is this boot for, and may a packed dump
# be trusted? Sourced by both sources-up.sh and test-country-guard.sh, so the
# test exercises the same code the boot runs.
#
# The synthesizers stamp their country (Profile/profile beside SynthVersion).
# profile_matches <stamped> returns 0 when the pack may be trusted on country
# grounds:
#   stamped == requested        trust
#   stamped != requested        rebuild
#   no stamp, default boot      trust (unstamped packs are Ethiopian)
#   no stamp, non-default boot  rebuild (contents unknown)

country_guard_init() {
  WANT_PROFILE="${NHDW_PROFILE:-}"
  if [ -z "$WANT_PROFILE" ] && [ -n "${NHDW_ENTITY_BUILD:-}" ]; then
    WANT_PROFILE=$(basename "${NHDW_ENTITY_BUILD}" | sed 's/^_build-entity-model[.-]*//')
  fi
  WANT_PROFILE="${WANT_PROFILE:-et-v3}"
  COUNTRY_FORCES_SYNTH=0
  [ "$WANT_PROFILE" != "et-v3" ] && COUNTRY_FORCES_SYNTH=1
}

profile_matches() {
  local stamped="$1"
  if [ -n "$stamped" ]; then
    [ "$stamped" = "$WANT_PROFILE" ] && return 0
    echo "    pack is stamped '$stamped' but this boot is '$WANT_PROFILE', re-synthesizing"
    return 1
  fi
  [ "$WANT_PROFILE" = "et-v3" ] && return 0
  echo "    pack has no country stamp and this boot is '$WANT_PROFILE', re-synthesizing"
  return 1
}
