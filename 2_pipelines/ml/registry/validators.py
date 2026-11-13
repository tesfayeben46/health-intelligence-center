"""Validation harness: runs against a submitted model artifact before
promotion. Four checks: schema / dependencies / resources / smoke (the
smoke run also measures latency). Returns a list of (validator, passed, detail) tuples; the
gateway persists each to ops_artifact.validator_result.

The smoke check runs the model in a one-shot docker container started with
--network=none (the target isolation is a locked-down namespace with no
egress, read-only mounts and an ephemeral PVC). Where no docker daemon is
available the smoke check skips itself.

This module is import-only: no side effects.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# When the gateway runs inside its own container but spawns the smoke-test
# sandbox via the host's docker socket, the bind-mount source path must be
# valid on the host: paths under /tmp inside the gateway container are not
# visible to the daemon. /tmp/nhdw-sandbox must therefore be mounted at the
# same path inside and outside the container so a temp dir created here can
# be bind-mounted into the sandbox unchanged.
_SANDBOX_WORKDIR_ROOT = os.environ.get("VALIDATOR_WORKDIR")

import yaml

log = logging.getLogger("ml.validators")


@dataclass
class ValidatorResult:
    validator:   str
    passed:      bool
    duration_ms: int
    detail:      dict
    error_msg:   str | None = None


# schema check
REQUIRED_FILES = {
    "driver.py":         "model implementation",
    "deployment.yaml":   "declares batch | on_demand + resources",
    "feature_spec.yaml": "input columns + types",
}


def validate_schema(tar_path: Path) -> ValidatorResult:
    t0 = time.perf_counter()
    missing = []
    found = []
    with tarfile.open(tar_path, "r:*") as tf:
        names = {Path(m.name).name for m in tf.getmembers() if m.isfile()}
    for req, why in REQUIRED_FILES.items():
        (found if req in names else missing).append(req)
    passed = not missing
    return ValidatorResult(
        validator="schema",
        passed=passed,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        detail={"found": found, "missing": missing,
                "required": list(REQUIRED_FILES.keys())},
        error_msg=None if passed else f"missing: {', '.join(missing)}",
    )


# dependency scan
_FORBIDDEN_IMPORTS = {
    "socket":      "raw socket access",
    "ctypes":      "native code loading",
    "subprocess":  "shell out",
    "os.system":   "shell out",
    "requests":    "outbound HTTP",      # nb: gateway uses httpx, model code shouldn't reach network at all
    "urllib":      "outbound HTTP",
}


def validate_deps(tar_path: Path) -> ValidatorResult:
    t0 = time.perf_counter()
    hits: list[dict] = []
    with tarfile.open(tar_path, "r:*") as tf:
        for m in tf.getmembers():
            if not m.isfile() or not m.name.endswith(".py"): continue
            content = tf.extractfile(m).read().decode("utf-8", errors="replace")
            for bad, why in _FORBIDDEN_IMPORTS.items():
                # Match `import bad` or `from bad import …` or `os.system(`
                if re.search(rf"(?m)^\s*(import\s+{re.escape(bad)}\b|from\s+{re.escape(bad)}\s+import)", content):
                    hits.append({"file": m.name, "import": bad, "reason": why})
                elif "." in bad and bad in content:
                    hits.append({"file": m.name, "pattern": bad, "reason": why})
    passed = not hits
    return ValidatorResult(
        validator="deps",
        passed=passed,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        detail={"forbidden_hits": hits,
                "forbidden_list": list(_FORBIDDEN_IMPORTS.keys())},
        error_msg=None if passed else f"{len(hits)} forbidden import(s)",
    )


# resource budget
MAX_CPU = 8         # ≤ XLarge
MAX_MEM_GIB = 16    # ≤ XLarge memory_limit


def validate_resources(tar_path: Path) -> ValidatorResult:
    t0 = time.perf_counter()
    spec = None
    with tarfile.open(tar_path, "r:*") as tf:
        for m in tf.getmembers():
            if Path(m.name).name == "deployment.yaml":
                spec = yaml.safe_load(tf.extractfile(m).read())
                break
    if spec is None:
        return ValidatorResult("resources", False, 0,
            {"error": "deployment.yaml not found"}, "missing deployment.yaml")
    req = spec.get("resource_request") or {}
    cpu = req.get("cpu", "1")
    mem = req.get("memory", "1Gi")
    # very loose parse
    try:
        cpu_num = float(cpu.rstrip("m")) / (1000 if cpu.endswith("m") else 1)
        mem_gib = float(mem.lower().rstrip("gimk").rstrip("b")) / (1024 if mem.lower().endswith(("ki","kb")) else 1)
    except Exception:
        cpu_num, mem_gib = 0, 0
    passed = (cpu_num <= MAX_CPU) and (mem_gib <= MAX_MEM_GIB)
    return ValidatorResult(
        validator="resources",
        passed=passed,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        detail={"requested_cpu": cpu, "requested_memory": mem,
                "max_cpu": MAX_CPU, "max_memory_gib": MAX_MEM_GIB,
                "parsed_cpu": cpu_num, "parsed_mem_gib": mem_gib},
        error_msg=None if passed else f"cpu={cpu_num} mem={mem_gib}Gi exceeds max {MAX_CPU}/{MAX_MEM_GIB}Gi",
    )


# smoke / latency: run the model in an isolated container
# Pre-baked image with pandas/numpy/statsmodels/scikit-learn/scipy installed
# at build time. This is what allows --network=none to be a real isolation
# guarantee: nothing inside the sandbox can resolve DNS or reach PyPI.
# Override via SANDBOX_IMAGE if a different image is preferred per env.
SANDBOX_IMAGE = os.environ.get("SANDBOX_IMAGE", "nhdw/ml-sandbox:1.0.0")
SANDBOX_NETWORK = "none"            # K3D namespace equivalent
SANDBOX_TIMEOUT_S = 60


def validate_smoke(tar_path: Path) -> ValidatorResult:
    """Untar into a tmpdir, then run a one-shot container with
    --network=none that imports the driver, instantiates the model,
    calls fit() + predict() on a tiny synthetic input. Measures
    latency."""
    t0 = time.perf_counter()
    detail: dict = {}
    with tempfile.TemporaryDirectory(dir=_SANDBOX_WORKDIR_ROOT) as workdir:
        wd = Path(workdir)
        with tarfile.open(tar_path, "r:*") as tf:
            tf.extractall(wd, filter="data")  # type: ignore[arg-type]
        # The harness mounts the workdir read-only, runs:
        #   import driver; m = next class subclassing NhdwModelBase; m.fit(); m.predict()
        harness = (wd / "_harness.py")
        harness.write_text("""
import importlib.util, inspect, sys, time, json
sys.path.insert(0, '/workdir')
spec = importlib.util.spec_from_file_location('driver', '/workdir/driver.py')
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as exc:
    print(json.dumps({'ok': False, 'phase': 'import', 'error': str(exc)})); sys.exit(0)
cls = None
for name, obj in inspect.getmembers(mod, inspect.isclass):
    if getattr(obj, 'meta', None) and obj.__module__ == 'driver':
        cls = obj; break
if cls is None:
    print(json.dumps({'ok': False, 'phase': 'class', 'error': 'no class with .meta found'})); sys.exit(0)
try:
    m = cls()
    import pandas as pd
    # 3-row synthetic DF using the feature_columns from meta.
    cols = list(getattr(m.meta, 'feature_columns', []) or ['x'])
    df = pd.DataFrame({c: [1, 2, 3] for c in cols})
    t = time.perf_counter()
    m.fit(df)
    fit_ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    out = m.predict(df)
    pred_ms = (time.perf_counter() - t) * 1000
    print(json.dumps({'ok': True, 'fit_ms': fit_ms, 'predict_ms': pred_ms,
                       'output_rows': len(out)}))
except Exception as exc:
    print(json.dumps({'ok': False, 'phase': 'fit_predict', 'error': str(exc)}))
""", encoding="utf-8")
        # The sandbox runs as nobody (65534). The workdir is created under
        # /tmp/nhdw-sandbox which the gateway writes to as root inside its
        # container, so relax perms so the unprivileged sandbox user can read.
        try:
            os.chmod(wd, 0o755)
            for p in wd.rglob("*"):
                try:
                    os.chmod(p, 0o644 if p.is_file() else 0o755)
                except OSError:
                    pass
        except OSError:
            pass
        try:
            r = subprocess.run([
                "docker", "run", "--rm",
                "--network", SANDBOX_NETWORK,
                "--memory", "1g", "--cpus", "1",
                "--read-only", "--tmpfs", "/tmp",
                "-v", f"{wd}:/workdir:ro",
                "--user", "65534:65534",
                SANDBOX_IMAGE,
                "python", "/workdir/_harness.py",
            ], capture_output=True, text=True, timeout=SANDBOX_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return ValidatorResult("smoke", False,
                int((time.perf_counter() - t0) * 1000),
                {"timeout_s": SANDBOX_TIMEOUT_S},
                f"sandbox timed out after {SANDBOX_TIMEOUT_S}s")
        except (FileNotFoundError, OSError) as exc:
            # No docker CLI / daemon in this environment (under k8s the
            # gateway has no docker socket). 'smoke' is not a required gate:
            # skip cleanly instead of crashing validate_all with an
            # uncaught error.
            return ValidatorResult("smoke", False,
                int((time.perf_counter() - t0) * 1000),
                {"skipped": True, "reason": "docker sandbox unavailable"},
                f"smoke skipped: docker sandbox unavailable ({exc})")
        out = r.stdout.strip().split("\n")[-1]
        try:
            detail = json.loads(out)
        except json.JSONDecodeError:
            detail = {"stdout": r.stdout[-500:], "stderr": r.stderr[-500:]}
        passed = bool(detail.get("ok"))
    return ValidatorResult(
        validator="smoke",
        passed=passed,
        duration_ms=int((time.perf_counter() - t0) * 1000),
        detail=detail,
        error_msg=None if passed else detail.get("error", "smoke failed"),
    )


# orchestrator
def validate_all(tar_path: Path) -> list[ValidatorResult]:
    """Run every validator on a submitted artifact tarball. The order is
    cheapest-first: schema/deps/resources happen in ms; smoke takes ~30s.
    All run regardless of earlier failures so the operator gets one full
    report."""
    return [
        validate_schema(tar_path),
        validate_deps(tar_path),
        validate_resources(tar_path),
        validate_smoke(tar_path),
    ]
