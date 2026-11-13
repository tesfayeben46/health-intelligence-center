"""Model-package handling: fetch, parse, dependency install, train import.

A package is a directory (or tarball) following the package contract
(bundle/seed-pack/app/ml/_template/ is the annotated reference):

    manifest.yaml       name/version/kind/deployment_kind/engine/…
    feature_spec.yaml   input.query + input.signature
    train.py            def train(df, params) -> mlflow.pyfunc.PythonModel
    params.yaml         default hyperparameters (optional, {} if absent)
    requirements.txt    pip EXTRAS beyond the baked runner image (optional)
    model_card.md       plain-language description
    tests/              package self-tests (run by validators, not the runner)

Package references the runner accepts:
    /seed/<name>                                  a mounted seed directory
    minio://nhdw-artifacts/ml-packages/<n>/<v>/   an object prefix (or one tarball)

Dependency layering (offline-first):
    1. baked runner image  ->  2. /wheels delta (--no-index)  ->  3. logged network fallback
"""
from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lib import minio_io

log = logging.getLogger("runner.package")

WHEELS_DIR = os.environ.get("NHDW_WHEELS_DIR", "/wheels")

REQUIRED_FILES = ["manifest.yaml", "feature_spec.yaml", "train.py", "model_card.md"]
REQUIRED_MANIFEST_KEYS = ["name", "version", "kind", "deployment_kind"]
VALID_KINDS = {"forecaster", "detector"}
VALID_DEPLOYMENT_KINDS = {"batch", "on_demand"}


@dataclass
class Package:
    root: Path
    manifest: dict
    feature_spec: dict
    params: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.manifest["name"]

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    @property
    def deployment_kind(self) -> str:
        return self.manifest["deployment_kind"]

    @property
    def requirements(self) -> Path:
        return self.root / "requirements.txt"

    @property
    def model_card(self) -> Path:
        return self.root / "model_card.md"


def fetch(ref: str) -> Path:
    """Resolve a package-ref to a local directory."""
    if ref.startswith(("minio://", "s3://")):
        dest = Path(tempfile.mkdtemp(prefix="nhdw-pkg-")) / "package"
        minio_io.download_prefix(ref, dest)
        # A tarball may extract into a single top-level directory; descend.
        if not (dest / "manifest.yaml").exists():
            subdirs = [p for p in dest.iterdir() if p.is_dir()]
            if len(subdirs) == 1 and (subdirs[0] / "manifest.yaml").exists():
                dest = subdirs[0]
        return dest
    path = Path(ref)
    if not path.is_dir():
        raise FileNotFoundError(f"package-ref is neither a minio:// URI nor a "
                                f"local directory: {ref}")
    return path


def load(root: Path) -> Package:
    """Parse + contract-check a package directory."""
    missing = [f for f in REQUIRED_FILES if not (root / f).exists()]
    if missing:
        raise FileNotFoundError(f"package at {root} is missing required files: {missing}")

    manifest = yaml.safe_load((root / "manifest.yaml").read_text()) or {}
    absent = [k for k in REQUIRED_MANIFEST_KEYS if not manifest.get(k)]
    if absent:
        raise ValueError(f"manifest.yaml missing required keys: {absent}")
    if manifest["kind"] not in VALID_KINDS:
        raise ValueError(f"manifest kind '{manifest['kind']}' not in {VALID_KINDS}")
    if manifest["deployment_kind"] not in VALID_DEPLOYMENT_KINDS:
        raise ValueError(f"manifest deployment_kind '{manifest['deployment_kind']}' "
                         f"not in {VALID_DEPLOYMENT_KINDS}")

    feature_spec = yaml.safe_load((root / "feature_spec.yaml").read_text()) or {}
    spec_input = feature_spec.get("input") or {}
    if not spec_input.get("query"):
        raise ValueError("feature_spec.yaml must define input.query")
    if not isinstance(spec_input.get("signature"), list) or not spec_input["signature"]:
        raise ValueError("feature_spec.yaml must define input.signature as a "
                         "non-empty list of {name, dtype} entries")

    params_path = root / "params.yaml"
    params = (yaml.safe_load(params_path.read_text()) or {}) if params_path.exists() else {}
    if not isinstance(params, dict):
        raise ValueError("params.yaml must be a mapping of parameter to default")

    log.info("loaded package %s v%s (%s, %s) from %s", manifest["name"],
             manifest["version"], manifest["kind"], manifest["deployment_kind"], root)
    return Package(root=root, manifest=manifest, feature_spec=feature_spec, params=params)


def install_extras(pkg: Package) -> None:
    """Layered install of the package's requirements.txt extras.

    Layer 1 is the baked runner image (most packages need nothing more);
    an empty/comment-only requirements.txt short-circuits here.
    Layer 2: /wheels delta, strictly offline (`pip --no-index --find-links`).
    Layer 3: network fallback, never silent, so the offline-first
    guarantee is visibly broken when it breaks.
    """
    req = pkg.requirements
    if not req.exists():
        return
    wanted = [ln.strip() for ln in req.read_text().splitlines()
              if ln.strip() and not ln.strip().startswith("#")]
    if not wanted:
        log.info("requirements.txt has no extras; baked image satisfies the package")
        return

    log.info("installing package extras: %s", wanted)
    offline = [sys.executable, "-m", "pip", "install", "--no-cache-dir",
               "--no-index", "--find-links", WHEELS_DIR, "-r", str(req)]
    if subprocess.run(offline, capture_output=True, text=True).returncode == 0:
        log.info("extras satisfied from %s (offline)", WHEELS_DIR)
        return

    log.warning("=" * 72)
    log.warning("offline install failed; falling back to the network for %s", wanted)
    log.warning("Add the missing wheels to bundle/pip-wheels to restore the")
    log.warning("offline-first guarantee for this package.")
    log.warning("=" * 72)
    online = subprocess.run([sys.executable, "-m", "pip", "install",
                             "--no-cache-dir", "-r", str(req)],
                            capture_output=True, text=True)
    if online.returncode != 0:
        raise RuntimeError(f"pip could not satisfy package extras {wanted}:\n"
                           f"{online.stderr[-2000:]}")
    log.warning("extras installed from the network; package is not offline-clean")


def import_train(pkg: Package) -> types.ModuleType:
    """Import the package's train.py as a module.

    The module is registered under a fixed name and later pickled by value
    (cloudpickle.register_pickle_by_value in mlflow_io.log_and_register), so
    the logged pyfunc model reloads without the package source present.
    """
    mod_name = "nhdw_package_train"
    sys.path.insert(0, str(pkg.root))          # let train.py import siblings
    spec = importlib.util.spec_from_file_location(mod_name, pkg.root / "train.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "train", None)):
        raise AttributeError(f"{pkg.root}/train.py must define "
                             "train(df: pd.DataFrame, params: dict)")
    return module


def scalar_params(params: dict) -> dict:
    """Filter params down to what an MLflow params-schema can carry
    (scalars + lists of scalars). Nested dicts stay train-time-only."""
    ok = {}
    for k, v in (params or {}).items():
        if isinstance(v, (str, int, float, bool)):
            ok[k] = v
        elif isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
            ok[k] = v
    return ok
