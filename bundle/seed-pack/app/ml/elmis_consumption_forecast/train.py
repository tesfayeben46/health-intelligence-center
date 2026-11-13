"""elmis_consumption_forecast v2: short-history demand forecasting under the PCT
forecast contract (docs/pct-forecast-contract.md).

Target: adjusted_demand (eLMIS normalized consumption; dispensed where NULL) per
facility x tracer product x month, from bi_supply.mart_pct_demand_month. Months a
series did not report are gaps (NaN), never zeros. Stockout-censored months are
kept as observed floors in training but never scored.

Evaluation is an expanding-window fold ledger at horizon 1: tuning origins are the
months that leave ≥ min_train_months of history, ending two months before the last
month; the final origin (second-to-last month -> last month) is the holdout, touched
once. Eligibility and the demand class of a series are computed only from data up
to each origin, so nothing from the future leaks into a forecast.

Comparators (feasible with 12 months, so no seasonal naïve): naive_last, trailing3
(the AMC analogue), damped Holt (gap-aware), Croston, SBA, TSB. Per demand class
(Syntetos-Boylan: smooth / erratic / intermittent / lumpy) the method with the
lowest tuning WAPE is selected. P10/P50/P90 come from the empirical distribution of
scaled tuning errors of the selected method per class (scale = the series' mean
training demand), clipped at zero.

The predeclared champion rule (contract section 6) is evaluated and exposed as the
metric champion_rule_passed: the selected portfolio must beat naive_last and trailing3 on
WAPE on the tuning folds and the holdout, with holdout |bias| ≤ max_abs_bias_pct and
holdout P10-P90 coverage inside [coverage_min, coverage_max].

train() returns the pyfunc model carrying `metrics` (flat floats) and `evaluation`
(tables) for the runner to log inside the registered MLflow run.
Output: the shared flat forecast schema, one row per eligible series; the contract
keys (forecast_contract.yaml) travel in attrs. The Gaussian lwr95/upr95 columns are
not used and stay empty; they are never relabelled as quantiles.
"""
from __future__ import annotations

import hashlib

import mlflow.pyfunc
import numpy as np
import pandas as pd

MODEL_NAME = "elmis_consumption_forecast"
MODEL_VERSION = "v2"

FLAT_COLUMNS = [
    "disease", "district", "year", "week",
    "pred_cases_mean", "pred_cases_sd", "pred_cases_lwr95",
    "pred_cases_median", "pred_cases_upr95",
    "endemic_mean", "endemic_sd", "outbreak_z",
    "alarm_threshold", "alarm_prob", "n_draws",
]
SERIES_KEYS = ["facility_code", "product_code"]
METHODS = ["naive_last", "trailing3", "holt_damped", "croston", "sba", "tsb"]
BASELINES = ["naive_last", "trailing3"]
CLASSES = ["smooth", "erratic", "intermittent", "lumpy"]


# single-series forecasters (y = history up to the origin, NaN = gap)
def _obs(y):
    return y[~np.isnan(y)]


def f_naive_last(y, p):
    o = _obs(y)
    return float(o[-1]) if len(o) else np.nan


def f_trailing3(y, p):
    o = _obs(y[-3:])
    return float(o.mean()) if len(o) else f_naive_last(y, p)


def f_holt_damped(y, p):
    a, b, phi = p["alpha"], p["beta"], p["phi"]
    level = trend = None
    for v in y:
        if level is None:
            if not np.isnan(v):
                level, trend = float(v), 0.0
            continue
        if np.isnan(v):                        # gap: advance the state, no update
            level, trend = level + phi * trend, phi * trend
            continue
        new_level = a * v + (1 - a) * (level + phi * trend)
        trend = b * (new_level - level) + (1 - b) * phi * trend
        level = new_level
    return np.nan if level is None else max(level + phi * trend, 0.0)


def _croston_core(y, a):
    z = q = None
    interval = 0
    for v in _obs(y):
        interval += 1
        if v > 0:
            if z is None:
                z, q = float(v), float(interval)
            else:
                z = a * v + (1 - a) * z
                q = a * interval + (1 - a) * q
            interval = 0
    return z, q


def f_croston(y, p):
    z, q = _croston_core(y, p["croston_alpha"])
    return 0.0 if z is None else z / q


def f_sba(y, p):
    z, q = _croston_core(y, p["croston_alpha"])
    return 0.0 if z is None else (1 - p["croston_alpha"] / 2) * z / q


def f_tsb(y, p):
    a, b = p["tsb_alpha"], p["tsb_beta"]
    z = prob = None
    for v in _obs(y):
        hit = 1.0 if v > 0 else 0.0
        prob = hit if prob is None else b * hit + (1 - b) * prob
        if v > 0:
            z = float(v) if z is None else a * v + (1 - a) * z
    return 0.0 if z is None else prob * z


FORECASTERS = {"naive_last": f_naive_last, "trailing3": f_trailing3,
               "holt_damped": f_holt_damped, "croston": f_croston, "sba": f_sba, "tsb": f_tsb}


def demand_class(y) -> str | None:
    """Syntetos-Boylan class from history (ADI 1.32, CV² 0.49); None if no demand."""
    o = _obs(y)
    nz = o[o > 0]
    if len(nz) == 0:
        return None
    adi = len(o) / len(nz)
    cv2 = float(np.var(nz, ddof=1) / nz.mean() ** 2) if len(nz) > 1 else 0.0
    if adi < 1.32:
        return "smooth" if cv2 < 0.49 else "erratic"
    return "intermittent" if cv2 < 0.49 else "lumpy"


def eligible(y, min_train_months: int) -> bool:
    o = _obs(y)
    return len(o) >= min_train_months and (o > 0).any()


def _scales(y):
    """Training-only scales: mean |Δ| (MASE) and mean Δ² (RMSSE) of consecutive observations."""
    o = _obs(y)
    d = np.diff(o)
    if len(d) == 0:
        return np.nan, np.nan
    return float(np.mean(np.abs(d))), float(np.mean(d ** 2))


# metrics
def point_metrics(actual, fc, s_mase, s_rmsse) -> dict:
    e = fc - actual
    tot = actual.sum()
    ok_m, ok_r = s_mase > 0, s_rmsse > 0
    return {
        "wape": float(np.abs(e).sum() / tot) if tot > 0 else np.nan,
        "mae": float(np.abs(e).mean()) if len(e) else np.nan,
        "bias_pct": float(100 * e.sum() / tot) if tot > 0 else np.nan,
        "mase": float(np.mean(np.abs(e[ok_m]) / s_mase[ok_m])) if ok_m.any() else np.nan,
        "rmsse": float(np.sqrt(np.mean(e[ok_r] ** 2 / s_rmsse[ok_r]))) if ok_r.any() else np.nan,
        "n_points": int(len(e)), "volume": float(tot),
    }


def interval_metrics(actual, p10, p90) -> dict:
    tot = actual.sum()
    pin = lambda q, p: np.maximum(q * (actual - p), (q - 1) * (actual - p))
    return {
        "pinball_p10_rel": float(pin(0.1, p10).sum() / tot) if tot > 0 else np.nan,
        "pinball_p90_rel": float(pin(0.9, p90).sum() / tot) if tot > 0 else np.nan,
        "coverage_p10_p90": float(np.mean((actual >= p10) & (actual <= p90))) if len(actual) else np.nan,
        "width_rel": float((p90 - p10).sum() / tot) if tot > 0 else np.nan,
    }


class ElmisConsumptionForecastModel(mlflow.pyfunc.PythonModel):
    def __init__(self, params: dict | None = None):
        p = dict(DEFAULT_PARAMS)
        p.update({k: v for k, v in (params or {}).items() if k in DEFAULT_PARAMS})
        self.p = {k: (float(v) if isinstance(DEFAULT_PARAMS[k], float) else int(v)) for k, v in p.items()}
        self.name, self.version = MODEL_NAME, MODEL_VERSION
        self.class_method: dict[str, str] = {}
        self.quantiles: dict[str, tuple[float, float, float]] = {}
        self.metrics: dict[str, float] = {}
        self.evaluation: dict[str, list] = {}
        self.performance_rows: list[dict] = []

    # frame -> series matrix on the calendar grid
    @staticmethod
    def _series(df: pd.DataFrame):
        df = df.copy()
        df["adjusted_demand"] = pd.to_numeric(df["adjusted_demand"], errors="coerce").clip(lower=0)
        for c in ("stock_in_hand", "amc"):
            df[c] = pd.to_numeric(df.get(c), errors="coerce")
        months = sorted(df["month_id"].astype(str).unique())
        idx = {m: i for i, m in enumerate(months)}
        keys = df[SERIES_KEYS].drop_duplicates().sort_values(SERIES_KEYS).reset_index(drop=True)
        kpos = {tuple(r): i for i, r in enumerate(keys.itertuples(index=False, name=None))}
        n, m = len(keys), len(months)
        Y = np.full((n, m), np.nan); C = np.zeros((n, m), bool)
        S = np.full((n, m), np.nan); A = np.full((n, m), np.nan)
        rows = [kpos[t] for t in df[SERIES_KEYS].itertuples(index=False, name=None)]
        cols = [idx[x] for x in df["month_id"].astype(str)]
        Y[rows, cols] = df["adjusted_demand"].to_numpy(float)
        C[rows, cols] = df["demand_censored"].astype("boolean").fillna(False).astype(bool).to_numpy()
        S[rows, cols] = df["stock_in_hand"].to_numpy(float)
        A[rows, cols] = df["amc"].to_numpy(float)
        meta = (df.sort_values("month_id").groupby(SERIES_KEYS, sort=True)
                  [["product_name", "region_id", "facility_type_code", "programme_name"]].last()
                  .reindex(pd.MultiIndex.from_frame(keys)).reset_index())
        return months, keys, meta, Y, C, S, A

    def _origins(self, months):
        k = self.p["min_train_months"]
        holdout = len(months) - 2
        tuning = list(range(max(k - 1, holdout - self.p["n_tuning_folds"]), holdout))
        return tuning, holdout

    # backtest
    def backtest(self, df: pd.DataFrame) -> pd.DataFrame:
        months, keys, meta, Y, C, S, A = self._series(df)
        tuning, holdout = self._origins(months)
        recs = []
        for o in tuning + [holdout]:
            t = o + 1
            for i in range(len(keys)):
                hist = Y[i, :o + 1]
                if not eligible(hist, self.p["min_train_months"]):
                    continue
                actual = Y[i, t]
                if np.isnan(actual) or C[i, t]:            # not reported, or censored: not scored
                    continue
                s_mase, s_rmsse = _scales(hist)
                obs = _obs(hist)
                rec = {"split": "holdout" if o == holdout else "tuning",
                       "as_of_month": months[o], "target_month": months[t],
                       "series": i, "demand_class": demand_class(hist), "actual": float(actual),
                       "scale": float(obs[obs > 0].mean()), "s_mase": s_mase, "s_rmsse": s_rmsse,
                       "soh_origin": float(S[i, o]) if not np.isnan(S[i, o]) else np.nan,
                       "amc_origin": float(A[i, o]) if not np.isnan(A[i, o]) else np.nan,
                       "stockout_next": bool(C[i, t])}
                for mth, fn in FORECASTERS.items():
                    rec[f"fc_{mth}"] = fn(hist, self.p)
                recs.append(rec)
        bt = pd.DataFrame(recs)
        return bt.merge(meta.reset_index().rename(columns={"index": "series"}), on="series", how="left")

    def fit_from_backtest(self, bt: pd.DataFrame) -> None:
        tun, hol = bt[bt.split == "tuning"], bt[bt.split == "holdout"]

        def wape(frame, col):
            tot = frame["actual"].sum()
            return float((frame[col] - frame["actual"]).abs().sum() / tot) if tot > 0 else np.inf

        # 1. per-class method selection on tuning WAPE
        per_class = []
        for cls in CLASSES:
            f = tun[tun.demand_class == cls]
            scores = {m: wape(f, f"fc_{m}") for m in METHODS} if len(f) else {}
            best = min(scores, key=scores.get) if scores else "trailing3"
            self.class_method[cls] = best
            per_class.append({"demand_class": cls, "n_points": int(len(f)), "selected": best,
                              **{f"wape_{m}": round(v, 4) for m, v in scores.items()}})

        def selected_fc(frame):
            return np.array([r[f"fc_{self.class_method[r['demand_class']]}"] for _, r in frame.iterrows()]) \
                if len(frame) else np.array([])

        # 2. empirical quantiles of scaled tuning errors, per class (pooled fallback)
        tun = tun.assign(fc_sel=selected_fc(tun))
        tun = tun.assign(r=(tun.actual - tun.fc_sel) / tun.scale)
        pooled = tuple(np.quantile(tun.r, [0.1, 0.5, 0.9])) if len(tun) else (0.0, 0.0, 0.0)
        for cls in CLASSES:
            rr = tun.loc[tun.demand_class == cls, "r"]
            self.quantiles[cls] = tuple(np.quantile(rr, [0.1, 0.5, 0.9])) if len(rr) >= self.p["min_class_points"] else pooled

        def with_quantiles(frame):
            if not len(frame):
                return frame
            fc = selected_fc(frame)
            q = np.array([self.quantiles[c] for c in frame.demand_class])
            p = np.maximum(fc[:, None] + q * frame.scale.to_numpy()[:, None], 0.0)
            p = np.sort(p, axis=1)
            return frame.assign(p10=p[:, 0], p50=p[:, 1], p90=p[:, 2])

        tun, hol = with_quantiles(tun), with_quantiles(hol)

        # 3. metrics per split: portfolio (P50) vs each method vs eLMIS AMC
        m: dict[str, float] = {}
        for split, f in (("tuning", tun), ("holdout", hol)):
            if not len(f):
                continue
            a = f.actual.to_numpy(float); sm = f.s_mase.to_numpy(float); sr = f.s_rmsse.to_numpy(float)
            for k, v in point_metrics(a, f.p50.to_numpy(float), sm, sr).items():
                m[f"{split}_{k}"] = v
            for k, v in interval_metrics(a, f.p10.to_numpy(float), f.p90.to_numpy(float)).items():
                m[f"{split}_{k}"] = v
            for mth in METHODS:
                m[f"{split}_wape_{mth}"] = wape(f, f"fc_{mth}")
            amc = f[f.amc_origin.notna()]
            m[f"{split}_wape_elmis_amc"] = wape(amc, "amc_origin") if len(amc) else np.nan
            m[f"{split}_n_series"] = float(f.series.nunique())
            # operational: alert when period-start stock < P50 (would run out within the month)
            alert = (f.soh_origin < f.p50).to_numpy()
            m[f"{split}_stockout_alert_rate"] = float(alert.mean())
        # 4. predeclared champion rule (contract section 6)
        ok = True
        for split in ("tuning", "holdout"):
            port = m.get(f"{split}_wape", np.inf)
            ok &= all(port < m.get(f"{split}_wape_{b}", -np.inf) for b in BASELINES)
        ok &= abs(m.get("holdout_bias_pct", np.inf)) <= self.p["max_abs_bias_pct"]
        cov = m.get("holdout_coverage_p10_p90", 0) * 100
        ok &= self.p["coverage_min"] <= cov <= self.p["coverage_max"]
        m["champion_rule_passed"] = float(ok)
        self.metrics = {k: float(v) for k, v in m.items() if v is not None and np.isfinite(v)}

        # 5. evaluation tables (logged as JSON artifacts)
        def seg(frame, col):
            out = []
            for key, g in frame.groupby(col, dropna=False):
                a = g.actual.to_numpy(float)
                out.append({col: None if pd.isna(key) else str(key), "n_points": int(len(g)),
                            **{k: (None if not np.isfinite(v) else round(v, 4)) for k, v in point_metrics(
                                a, g.p50.to_numpy(float), g.s_mase.to_numpy(float), g.s_rmsse.to_numpy(float)).items()
                               if k in ("wape", "bias_pct", "mase")},
                            "coverage_p10_p90": round(float(np.mean((a >= g.p10) & (a <= g.p90))), 4)})
            return out
        ledger = []
        for (split, as_of), g in pd.concat([tun, hol]).groupby(["split", "as_of_month"]):
            ledger.append({"split": split, "as_of_month": as_of, "target_month": g.target_month.iloc[0],
                           "n_points": int(len(g)), "wape_p50": round(wape(g, "p50"), 4),
                           **{f"wape_{mth}": round(wape(g, f"fc_{mth}"), 4) for mth in METHODS}})
        # Backtest rows for ops_ml.model_performance (runner writes them under the new
        # version): one per fold window as_of -> target, split named in every metric.
        rows = []
        for (split, as_of), g in pd.concat([tun, hol]).groupby(["split", "as_of_month"]):
            a = g.actual.to_numpy(float)
            pm = point_metrics(a, g.p50.to_numpy(float), g.s_mase.to_numpy(float), g.s_rmsse.to_numpy(float))
            im = interval_metrics(a, g.p10.to_numpy(float), g.p90.to_numpy(float))
            met = {f"backtest_{split}_{k}": v for k, v in {**pm, **im}.items() if k not in ("n_points", "volume")}
            met.update({f"backtest_{split}_wape_{mth}": wape(g, f"fc_{mth}") for mth in METHODS})
            amc = g[g.amc_origin.notna()]
            if len(amc):
                met[f"backtest_{split}_wape_elmis_amc"] = wape(amc, "amc_origin")
            for cls, gc in g.groupby("demand_class"):
                met[f"backtest_{split}_wape_class_{cls}"] = wape(gc, "p50")
            met[f"backtest_{split}_volume"] = pm["volume"]
            rows.append({"window_start": pd.Period(as_of, freq="M").start_time.date(),
                         "window_end": pd.Period(g.target_month.iloc[0], freq="M").start_time.date(),
                         "metrics": {k: float(v) for k, v in met.items() if v is not None and np.isfinite(v)},
                         "sample_size": int(len(g))})
        self.performance_rows = rows

        self.evaluation = {
            "fold_ledger": ledger, "per_class": per_class,
            "quantiles_scaled": [{"demand_class": c, "q10": round(q[0], 4), "q50": round(q[1], 4), "q90": round(q[2], 4)}
                                 for c, q in self.quantiles.items()],
            "holdout_by_class": seg(hol, "demand_class"), "holdout_by_province": seg(hol, "region_id"),
            "holdout_by_facility_type": seg(hol, "facility_type_code"), "holdout_by_programme": seg(hol, "programme_name"),
        }

    # pyfunc contract: forecast the month after the latest month
    def predict(self, context, model_input, params=None):  # noqa: D401
        months, keys, meta, Y, C, S, A = self._series(model_input)
        o = len(months) - 1
        as_of = months[o]
        target = (pd.Period(as_of, freq="M") + 1)
        iso = target.start_time.isocalendar()
        rows = []
        for i in range(len(keys)):
            hist = Y[i, :o + 1]
            if not eligible(hist, self.p["min_train_months"]):
                continue
            cls = demand_class(hist)
            method = self.class_method.get(cls, "trailing3")
            fc = FORECASTERS[method](hist, self.p)
            obs = _obs(hist); scale = float(obs[obs > 0].mean())
            q = self.quantiles.get(cls, (0.0, 0.0, 0.0))
            p10, p50, p90 = sorted(max(fc + qq * scale, 0.0) for qq in q)
            soh = float(S[i, o]) if not np.isnan(S[i, o]) else np.nan
            if np.isnan(soh):
                risk = np.nan
            elif soh <= 0:
                risk = 1.0
            else:   # P(demand over the month > stock), piecewise-linear through (P10,.9) (P50,.5) (P90,.1)
                risk = float(np.interp(soh, [p10, p50, p90], [0.9, 0.5, 0.1], left=0.9, right=0.05))
            status = ("unknown" if np.isnan(risk) else "stockout" if soh <= 0 else
                      "risk" if risk >= 0.5 else "watch" if risk >= 0.1 else "ok")
            fac, prod = keys.iloc[i]
            rows.append({
                "disease": "", "district": f"{fac}|{prod}", "year": int(iso[0]), "week": int(iso[1]),
                "pred_cases_mean": p50, "pred_cases_median": p50, "pred_cases_sd": np.nan,
                "pred_cases_lwr95": np.nan, "pred_cases_upr95": np.nan,
                "endemic_mean": np.nan, "endemic_sd": np.nan, "outbreak_z": np.nan,
                "alarm_threshold": p50, "alarm_prob": 0.0 if np.isnan(risk) else risk, "n_draws": np.nan,
                "facility_code": str(fac), "product_code": str(prod),
                "product_name": str(meta.loc[i, "product_name"]),
                "as_of_month": as_of, "target_month": str(target), "horizon": 1,
                "p10": p10, "p50": p50, "p90": p90, "demand_class": cls, "method": method,
                "soh_now": None if np.isnan(soh) else soh,
                "amc": None if np.isnan(A[i, o]) else float(A[i, o]), "status": status,
            })
        return pd.DataFrame(rows)


DEFAULT_PARAMS = {
    "alpha": 0.5, "beta": 0.2, "phi": 0.85,             # damped Holt (fixed; one comparator among six)
    "croston_alpha": 0.1, "tsb_alpha": 0.1, "tsb_beta": 0.1,
    "min_train_months": 6, "n_tuning_folds": 5, "min_class_points": 30,
    "max_abs_bias_pct": 10.0, "coverage_min": 70.0, "coverage_max": 90.0,
}


def feature_query_hash(query: str) -> str:
    return hashlib.sha256(" ".join(query.split()).encode()).hexdigest()[:16]


def train(df: pd.DataFrame, params: dict) -> mlflow.pyfunc.PythonModel:
    """Run the contract backtest, select methods per demand class, calibrate
    quantiles, and return the pyfunc model carrying metrics + evaluation tables."""
    model = ElmisConsumptionForecastModel(params)
    bt = model.backtest(df)
    if bt.empty:
        raise ValueError("backtest produced no scored points: check feature_spec.yaml scoping")
    model.fit_from_backtest(bt)
    print(f"[{MODEL_NAME}] champion_rule_passed={model.metrics.get('champion_rule_passed')} "
          f"tuning_wape={model.metrics.get('tuning_wape'):.4f} holdout_wape={model.metrics.get('holdout_wape'):.4f}")
    if model.predict(None, df).empty:
        raise ValueError("training frame produced zero forecast rows; check feature_spec.yaml scoping")
    return model
