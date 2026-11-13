"""EWARS-lite: climate-driven negative-binomial GLM early-warning model.

A lightweight, offline stand-in for WHO's EWARS-CSD (R/INLA, 48-64 GB). It
captures the climate-early-warning behaviour without the R/INLA weight:

  response  : weekly `cases` per (woreda x disease)
  family    : Negative Binomial (NB2) GLM  (statsmodels)
  covariates: lagged climate: rainfall / temp / humidity, lags 1..`nlags`
              (default 8), built within each woreda series;
              seasonal harmonics: sin/cos of ISO-week, `nharmonics` (default 2);
              region fixed effects: dummy per region (first dropped);
              offset = log(population) when population is present, else none.
  fitting   : one pooled GLM per disease (woredas pooled, region FE). NB2
              dispersion `alpha` estimated via the Cameron-Trivedi auxiliary
              OLS on a first-pass Poisson fit, then refit as NB(alpha).
  bands     : NB posterior-style sampling of `n_draws` (default 500) from
              NB2(mu, alpha) -> pred_cases_mean/sd/median/lwr95/upr95.
  alarm     : threshold from the endemic channel (reuse the EARS baseline);
              alarm_prob = P(draw > alarm_threshold). Emits n_draws.

Short history
The GLM itself needs no multi-year history: harmonics and lagged climate carry
the seasonal signal from a single year. The endemic channel used for the alarm
threshold has the same two explicit branches as the EARS package: same-ISO-week-
other-years when >= `min_history_years` ISO-years exist, else a rolling
preceding-8-week window. Where the GLM cannot be fit (too few rows / singular
design) each disease falls back to its endemic mean.

provenance = ewars. Package contract (bundle/seed-pack/app/ml/_template): never emits
alarm_flag (platform derives alarm_prob >= 0.5) and never serializes attrs
(extras folded by runner). Written from the published EWARS-CSD method
description; no R/INLA code is copied.
"""
from __future__ import annotations

import mlflow.pyfunc
import numpy as np
import pandas as pd

# Import narrowly (GLM path only) rather than `statsmodels.api`: the latter
# eagerly imports statsmodels.distributions.discrete, which breaks on scipy
# >= 1.15 (removed scipy._lib._util._lazywhere). This keeps the package
# import-robust across scipy versions (the runner image ships scipy 1.14.1).
from statsmodels.genmod.generalized_linear_model import GLM
from statsmodels.genmod import families as _families
from statsmodels.tools.tools import add_constant

MODEL_NAME = "ewars_lite"
MODEL_VERSION = "v1"
WEEKS_PER_YEAR = 52.1775

FLAT_COLUMNS = [
    "disease", "district", "year", "week",
    "pred_cases_mean", "pred_cases_sd", "pred_cases_lwr95",
    "pred_cases_median", "pred_cases_upr95",
    "endemic_mean", "endemic_sd", "outbreak_z",
    "alarm_threshold", "alarm_prob", "n_draws",
]

_CLIMATE = ["rainfall_mm", "temp_celsius", "humidity_pct"]


# endemic channel (copied from the ears package; packages stay isolated)
def _safe_sd(values: np.ndarray, mean: float) -> float:
    values = np.asarray(values, dtype=float)
    sd = float(np.std(values, ddof=1)) if values.size >= 2 else 0.0
    if not np.isfinite(sd) or sd <= 0.0:
        sd = float(np.sqrt(max(mean, 1.0)))
    return sd


def _endemic_baseline(cases, weeks, years, i, multi_year, fallback_window):
    """Return (mean, sd, kind) for row i using same-week-other-years
    (multi-year) or a rolling preceding-8-week window (fallback)."""
    obs = float(cases[i])
    if multi_year:
        kind = "same-week-multi-year"
        base = cases[(weeks == weeks[i]) & (years != years[i])]
    else:
        kind = f"rolling-{fallback_window}w-fallback"
        lo = max(0, i - fallback_window)
        base = cases[lo:i]
    if base.size == 0:
        return obs, float(np.sqrt(max(obs, 1.0))), kind
    mean = float(np.mean(base))
    return mean, _safe_sd(base, mean), kind


def _harmonics(week: np.ndarray, nharmonics: int) -> dict[str, np.ndarray]:
    cols = {}
    for k in range(1, nharmonics + 1):
        ang = 2.0 * np.pi * k * week / WEEKS_PER_YEAR
        cols[f"sin_{k}"] = np.sin(ang)
        cols[f"cos_{k}"] = np.cos(ang)
    return cols


class EwarsLiteModel(mlflow.pyfunc.PythonModel):
    def __init__(self, nlags: int = 8, nharmonics: int = 2, n_draws: int = 500,
                 k: float = 3.0, min_history_years: int = 2, fallback_window: int = 8,
                 random_state: int = 42):
        self.nlags = int(nlags)
        self.nharmonics = int(nharmonics)
        self.n_draws = int(n_draws)
        self.k = float(k)
        self.min_history_years = int(min_history_years)
        self.fallback_window = int(fallback_window)
        self.random_state = int(random_state)
        self.name = MODEL_NAME
        self.version = MODEL_VERSION
        self.fits: dict = {}       # disease_code -> fit dict (set by fit())

    # design construction (shared by fit + predict)
    def _build_design(self, g: pd.DataFrame, region_cats, fill_means=None):
        """g = one disease frame (all woredas). Returns (g_sorted, X DataFrame,
        offset, use_offset, fill_means). Lags computed within each woreda."""
        g = g.sort_values(["woreda_uid", "year", "week"]).copy()
        for c in _CLIMATE:
            g[c] = pd.to_numeric(g[c], errors="coerce")
        feats = {}
        for c in _CLIMATE:
            for lag in range(1, self.nlags + 1):
                feats[f"{c}_lag{lag}"] = g.groupby("woreda_uid")[c].shift(lag).to_numpy()
        feats.update(_harmonics(g["week"].to_numpy(dtype=float), self.nharmonics))
        X = pd.DataFrame(feats, index=g.index)
        if "region_id" in g.columns:
            for cat in list(region_cats)[1:]:          # drop first category
                X[f"region_{cat}"] = (g["region_id"] == cat).astype(float).to_numpy()
        if fill_means is None:
            fill_means = {}
            for c in X.columns:
                col = X[c].to_numpy(dtype=float)
                finite = col[np.isfinite(col)]
                fill_means[c] = float(finite.mean()) if finite.size else 0.0
        X = X.fillna(value=fill_means).fillna(0.0)
        X = add_constant(X, has_constant="add")
        pop = pd.to_numeric(g.get("population"), errors="coerce") if "population" in g else pd.Series(np.nan, index=g.index)
        use_offset = bool(len(pop) > 0 and pop.notna().all() and (pop > 0).all())
        offset = np.log(pop.to_numpy()) if use_offset else None
        return g, X, offset, use_offset, fill_means

    # training
    def fit(self, df: pd.DataFrame) -> "EwarsLiteModel":
        df = df.copy()
        df["year"] = df["year"].astype(int)
        df["week"] = df["week"].astype(int)
        df["cases"] = pd.to_numeric(df["cases"], errors="coerce").fillna(0.0).astype(float)
        self.fits = {}
        for disease, g in df.groupby("disease_code", sort=False):
            # region fixed effects are optional; tolerate a frame without region_id
            region_cats = (sorted(g["region_id"].dropna().unique().tolist())
                           if "region_id" in g.columns else [])
            g2, X, offset, use_offset, fill_means = self._build_design(g, region_cats)
            y = g2["cases"].to_numpy(dtype=float)
            fit = {"cols": list(X.columns), "region_cats": region_cats,
                   "fill_means": fill_means, "use_offset": use_offset}
            if len(y) >= max(2 * X.shape[1], 20) and np.any(y > 0):
                try:
                    poi = GLM(y, X.to_numpy(), family=_families.Poisson(),
                              offset=offset).fit()
                    mu = np.clip(poi.mu, 1e-6, None)
                    aux_y = ((y - mu) ** 2 - y) / mu
                    alpha = float(np.sum(mu * aux_y) / np.sum(mu * mu))
                    alpha = float(min(max(alpha, 1e-4), 50.0))
                    nb = GLM(y, X.to_numpy(),
                             family=_families.NegativeBinomial(alpha=alpha),
                             offset=offset).fit()
                    fit.update({"kind": "nb_glm", "params": nb.params.tolist(),
                                "alpha": alpha})
                except Exception as exc:
                    fit.update({"kind": "mean", "mu_const": float(max(y.mean(), 0.1)),
                                "alpha": 1.0, "error": type(exc).__name__})
            else:
                fit.update({"kind": "mean", "mu_const": float(max(y.mean(), 0.1)),
                            "alpha": 1.0})
            self.fits[disease] = fit
        return self

    def _predict_mu(self, fit, X: pd.DataFrame, offset):
        if fit["kind"] == "nb_glm":
            beta = np.asarray(fit["params"], dtype=float)
            Xn = X.reindex(columns=fit["cols"], fill_value=0.0).to_numpy(dtype=float)
            eta = Xn @ beta
            if offset is not None:
                eta = eta + offset
            return np.clip(np.exp(eta), 1e-6, None)
        return np.full(len(X), fit["mu_const"], dtype=float)

    # pyfunc entrypoint
    def predict(self, context, model_input, params=None):  # noqa: D401
        params = params or {}
        k = float(params.get("k", self.k))
        if not self.fits:               # allow predict-after-load without explicit fit
            self.fit(model_input)
        df = model_input.copy()
        df["year"] = df["year"].astype(int)
        df["week"] = df["week"].astype(int)
        df["cases"] = pd.to_numeric(df["cases"], errors="coerce").fillna(0.0).astype(float)
        rng = np.random.default_rng(self.random_state)
        rows = []
        for disease, g in df.groupby("disease_code", sort=False):
            fit = self.fits.get(disease)
            if fit is None:
                continue
            g2, X, offset, use_offset, _ = self._build_design(
                g, fit["region_cats"], fill_means=fit["fill_means"])
            g2 = g2.assign(_mu=self._predict_mu(fit, X, offset))  # positional align
            alpha = float(fit.get("alpha", 1.0))
            for district, gd in g2.groupby("woreda_uid", sort=False):
                cases = gd["cases"].to_numpy(dtype=float)
                weeks = gd["week"].to_numpy(dtype=int)
                years = gd["year"].to_numpy(dtype=int)
                mu_series = gd["_mu"].to_numpy(dtype=float)
                multi_year = int(gd["year"].nunique()) >= self.min_history_years
                for j in range(len(gd)):
                    mu = float(max(mu_series[j], 1e-6))
                    draws = self._nb_draws(rng, mu, alpha)
                    e_mean, e_sd, e_kind = _endemic_baseline(
                        cases, weeks, years, j, multi_year, self.fallback_window)
                    threshold = e_mean + k * e_sd
                    obs = float(cases[j])
                    z = (obs - e_mean) / e_sd if e_sd > 0 else 0.0
                    alarm_prob = float(np.mean(draws > threshold))
                    rows.append({
                        "disease": disease, "district": district,
                        "year": int(years[j]), "week": int(weeks[j]),
                        "pred_cases_mean": float(draws.mean()),
                        "pred_cases_sd": float(draws.std(ddof=1)) if draws.size > 1 else 0.0,
                        "pred_cases_lwr95": float(np.percentile(draws, 2.5)),
                        "pred_cases_median": float(np.median(draws)),
                        "pred_cases_upr95": float(np.percentile(draws, 97.5)),
                        "endemic_mean": e_mean, "endemic_sd": e_sd,
                        "outbreak_z": z, "alarm_threshold": threshold,
                        "alarm_prob": alarm_prob, "n_draws": self.n_draws,
                        # extras -> attrs
                        "observed_cases": obs, "mu_glm": mu, "alpha": alpha,
                        "use_offset": use_offset, "glm_kind": fit["kind"],
                        "endemic_kind": e_kind, "nlags": self.nlags,
                        "nharmonics": self.nharmonics, "k": k,
                    })
        return pd.DataFrame(rows)

    def _nb_draws(self, rng, mu: float, alpha: float) -> np.ndarray:
        """Sample n_draws from NB2(mu, alpha): var = mu + alpha*mu^2."""
        if alpha is None or not np.isfinite(alpha) or alpha <= 0:
            return rng.poisson(mu, self.n_draws).astype(float)
        r = 1.0 / alpha
        p = r / (r + mu)
        p = float(min(max(p, 1e-6), 1.0 - 1e-9))
        return rng.negative_binomial(r, p, self.n_draws).astype(float)


def train(df: pd.DataFrame, params: dict) -> mlflow.pyfunc.PythonModel:
    """Fit the pooled-per-disease NB2 GLM and return the pyfunc model."""
    params = params or {}
    model = EwarsLiteModel(
        nlags=params.get("nlags", 8),
        nharmonics=params.get("nharmonics", 2),
        n_draws=params.get("n_draws", 500),
        k=params.get("k", 3.0),
        min_history_years=params.get("min_history_years", 2),
        fallback_window=params.get("fallback_window", 8),
        random_state=params.get("random_state", 42),
    )
    model.fit(df)
    if model.predict(None, df).empty:
        raise ValueError("training frame produced zero forecast rows; check feature_spec.yaml scoping")
    return model
