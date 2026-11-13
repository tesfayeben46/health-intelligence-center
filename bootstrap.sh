#!/usr/bin/env bash
# bootstrap.sh (repo root): thin wrapper for the Kubernetes bootstrap.
#
# Entry point after extracting the release archive:
#   ./bootstrap.sh                   # full rebuild: sources -> ingestors -> pipeline
#   ./bootstrap.sh teardown          # delete cluster + stop sources (clean slate)
#
# The logic lives in bundle/scripts/bootstrap.sh.

exec bash "$(dirname "$0")/bundle/scripts/bootstrap.sh" "$@"
