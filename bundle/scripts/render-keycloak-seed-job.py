#!/usr/bin/env python3
"""Render the keycloak-seed Job for this deployment's host base.

The seed writes every OIDC client's redirect URIs, so the Job's browser-facing
env (PUBLIC_HOST_BASE, KC_PUBLIC_BASE, DASH_BASE_*) decides whether logins keep
working. The committed manifest carries the dev convention (127.0.0.1.nip.io);
applying it unchanged to a deployment served on another host rewrites every
client to localhost and breaks login for everyone.

So the host base is supplied, never inherited: this renders the Job and
overrides those env values from --hostbase. Two input paths, same output:

  --from-live <file>   a live Job's JSON (kubectl get job -o json); keeps any
                       provisioning-specific extras the manifest doesn't carry
  stdin                `kustomize build` YAML; used when no live Job exists
                       (it was deleted, or this is a fresh host)

Immutable/system fields (selector, controller-uid labels, status) are stripped
so the result is applyable.

    kustomize build --load-restrictor=LoadRestrictionsNone 1_infrastructure/k8s/base \\
      | render-keycloak-seed-job.py --hostbase <public-ip>.nip.io > job.json
"""
from __future__ import annotations

import argparse
import json
import sys

JOB_NAME = "keycloak-seed"
STRIP_SPEC_KEYS = ("selector", "manualSelector", "completionMode", "suspend")


def host_env(hostbase: str) -> dict[str, str]:
    return {
        "PUBLIC_HOST_BASE": hostbase,
        "KC_PUBLIC_BASE": f"https://keycloak.{hostbase}",
        "DASH_BASE_INTERNAL_BI": f"https://internal-bi.{hostbase}",
        "DASH_BASE_WORKSHOP": f"https://workshop.{hostbase}",
        "DASH_BASE_ADMIN": f"https://admin.{hostbase}",
    }   # exactly the five setup-keycloak.py reads


def load_from_stdin() -> dict:
    """Pull the keycloak-seed Job out of a kustomize YAML stream."""
    try:
        import yaml
    except ImportError:
        sys.exit("PyYAML is required to read the kustomize stream")
    for doc in yaml.safe_load_all(sys.stdin):
        if (doc and doc.get("kind") == "Job"
                and doc.get("metadata", {}).get("name") == JOB_NAME):
            return doc
    sys.exit(f"no Job/{JOB_NAME} found in the kustomize stream")


def normalize(job: dict, hostbase: str) -> dict:
    spec = {k: v for k, v in job["spec"].items() if k not in STRIP_SPEC_KEYS}
    labels = spec.get("template", {}).get("metadata", {}).get("labels", {})
    for key in [k for k in labels if "controller-uid" in k or "job-name" in k]:
        del labels[key]

    wanted = host_env(hostbase)
    for container in spec.get("template", {}).get("spec", {}).get("containers", []):
        env = container.setdefault("env", [])
        seen = set()
        for entry in env:
            if entry.get("name") in wanted:
                entry.pop("valueFrom", None)
                entry["value"] = wanted[entry["name"]]
                seen.add(entry["name"])
        # a manifest that predates one of these vars still gets it
        for name, value in wanted.items():
            if name not in seen:
                env.append({"name": name, "value": value})

    return {"apiVersion": "batch/v1", "kind": "Job",
            "metadata": {"name": JOB_NAME,
                         "namespace": job["metadata"].get("namespace", "nhdw")},
            "spec": spec}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hostbase", required=True,
                    help="deployment host base, e.g. 203.0.113.10.nip.io")
    ap.add_argument("--from-live", help="live Job JSON (kubectl get job -o json)")
    args = ap.parse_args()

    job = (json.load(open(args.from_live)) if args.from_live else load_from_stdin())
    if job.get("kind") != "Job" or job["metadata"]["name"] != JOB_NAME:
        sys.exit(f"input is not Job/{JOB_NAME}")
    print(json.dumps(normalize(job, args.hostbase)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
