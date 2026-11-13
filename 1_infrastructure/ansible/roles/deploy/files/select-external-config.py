#!/usr/bin/env python3
"""Filter a `kustomize build` stream down to the objects the Helm chart does not render,
so the deploy role can create them without colliding with Helm.

The Helm chart renders every workload, Service, RBAC object and Ingress plus the inline
ConfigMaps (opa-config, trino-*, prometheus/loki/promtail-config). It does not render the
ConfigMaps that kustomize generates from files outside the chart (postgres-vault-init,
opa-policies, keycloak-*, clickhouse-users-d, workspace-profiles) nor the Secrets. Those are
the "deploy-role dependencies" documented in the chart README.

Reads YAML docs on stdin, writes to stdout only:
  * every Secret, and
  * every ConfigMap whose name is not in the chart-owned list (passed as argv or $OWN).

Usage:
  kustomize build overlays/dev | select-external-config.py opa-config,trino-etc,... | kubectl apply -f -
"""
import os
import sys

import yaml


def owned(argv):
    raw = argv[1] if len(argv) > 1 else os.environ.get("OWN", "")
    return {n.strip() for n in raw.split(",") if n.strip()}


def keep(doc, chart_owned):
    if not doc:
        return False
    kind = doc.get("kind")
    if kind == "Secret":
        return True
    if kind == "ConfigMap":
        return doc.get("metadata", {}).get("name") not in chart_owned
    return False


def main():
    chart_owned = owned(sys.argv)
    docs = [d for d in yaml.safe_load_all(sys.stdin.read()) if keep(d, chart_owned)]
    if docs:
        yaml.safe_dump_all(docs, sys.stdout, default_flow_style=False, sort_keys=False)


if __name__ == "__main__":
    main()
