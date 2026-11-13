"""Schema validation for the ml-runner.

Three surfaces:
  1. validate_input_spec():    the DataFrame a feature_spec.yaml query returned
                               matches the package's declared input.signature.
  2. coerce_to_model_schema(): an ad-hoc CSV matches the registered model's
                               MLflow input signature (predict-adhoc mode).
  3. validate_flat_output():   a model's prediction frame conforms to the
                               unified flat forecast schema (ops_ml.ml_predictions
                               columns) before anything is written.

Flat-schema writer rules enforced here (never trusted from the model):
  * alarm_flag = 1 ⇔ alarm_prob >= 0.5 (derived, model's value discarded)
  * disease NULL -> '' (the PK-safe encoding for non-disease models)
  * unknown extra columns are folded into the `attrs` jsonb
  * (disease, district, year, week) must be unique within the frame
"""
from __future__ import annotations

import logging
import math
from datetime import datetime

import numpy as np
import pandas as pd

log = logging.getLogger("runner.schemas")

# The flat forecast columns a model may emit (everything else -> attrs).
KEY_COLUMNS = ["district", "year", "week"]                 # required
NUMERIC_COLUMNS = [
    "pred_cases_mean", "pred_cases_sd",
    "pred_cases_lwr95", "pred_cases_median", "pred_cases_upr95",
    "endemic_mean", "endemic_sd", "outbreak_z",
    "alarm_threshold", "alarm_prob",
]
OPTIONAL_COLUMNS = ["disease", "alarm_flag", "n_draws", "attrs", *NUMERIC_COLUMNS]

_DTYPE_COERCERS = {
    "string":   lambda s: s.astype("string").astype(object),
    "str":      lambda s: s.astype("string").astype(object),
    "int":      lambda s: pd.to_numeric(s, errors="raise").astype("int64"),
    "integer":  lambda s: pd.to_numeric(s, errors="raise").astype("int64"),
    "long":     lambda s: pd.to_numeric(s, errors="raise").astype("int64"),
    "float":    lambda s: pd.to_numeric(s, errors="raise").astype("float64"),
    "double":   lambda s: pd.to_numeric(s, errors="raise").astype("float64"),
    "number":   lambda s: pd.to_numeric(s, errors="raise").astype("float64"),
    "bool":     lambda s: s.astype("boolean").astype(object),
    "boolean":  lambda s: s.astype("boolean").astype(object),
    "date":     lambda s: pd.to_datetime(s, errors="raise"),
    "datetime": lambda s: pd.to_datetime(s, errors="raise"),
}


class SchemaError(ValueError):
    """A validation failure: always fails the run loudly."""


def coerce_ts(ts) -> datetime:
    """Robust timestamp coercion."""
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.fromtimestamp(float(ts))


# 1 · package input signature

def validate_input_spec(df: pd.DataFrame, signature: list[dict]) -> pd.DataFrame:
    """Check + coerce the warehouse frame against feature_spec input.signature.

    `signature` is the package's list of {name, dtype} entries. Missing
    columns are a hard error; extra columns are allowed (models ignore what
    they don't use). Returns the frame with declared columns coerced.
    """
    missing = [c["name"] for c in signature if c["name"] not in df.columns]
    if missing:
        raise SchemaError(
            f"feature query is missing declared input columns: {missing} "
            f"(got: {list(df.columns)})")
    df = df.copy()
    for col in signature:
        name, dtype = col["name"], str(col.get("dtype", "string")).lower()
        coercer = _DTYPE_COERCERS.get(dtype)
        if coercer is None:
            raise SchemaError(f"input.signature: unknown dtype '{dtype}' for "
                              f"column '{name}' (valid: {sorted(set(_DTYPE_COERCERS))})")
        try:
            df[name] = coercer(df[name])
        except Exception as exc:
            raise SchemaError(f"input column '{name}' failed {dtype} coercion: {exc}") from exc
    return df


# 2 · MLflow model signature (predict-adhoc CSV)

def coerce_to_model_schema(df: pd.DataFrame, input_schema) -> pd.DataFrame:
    """Validate an uploaded CSV against a registered model's input signature.

    `input_schema` is mlflow.types.Schema (model.metadata.get_input_schema()).
    Column-name schemas only (that's what the runner logs at train time).
    """
    if input_schema is None:
        log.warning("model has no input signature; skipping CSV validation")
        return df
    required = [c.name for c in input_schema.inputs if c.name]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SchemaError(
            f"input CSV is missing required columns {missing}; the model "
            f"signature requires {required}")
    df = df.copy()
    for col in input_schema.inputs:
        if not col.name:
            continue
        kind = str(col.type)
        try:
            if kind in ("long", "integer"):
                df[col.name] = pd.to_numeric(df[col.name], errors="raise").astype("int64")
            elif kind in ("double", "float"):
                df[col.name] = pd.to_numeric(df[col.name], errors="raise").astype("float64")
            elif kind == "datetime":
                df[col.name] = pd.to_datetime(df[col.name], errors="raise")
            # string/boolean/binary: leave as parsed
        except Exception as exc:
            raise SchemaError(f"CSV column '{col.name}' failed {kind} coercion: {exc}") from exc
    return df


# 3 · flat forecast output

def _clean_number(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def validate_flat_output(out: pd.DataFrame, model_name: str,
                         model_version: str) -> list[dict]:
    """Validate + canonicalise a prediction frame -> ready-to-insert records.

    Enforces the flat-schema contract; raises SchemaError on any violation so the run
    fails loudly instead of writing garbage under a widget.
    """
    if not isinstance(out, pd.DataFrame):
        raise SchemaError(f"model returned {type(out).__name__}, expected a DataFrame")
    if out.empty:
        log.warning("model emitted an EMPTY prediction frame")
        return []

    missing = [c for c in KEY_COLUMNS if c not in out.columns]
    if missing:
        raise SchemaError(f"prediction frame is missing required key columns: {missing}")

    out = out.copy()

    # Keys: district non-empty text, ISO year/week ints.
    out["district"] = out["district"].astype(str).str.strip()
    if (out["district"] == "").any() or out["district"].isna().any():
        raise SchemaError("prediction frame has empty/null district values")
    for col, lo, hi in (("year", 1990, 2100), ("week", 1, 53)):
        try:
            out[col] = pd.to_numeric(out[col], errors="raise").astype("int64")
        except Exception as exc:
            raise SchemaError(f"'{col}' must be integer: {exc}") from exc
        bad = out[(out[col] < lo) | (out[col] > hi)]
        if not bad.empty:
            raise SchemaError(f"'{col}' out of range [{lo},{hi}] in {len(bad)} rows "
                              f"(e.g. {bad[col].iloc[0]})")

    # disease: NULL -> '' (PK-safe encoding for non-disease models).
    if "disease" in out.columns:
        out["disease"] = out["disease"].fillna("").astype(str).str.strip()
    else:
        out["disease"] = ""

    # Uniqueness of the natural key within this run.
    dupes = out.duplicated(subset=["disease", "district", "year", "week"])
    if dupes.any():
        sample = out.loc[dupes, ["disease", "district", "year", "week"]].iloc[0].to_dict()
        raise SchemaError(f"{int(dupes.sum())} duplicate (disease,district,year,week) "
                          f"rows, e.g. {sample}")

    # attrs base: an explicit dict column if the model provided one.
    if "attrs" in out.columns:
        attrs_base = [a if isinstance(a, dict) else {} for a in out["attrs"]]
    else:
        attrs_base = [{} for _ in range(len(out))]

    # Unknown extra columns fold into attrs (e.g. stockout soh_now/status).
    known = set(KEY_COLUMNS) | set(OPTIONAL_COLUMNS)
    extras = [c for c in out.columns if c not in known]
    if extras:
        log.info("folding extra columns into attrs: %s", extras)

    records: list[dict] = []
    rows = out.to_dict(orient="records")
    for i, row in enumerate(rows):
        rec = {
            "model_name": model_name,
            "model_version": str(model_version),
            "disease": row["disease"],
            "district": row["district"],
            "year": int(row["year"]),
            "week": int(row["week"]),
        }
        for col in NUMERIC_COLUMNS:
            rec[col] = _clean_number(row.get(col))
        prob = rec["alarm_prob"]
        if prob is not None and not (0.0 <= prob <= 1.0):
            if -1e-9 <= prob <= 1.0 + 1e-9:          # float fuzz only
                prob = min(max(prob, 0.0), 1.0)
                rec["alarm_prob"] = prob
            else:
                raise SchemaError(f"alarm_prob={prob} outside [0,1] at row {i} "
                                  f"({rec['district']} {rec['year']}-W{rec['week']})")
        # Writer-enforced invariant: alarm_flag = 1 ⇔ alarm_prob >= 0.5.
        rec["alarm_flag"] = None if prob is None else int(prob >= 0.5)
        nd = row.get("n_draws")
        nd = _clean_number(nd)
        rec["n_draws"] = None if nd is None else int(nd)

        attrs = dict(attrs_base[i])
        for c in extras:
            v = row.get(c)
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                v = None
            elif isinstance(v, (np.integer, np.floating, np.bool_)):
                v = v.item()
            elif isinstance(v, (pd.Timestamp, datetime)):
                v = v.isoformat()
            elif v is not None and not isinstance(v, (str, int, float, bool, list, dict)):
                v = str(v)
            attrs[c] = v
        rec["attrs"] = attrs
        records.append(rec)

    log.info("flat-schema validation OK: %d rows (%d attrs-folded columns)",
             len(records), len(extras))
    return records
