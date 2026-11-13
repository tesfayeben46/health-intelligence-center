"""Feature-drift computation: the prediction-time half of the drift contract.

`lib/profile.py` logs a `drift/reference_profile.json` on every training run
(numeric histograms + quantiles, categorical top-N). This module compares a
fresh feature frame (the same feature_spec query re-run at drift-check time)
against that reference and emits one drift record per feature:

    {"feature", "kind", "psi", "ks", "drifted"}

Metrics (both model-agnostic: they only read the stable profile shape):
  * PSI  (Population Stability Index): numeric and categorical. The headline
    signal; `drifted` = psi >= threshold (contract default 0.2).
        PSI = Σ_bins (cur% - ref%) · ln(cur% / ref%)
    Rule of thumb: below 0.1 stable, 0.1 to 0.2 moderate, above 0.2 significant shift.
  * KS  (Kolmogorov-Smirnov, numeric only): max gap between the reference and
    current cumulative distributions, approximated from the shared histogram
    bins (the reference stores no raw sample, only the histogram). Diagnostic,
    not the gate.

Everything is derived from `reference_profile.json` alone, so drift monitoring
needs no retraining and no per-model code: a new model is covered the moment
its training run logs a reference profile.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

log = logging.getLogger("runner.drift")

EPS = 1e-6                       # proportion floor; keeps ln()/division finite
DEFAULT_PSI_THRESHOLD = 0.2      # drift contract default
OTHER_BUCKET = "__other__"       # categorical tail (categories beyond top-N)


def _psi(expected_p: np.ndarray, actual_p: np.ndarray) -> float:
    """PSI over two aligned proportion vectors (already summing to ~1)."""
    e = np.clip(expected_p.astype("float64"), EPS, None)
    a = np.clip(actual_p.astype("float64"), EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def _numeric_drift(ref: dict, series: pd.Series) -> dict | None:
    """PSI + KS for a numeric feature using the reference histogram bins."""
    hist = ref.get("histogram") or {}
    edges = hist.get("edges") or []
    counts = np.asarray(hist.get("counts") or [], dtype="float64")
    if len(edges) < 2 or counts.sum() <= 0:
        return None
    edges = np.asarray(edges, dtype="float64")

    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype="float64")
    if values.size == 0:
        return None
    # Assign out-of-range current values to the boundary bins (a real shift
    # still registers in the tails) so both histograms share the same bins.
    clipped = np.clip(values, edges[0], edges[-1])
    cur_counts, _ = np.histogram(clipped, bins=edges)
    cur_counts = cur_counts.astype("float64")
    if cur_counts.sum() <= 0:
        return None

    expected_p = counts / counts.sum()
    actual_p = cur_counts / cur_counts.sum()
    psi = _psi(expected_p, actual_p)

    # KS ≈ max gap between the two cumulative distributions across shared bins.
    ref_cdf = np.cumsum(expected_p)
    cur_cdf = np.cumsum(actual_p)
    ks = float(np.max(np.abs(ref_cdf - cur_cdf))) if ref_cdf.size else None
    return {"kind": "numeric", "psi": psi, "ks": ks}


def _categorical_drift(ref: dict, series: pd.Series) -> dict | None:
    """PSI for a categorical feature over {reference top-N values} + __other__."""
    top = ref.get("top") or []
    ref_total = float(ref.get("count", 0)) - float(ref.get("null_count", 0))
    if ref_total <= 0 or not top:
        return None

    cats = [str(t["value"]) for t in top]
    ref_counts = np.asarray([float(t["count"]) for t in top] + [0.0], dtype="float64")
    ref_counts[-1] = max(ref_total - ref_counts[:-1].sum(), 0.0)   # __other__

    cur = series.dropna().astype(str).value_counts()
    cur_total = float(cur.sum())
    if cur_total <= 0:
        return None
    cur_counts = np.asarray([float(cur.get(c, 0)) for c in cats] + [0.0], dtype="float64")
    cur_counts[-1] = max(cur_total - cur_counts[:-1].sum(), 0.0)   # __other__

    psi = _psi(ref_counts / ref_counts.sum(), cur_counts / cur_counts.sum())
    return {"kind": "categorical", "psi": psi, "ks": None}


def compare(reference_profile: dict, current_df: pd.DataFrame,
            threshold: float = DEFAULT_PSI_THRESHOLD) -> list[dict]:
    """One drift record per reference feature present in `current_df`.

    Datetime columns and columns the current frame no longer returns are
    skipped (nothing to compare). Each record carries the PSI, the KS (numeric
    only) and the `drifted` verdict.
    """
    columns = (reference_profile or {}).get("columns") or {}
    records: list[dict] = []
    for feature, ref in columns.items():
        if feature not in current_df.columns:
            log.info("drift: feature '%s' absent from current frame; skipping", feature)
            continue
        kind = ref.get("kind")
        if kind == "numeric":
            metrics = _numeric_drift(ref, current_df[feature])
        elif kind == "categorical":
            metrics = _categorical_drift(ref, current_df[feature])
        else:                                   # datetime / unknown -> no PSI
            continue
        if metrics is None:
            continue
        psi = metrics["psi"]
        drifted = psi is not None and math.isfinite(psi) and psi >= threshold
        records.append({
            "feature": feature,
            "kind": metrics["kind"],
            "psi": None if psi is None or not math.isfinite(psi) else round(psi, 6),
            "ks": None if metrics["ks"] is None else round(metrics["ks"], 6),
            "drifted": bool(drifted),
        })
    return records


def summarize(records: list[dict]) -> dict:
    """Fleet-level rollup used for the retrain decision + logging."""
    psis = [r["psi"] for r in records if r["psi"] is not None]
    drifted = [r["feature"] for r in records if r["drifted"]]
    return {
        "features": len(records),
        "max_psi": max(psis) if psis else 0.0,
        "n_drifted": len(drifted),
        "drifted_features": drifted,
    }
