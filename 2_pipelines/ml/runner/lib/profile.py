"""Reference feature profile: the training-time half of the drift contract.

At train time the runner profiles the exact frame the model was fitted on and
logs it to the MLflow run as `drift/reference_profile.json`. lib/drift.py
compares each batch-predict input frame against this profile (PSI / KS per
column) and the runner writes ops_ml.model_drift rows, so drift monitoring
needs no retrain.

Shape (stable contract: lib/drift.py depends on it):

    {
      "row_count": 31800,
      "computed_at": "2026-07-03T…Z",
      "columns": {
        "<numeric col>": {"kind": "numeric", "count", "null_count", "mean",
                          "std", "min", "max",
                          "quantiles": {"0.01": …, …, "0.99": …},
                          "histogram": {"edges": […11 floats…],
                                        "counts": […10 ints…]}},
        "<other col>":   {"kind": "categorical", "count", "null_count",
                          "n_unique", "top": [{"value", "count"} by ≤20]}
      }
    }
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

QUANTILES = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)
HISTOGRAM_BINS = 10
TOP_N_CATEGORIES = 20


def _numeric_profile(series: pd.Series) -> dict:
    values = pd.to_numeric(series, errors="coerce").astype("float64")
    clean = values.dropna()
    prof = {
        "kind": "numeric",
        "count": int(len(values)),
        "null_count": int(values.isna().sum()),
    }
    if clean.empty:
        prof.update(mean=None, std=None, min=None, max=None,
                    quantiles={}, histogram={"edges": [], "counts": []})
        return prof
    counts, edges = np.histogram(clean, bins=HISTOGRAM_BINS)
    prof.update(
        mean=float(clean.mean()),
        std=float(clean.std(ddof=0)),
        min=float(clean.min()),
        max=float(clean.max()),
        quantiles={str(q): float(clean.quantile(q)) for q in QUANTILES},
        histogram={"edges": [float(e) for e in edges],
                   "counts": [int(c) for c in counts]},
    )
    return prof


def _categorical_profile(series: pd.Series) -> dict:
    as_str = series.dropna().astype(str)
    top = as_str.value_counts().head(TOP_N_CATEGORIES)
    return {
        "kind": "categorical",
        "count": int(len(series)),
        "null_count": int(series.isna().sum()),
        "n_unique": int(as_str.nunique()),
        "top": [{"value": v, "count": int(c)} for v, c in top.items()],
    }


def build_profile(df: pd.DataFrame) -> dict:
    """Profile every column of the training frame (numeric vs categorical)."""
    columns = {}
    for col in df.columns:
        series = df[col]
        if pd.api.types.is_datetime64_any_dtype(series):
            clean = series.dropna()
            columns[col] = {
                "kind": "datetime",
                "count": int(len(series)),
                "null_count": int(series.isna().sum()),
                "min": clean.min().isoformat() if not clean.empty else None,
                "max": clean.max().isoformat() if not clean.empty else None,
            }
        elif pd.api.types.is_numeric_dtype(series):
            columns[col] = _numeric_profile(series)
        else:
            # object columns holding numbers (all-string raw surfaces) still
            # profile as categorical; the marts supply typed columns.
            columns[col] = _categorical_profile(series)
    return {
        "row_count": int(len(df)),
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "columns": columns,
    }
