"""Widget data fetchers: bind a widget_id to a mart query through Trino.

Each Fetcher declares (a) its mart (Trino `marts.<schema>.<table>`), (b) the OPA
resource attributes (subject, data_class, granularity, sensitivity), so the
widget_data endpoint sends OPA a complete payload and OPA does not deny on a
missing data_class, and (c) the column each row_filter dimension maps to, so OPA's
`row_filter` obligation (the caller's granted scope) is AND-applied to the query.

Global filters: the widget_data endpoint folds the UI filter bar's validated params
(geography, programme, period) into the row_filter via `merge_ui_filters`, which only
narrows; the UI can never widen the OPA scope. `period_col`/`period_kind` bind each
widget's native time column, so the period filter becomes a rolling window anchored
at the scoped data's max date rather than now(), because the data does not run to
the present. `widget_supports()` exposes per-widget dimension support to the
dashboard-spec resolver (the supports{} key).

Widgets with no fetcher fall back to the permitted stub. Fetchers declare their own
OPA attributes because the ops_perm.mart_attrs catalog uses names that do not match
the dbt mart schemas; binding and attributes live together here as the source of truth.

ML-fed widgets (the EWARS dz-* family) also join the marts to model predictions in
ops_ml.ml_predictions through Trino's `ops` catalog (a cross-catalog join, see
1_infrastructure/k8s/base/trino.yaml). Their `params_spec` declares
allowlist-validated server-side params (?model=, ?metric=), the only request values
ever spliced into SQL text.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from . import trino_client

log = logging.getLogger("gateway.fetchers")


def _in_clause(col: str, values) -> str:
    lits = ",".join("'" + str(v).replace("'", "''") + "'" for v in values)
    return f'"{col}" IN ({lits})'


# period windows (global filters)
# The UI's period filter sends one of these canonical keys. They mirror the
# ops_perm.filter 'period' catalog row and biApp.ts PERIOD_OPTS; keep all three
# in step. Each window is a rolling window anchored at the data's max date (the
# max(week_start) idiom), not now(), because the data does not run to the
# present. The anchor is computed over the same geo/programme-scoped subset the
# query reads, so a scoped view keeps its own latest window.
PERIOD_KEYS = ("last_4wk", "last_12wk", "last_6mo", "last_12mo", "last_24mo", "ytd", "efy_ytd")

# date-typed window templates: {e} = the date expression, {a} = its scoped max.
_PERIOD_DATE_WINDOWS = {
    "last_4wk":  "{e} > date_add('day', -28, {a})",
    "last_12wk": "{e} > date_add('day', -84, {a})",
    "last_6mo":  "{e} > date_add('month', -6, {a})",
    "last_12mo": "{e} > date_add('month', -12, {a})",
    "last_24mo": "{e} > date_add('month', -24, {a})",
    "ytd":       "{e} >= date_trunc('year', {a})",
}
# month-grain ('YYYY-MM' varchar) windows: months back, inclusive of the anchor
# month (11 back plus the anchor = 12 months). last_4wk maps to the anchor month.
_PERIOD_MONTHS_BACK = {"last_4wk": 0, "last_12wk": 2, "last_6mo": 5,
                       "last_12mo": 11, "last_24mo": 23}


def _period_clause(value: str, col: str, kind: str, fq_table: str,
                   scope_clauses: tuple[str, ...] = ()) -> str:
    """SQL predicate restricting `col` to the rolling window `value`, anchored
    at max(col) over `fq_table` (with the caller's geo/prog scope applied so
    the anchor follows the scoped data, not the global table).

    kind: 'date'     col is a DATE (week_start, declared_date, ...)
          'month'    col is a 'YYYY-MM' varchar (month_id; lexicographic >=)
          'iso_week' col is a zero-padded 'YYYY-Www' varchar (converted via
                     from_iso8601_date; unpadded '2026W9' is not supported)
          'year'     col is a 'YYYY' varchar (year_id), the coarsest cut:
                     sub-year windows and ytd collapse to the anchor year,
                     last_24mo to the anchor and previous year

    `value` is allowlist-validated here. This is the only guard between a
    query param and SQL text; keep it strict."""
    if value not in PERIOD_KEYS:
        raise ValueError(f"period={value!r} not allowed; valid: {sorted(PERIOD_KEYS)}")
    if kind not in ("date", "month", "iso_week", "year"):
        raise ValueError(f"unknown period_kind {kind!r}")
    scope_where = (" WHERE " + " AND ".join(scope_clauses)) if scope_clauses else ""
    if kind == "year":
        anchor = f'(SELECT max("{col}") FROM {fq_table}{scope_where})'
        if value == "last_24mo":
            return f'"{col}" >= CAST(CAST({anchor} AS integer) - 1 AS varchar)'
        # Year grain is coarser than the EFY boundary, so efy_ytd == ytd == anchor year.
        return f'"{col}" >= {anchor}'
    if kind == "month":
        anchor = f'(SELECT max("{col}") FROM {fq_table}{scope_where})'
        if value == "ytd":
            return f'"{col}" >= substr({anchor}, 1, 4) || \'-01\''
        if value == "efy_ytd":
            # Ethiopian fiscal/HMIS year runs Hamle to Sene (about July to June).
            # EFY start month = July of the anchor's year if the anchor month
            # is >= July, else July of the previous year.
            ay = f'CAST(substr({anchor}, 1, 4) AS integer)'
            am = f'CAST(substr({anchor}, 6, 2) AS integer)'
            efy_start = (f'CAST(IF({am} >= 7, {ay}, {ay} - 1) AS varchar) || \'-07\'')
            return f'"{col}" >= {efy_start}'
        n = _PERIOD_MONTHS_BACK[value]
        return (f'"{col}" >= date_format(date_add(\'month\', -{n}, '
                f'date_parse({anchor} || \'-01\', \'%Y-%m-%d\')), \'%Y-%m\')')
    expr = f'"{col}"' if kind == "date" else f'from_iso8601_date("{col}" || \'-1\')'
    anchor = f'(SELECT max({expr}) FROM {fq_table}{scope_where})'
    if value == "efy_ytd":
        # July 1 (Hamle-boundary) of the EFY that contains the anchor date.
        efy_start = (f"IF(month({anchor}) >= 7, "
                     f"from_iso8601_date(format('%04d-07-01', year({anchor}))), "
                     f"from_iso8601_date(format('%04d-07-01', year({anchor}) - 1)))")
        return f'{expr} >= {efy_start}'
    return _PERIOD_DATE_WINDOWS[value].format(e=expr, a=anchor)


def _where_period(period: str | None, col: str, kind: str, fq_table: str) -> str:
    """' WHERE <period window>' or '', for period_param fetchers whose bespoke
    cross-mart SQL applies the window to marts other than the declared one."""
    return f" WHERE {_period_clause(period, col, kind, fq_table)}" if period else ""


def _and_period(period: str | None, col: str, kind: str, fq_table: str) -> str:
    """' AND <period window>' or '', appended to an existing WHERE."""
    return f" AND {_period_clause(period, col, kind, fq_table)}" if period else ""


def _where(row_filter: dict[str, Any], *, geo_col: str | None,
           prog_col: str | None, commodity_col: str | None = None,
           period_col: str | None = None,
           period_kind: str = "date", fq_table: str | None = None) -> str:
    """Build a WHERE clause from the merged row_filter (OPA's scope obligation
    intersected with the UI's validated global-filter narrowing). Only dimensions
    the mart has a column for are applied. The period predicate goes into the
    same WHERE as geography/programme/commodity (it bounds the base CTE `b`), so
    each fetcher's internal max()-anchored window narrows with it instead of
    stacking a second, conflicting clause. commodity is a plain UI filter (no OPA
    scope obligation carries it) applied like geography/programme; its clause
    precedes period so the period anchor follows the commodity-scoped data."""
    clauses: list[str] = []
    if geo_col and row_filter.get("geography"):
        clauses.append(_in_clause(geo_col, row_filter["geography"]))
    if prog_col and row_filter.get("programme"):
        clauses.append(_in_clause(prog_col, row_filter["programme"]))
    if commodity_col and row_filter.get("commodity"):
        clauses.append(_in_clause(commodity_col, row_filter["commodity"]))
    if period_col and fq_table and row_filter.get("period"):
        pv = row_filter["period"]
        pv = pv[0] if isinstance(pv, (list, tuple)) else pv
        clauses.append(_period_clause(str(pv), period_col, period_kind,
                                      fq_table, scope_clauses=tuple(clauses)))
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


# UI global-filter -> row_filter merge (narrow-only)
# The internal-bi filter bar's dims and the query-param spellings they may
# arrive under. 'geography' values are region ids ('OR'); the catalog governs
# them under filter id 'region', so both spellings are accepted.
FILTER_DIM_PARAMS: dict[str, tuple[str, ...]] = {
    "geography": ("geography", "region"),
    "programme": ("programme",),
    "period":    ("period",),
    "commodity": ("commodity",),
}


def merge_ui_filters(row_filter: dict[str, Any] | None,
                     ui_params: dict[str, Any]) -> dict[str, Any]:
    """Fold the UI's already-validated filter params into OPA's row_filter
    scope obligation. Narrow only: a UI value inside the scope replaces it
    (['OR'] with 'OR' gives ['OR']); 'all' or absent keeps the scope; a value
    outside the scope keeps the tighter scope. The UI can never widen what
    OPA granted."""
    merged: dict[str, Any] = dict(row_filter or {})
    for dim, names in FILTER_DIM_PARAMS.items():
        val = next((str(ui_params[n]) for n in names
                    if ui_params.get(n) not in (None, "", "all")), None)
        if val is None:
            continue
        scope = merged.get(dim)
        if scope:
            if val in {str(v) for v in scope}:
                merged[dim] = [val]
            # Out-of-scope value: keep the tighter OPA scope (never widen).
        else:
            merged[dim] = [val]
    return merged


@dataclass(frozen=True)
class Param:
    """A server-side widget query param (?model=, ?metric=) folded into the SQL.
    Allowlist-validated so only known literals ever reach the query text."""
    default: str
    allowed: tuple[str, ...]

    def resolve(self, name: str, raw: Any) -> str:
        val = str(raw) if raw not in (None, "") else self.default
        if val not in self.allowed:
            raise ValueError(
                f"{name}={val!r} not allowed; valid: {sorted(self.allowed)}")
        return val


# Disease-family models with batch forecasts in ops_ml.ml_predictions (MLflow
# registry @champion aliases). stockout_forecast is excluded because its
# `district` is a commodity, not a woreda_uid.
DZ_MODELS = ("ears", "outbreak", "seasonal", "ewars_lite")


def _ml_latest_run(model: str) -> str:
    """Predictions of `model`'s most recent successful run, via the Trino `ops`
    catalog (nhdw_ops). Same latest-run pattern as routes/ml.py. `model` is
    already allowlist-validated by Param.resolve."""
    # The latest successful run that produced predictions. A train run is also
    # status='success' but writes no ml_predictions, so filtering on status alone
    # can select a run with zero prediction rows and leave the widgets empty.
    run = (f"SELECT r.id FROM ops.ops_ml.ml_run r WHERE r.model_name = '{model}' "
           f"AND r.status = 'success' "
           f"AND r.id IN (SELECT run_id FROM ops.ops_ml.ml_predictions WHERE model_name = '{model}') "
           f"ORDER BY r.finished_at DESC NULLS LAST LIMIT 1")
    return (f"SELECT * FROM ops.ops_ml.ml_predictions "
            f"WHERE model_name = '{model}' AND run_id = ({run})")


@dataclass
class Fetcher:
    schema: str                       # bi_idsr
    table: str                        # mart_idsr_disease_weekly
    subject: str                      # PRG/PT/HP/FAC/PRD -> OPA resource.subject
    data_class: str                   # DC.PRO/DC.PRF/DC.STK/...
    sql: Callable[..., str]           # (fq_table, where_clause) -> SELECT ...
                                      # (plus a resolved-params dict when params_spec is set)
    granularity: str = "aggregate"
    sensitivity: str = "default"
    geo_col: str | None = None        # column for the geography row_filter (or None)
    prog_col: str | None = None       # column for the programme row_filter (or None)
    commodity_col: str | None = None  # column for the commodity UI filter (or None).
                                      # A plain narrowing dim, not an OPA scope obligation:
                                      # set on commodity-grain rm-* marts that carry a
                                      # commodity_name column; region-grain marts leave
                                      # it None because they cannot apply it.
    period_col: str | None = None     # the widget's native date/week/month column
                                      # (week_start, month_id, ...). None means the mart
                                      # has no time dimension; supports{} reports
                                      # period as unsupported.
    period_kind: str = "date"         # 'date' | 'month' ('YYYY-MM') | 'iso_week' | 'year'
    period_param: bool = False        # bespoke cross-mart SQL: instead of a single
                                      # period_col clause, the sql callable takes a
                                      # period=<key|None> kwarg and applies its own
                                      # _where_period/_and_period windows per mart.
                                      # Counts as period support in supports{}.
    params_spec: dict[str, Param] | None = None   # server-side widget params

    def opa_attributes(self) -> dict[str, str]:
        return {"data_class": self.data_class, "granularity": self.granularity,
                "sensitivity": self.sensitivity}

    def supports(self) -> dict[str, bool]:
        """Global-filter dims this widget's mart can apply; drives the
        supports{} key in the resolved dashboard spec. supports.period=true
        must mean the SQL responds to the period param (via period_col or
        period_param). Never advertise a dim the query ignores: the UI would
        show a filter that changes nothing."""
        return {"geography": bool(self.geo_col), "programme": bool(self.prog_col),
                "period": bool(self.period_col) or self.period_param,
                "commodity": bool(self.commodity_col)}

    async def fetch(self, params: dict, row_filter: dict[str, Any]) -> dict[str, Any]:
        fq = f'marts.{self.schema}."{self.table}"'
        where = _where(row_filter, geo_col=self.geo_col, prog_col=self.prog_col,
                       commodity_col=self.commodity_col,
                       period_col=self.period_col, period_kind=self.period_kind,
                       fq_table=fq)
        args: list[Any] = [fq, where]
        if self.params_spec:
            args.append({name: spec.resolve(name, params.get(name))
                         for name, spec in self.params_spec.items()})
        if self.period_param:
            pv = row_filter.get("period")
            pv = pv[0] if isinstance(pv, (list, tuple)) else pv
            period_val = str(pv) if pv else None
            if period_val is not None and period_val not in PERIOD_KEYS:
                raise ValueError(
                    f"period={period_val!r} not allowed; valid: {sorted(PERIOD_KEYS)}")
            query = self.sql(*args, period=period_val)
        else:
            query = self.sql(*args)
        res = await trino_client.query(query)
        return {"columns": res.get("columns", []), "rows": res.get("rows", []),
                "row_count": res.get("row_count", len(res.get("rows", [])))}


# dz-region-map SQL (aggregate map + weekly sparkline)
# Two shapes from the same fetcher and grant, so the sparkline needs no widget
# id of its own: by default one row per region (map, Top-5, summary); with
# ?spark=1 the scoped region's weekly case series for the sidebar sparkline.
def _dz_region_map_sql(t: str, w: str, p: dict) -> str:
    return (
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"mx AS (SELECT max(week_start) AS m FROM b), "
        f"obs AS (SELECT b.region_id, sum(b.new_cases) AS cases_7d, sum(b.deaths) AS deaths_7d "
        f"FROM b CROSS JOIN mx WHERE b.week_start = mx.m GROUP BY 1), "
        f"scope AS (SELECT DISTINCT woreda_uid, region_id, disease_code FROM b), "
        f"ml AS ({_ml_latest_run(p['model'])}), "
        f"mlx AS (SELECT max(year * 100 + week) AS yw FROM ml), "
        f"pr AS (SELECT s.region_id, max(m.alarm_prob) AS p_alarm "
        f"FROM ml m CROSS JOIN mlx "
        f"JOIN scope s ON s.woreda_uid = m.district AND s.disease_code = m.disease "
        f"WHERE m.year * 100 + m.week = mlx.yw GROUP BY 1), "
        # reliability = reporting completeness per region (bi_quality); readiness =
        # share of tracer commodities with ≥1 month of stock per region (bi_supply).
        f"rel AS (SELECT region_id, CAST(round(avg(completeness_pct), 1) AS double) AS reliability_pct "
        f"FROM bi_quality.mart_data_quality GROUP BY region_id), "
        f"rdy AS (SELECT region_id, CAST(round(100.0 * count_if(mos >= 1) "
        f"/ NULLIF(count(*), 0), 1) AS double) AS readiness_pct "
        f"FROM bi_supply.mart_supply_region_stock GROUP BY region_id), "
        # burden = 12-week cases per 100k, population from bi_facility.mart_health_workforce.
        f"pop AS (SELECT region_id, sum(population) AS pop "
        f"FROM bi_facility.mart_health_workforce GROUP BY region_id), "
        f"inc AS (SELECT b.region_id, CAST(round(1e5 * sum(b.new_cases) "
        f"/ NULLIF(max(pop.pop), 0), 1) AS double) AS burden_incidence_100k "
        f"FROM b JOIN pop ON pop.region_id = b.region_id "
        f"CROSS JOIN mx WHERE b.week_start >= date_add('day', -84, mx.m) "
        f"GROUP BY b.region_id) "
        f"SELECT COALESCE(o.region_id, pr.region_id) AS region, "
        f"inc.burden_incidence_100k, "
        f"rdy.readiness_pct, rel.reliability_pct, "
        f"round(pr.p_alarm, 3) AS p_alarm, o.cases_7d, "
        f"round(100.0 * o.deaths_7d / NULLIF(o.cases_7d, 0), 2) AS cfr_pct, "
        # data_through = the latest reported week in scope, for the sidebar freshness tile.
        f"(SELECT m FROM mx) AS data_through "
        f"FROM obs o FULL JOIN pr ON pr.region_id = o.region_id "
        f"LEFT JOIN rel ON rel.region_id = COALESCE(o.region_id, pr.region_id) "
        f"LEFT JOIN rdy ON rdy.region_id = COALESCE(o.region_id, pr.region_id) "
        f"LEFT JOIN inc ON inc.region_id = COALESCE(o.region_id, pr.region_id) "
        f"ORDER BY 1")


def _dz_region_spark_sql(t: str, w: str) -> str:
    # Weekly case series for the scoped region ({w} already narrows region_id +
    # programme). Ordered ascending; the widget takes the trailing weeks.
    return (
        f"SELECT date_format(week_start, '%Y-%m-%d') AS week, "
        f"CAST(sum(new_cases) AS bigint) AS cases "
        f"FROM {t}{w} GROUP BY week_start ORDER BY week_start")


def _dz_region_woreda_sql(t: str, w: str, p: dict) -> str:
    # Single-region drill-down: one row per woreda in the scoped region, so the
    # map's ranked list switches from "Top regions" to "Top woredas by burden"
    # when exactly one region is in scope (a regional-bureau role or a picked
    # region); a Top-5 over one region carries no information. Same grant and
    # mart as the region and spark shapes, no new widget id. burden = 12-week
    # cases per 100k on the woreda's DHIS2 population
    # (bi_idsr.mart_ml_disease_input); cases_7d/cfr = latest reported week;
    # p_alarm = the woreda's peak forecast alarm from the latest model run.
    return (
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"mx AS (SELECT max(week_start) AS m FROM b), "
        f"obs AS (SELECT b.woreda_uid, max(b.woreda_name) AS woreda_name, "
        f"max(b.region_id) AS region_id, "
        f"sum(b.new_cases) FILTER (WHERE b.week_start = (SELECT m FROM mx)) AS cases_7d, "
        f"sum(b.deaths) FILTER (WHERE b.week_start = (SELECT m FROM mx)) AS deaths_7d, "
        f"sum(b.new_cases) FILTER (WHERE b.week_start >= date_add('day', -84, (SELECT m FROM mx))) AS cases_12w "
        f"FROM b GROUP BY b.woreda_uid), "
        f"pop AS (SELECT woreda_uid, max(population) AS pop "
        f"FROM bi_idsr.mart_ml_disease_input WHERE population IS NOT NULL GROUP BY 1), "
        f"scope AS (SELECT DISTINCT woreda_uid, disease_code FROM b), "
        f"ml AS ({_ml_latest_run(p['model'])}), "
        f"mlx AS (SELECT max(year * 100 + week) AS yw FROM ml), "
        f"pr AS (SELECT s.woreda_uid, max(m.alarm_prob) AS p_alarm "
        f"FROM ml m CROSS JOIN mlx "
        f"JOIN scope s ON s.woreda_uid = m.district AND s.disease_code = m.disease "
        f"WHERE m.year * 100 + m.week = mlx.yw GROUP BY 1) "
        f"SELECT obs.woreda_uid AS region, obs.woreda_name, obs.region_id, "
        f"CAST(round(1e5 * obs.cases_12w / NULLIF(pop.pop, 0), 1) AS double) AS burden_incidence_100k, "
        f"obs.cases_7d, "
        f"round(100.0 * obs.deaths_7d / NULLIF(obs.cases_7d, 0), 2) AS cfr_pct, "
        f"round(pr.p_alarm, 3) AS p_alarm, "
        f"(SELECT m FROM mx) AS data_through "
        f"FROM obs LEFT JOIN pop ON pop.woreda_uid = obs.woreda_uid "
        f"LEFT JOIN pr ON pr.woreda_uid = obs.woreda_uid "
        f"ORDER BY burden_incidence_100k DESC NULLS LAST")


# the registry
# Keyed by the canonical widget id (bundle/seed-pack/app/security/widgets.yaml).
FETCHERS: dict[str, Fetcher] = {
    # internal-bi, disease: EWARS-style surveillance (model-fed)
    # Observed epi data (bi_idsr marts) joined with the latest successful run's
    # predictions in ops_ml.ml_predictions (flat forecast schema, woreda_uid by
    # disease_code by ISO year/week grain) through the Trino `ops` catalog.
    # The widget-to-column mapping follows the EWARS reference data contract.
    # The ?model= param picks the forecast model (default ears). The caller's
    # row_filter is applied to the mart side; predictions inherit it through a
    # `scope` CTE join on (woreda_uid, disease_code): geography scopes via
    # woreda, and a programme scope narrows to that programme's diseases.
    # Disease KPI strip (widget contract: one row per tile with tile, value,
    # unit, subtitle, delta_text, delta_direction, footnote_label,
    # footnote_value, icon). Tiles come from outbreaks, disease_weekly and the
    # model. IDSR timeliness and lab-confirmation tiles are omitted because the
    # marts carry no timeliness or lab feed. value is varchar because tiles mix
    # count, percent and text.
    "dz-kpi-strip": Fetcher(
        schema="bi_idsr", table="mart_idsr_disease_weekly",
        subject="PRG", data_class="DC.PRO",
        geo_col="region_id", prog_col="programme_id", period_col="week_start",
        params_spec={"model": Param("ears", DZ_MODELS)},
        sql=lambda t, w, p: (
            f"WITH b AS (SELECT * FROM {t}{w}), "
            f"mx AS (SELECT max(week_start) AS m FROM b), "
            f"cw AS (SELECT COALESCE(sum(new_cases) FILTER (WHERE week_start = mx.m), 0) AS cur, "
            f"COALESCE(sum(new_cases) FILTER (WHERE week_start = mx.m - INTERVAL '7' DAY), 0) AS prev, "
            f"COALESCE(sum(new_cases) FILTER (WHERE week_start > mx.m - INTERVAL '28' DAY), 0) AS cases_28, "
            f"COALESCE(sum(deaths) FILTER (WHERE week_start > mx.m - INTERVAL '28' DAY), 0) AS deaths_28 "
            f"FROM b CROSS JOIN mx), "
            f"rep AS (SELECT count(DISTINCT woreda_uid || '|' || iso_week) AS obs, "
            f"greatest(count(DISTINCT woreda_uid) * count(DISTINCT iso_week), 1) AS exp FROM b), "
            f"names AS (SELECT woreda_uid, max(woreda_name) AS woreda_name FROM b GROUP BY 1), "
            f"scope AS (SELECT DISTINCT woreda_uid, disease_code FROM b), "
            f"ml AS ({_ml_latest_run(p['model'])}), "
            f"mlx AS (SELECT max(year * 100 + week) AS yw FROM ml), "
            f"tw AS (SELECT m.district, max(m.alarm_prob) AS ap FROM ml m CROSS JOIN mlx "
            f"JOIN scope s ON s.woreda_uid = m.district AND s.disease_code = m.disease "
            f"WHERE m.year * 100 + m.week = mlx.yw GROUP BY 1), "
            f"top AS (SELECT n.woreda_name, tw.ap FROM tw JOIN names n ON n.woreda_uid = tw.district "
            f"ORDER BY tw.ap DESC NULLS LAST, tw.district LIMIT 1), "
            f"ob AS (SELECT count(*) AS active FROM bi_idsr.mart_idsr_outbreaks WHERE status = 'active') "
            f"SELECT tile, value, unit, subtitle, delta_text, delta_direction, "
            f"footnote_label, footnote_value, icon FROM ("
            f"SELECT 1 AS ord, 'Active Outbreaks' AS tile, CAST((SELECT active FROM ob) AS varchar) AS value, "
            f"'count' AS unit, 'Currently active' AS subtitle, CAST(NULL AS varchar) AS delta_text, "
            f"CAST(NULL AS varchar) AS delta_direction, 'Reporting completeness' AS footnote_label, "
            f"CAST(round(100.0 * (SELECT obs FROM rep) / (SELECT exp FROM rep)) AS varchar) || '%' AS footnote_value, "
            f"'triangle-alert' AS icon "
            f"UNION ALL SELECT 2, 'New Cases, 7D', CAST((SELECT cur FROM cw) AS varchar), 'count', "
            f"CAST((SELECT cur - prev FROM cw) AS varchar) || ' vs prior week', "
            f"(CASE WHEN (SELECT cur FROM cw) >= (SELECT prev FROM cw) THEN '▲ ' ELSE '▼ ' END) "
            f"|| CAST(abs(round(100.0 * ((SELECT cur FROM cw) - (SELECT prev FROM cw)) / nullif((SELECT prev FROM cw), 0))) AS varchar) || '% vs prior week', "
            f"CASE WHEN (SELECT cur FROM cw) > (SELECT prev FROM cw) THEN 'watch' ELSE 'good' END, "
            f"'Expected woreda-weeks', CAST((SELECT obs FROM rep) AS varchar) || ' / ' || CAST((SELECT exp FROM rep) AS varchar), 'users' "
            f"UNION ALL SELECT 3, 'Case Fatality, 30D', CAST(round(100.0 * (SELECT deaths_28 FROM cw) / nullif((SELECT cases_28 FROM cw), 0), 1) AS varchar), 'percent', "
            f"'Last 4 weeks', CAST(NULL AS varchar), 'alert', 'Death reporting completeness', CAST(NULL AS varchar), 'shield' "
            f"UNION ALL SELECT 4, 'Highest-Risk Woreda', COALESCE((SELECT woreda_name FROM top), 'n/a'), 'text', "
            f"'P(alarm) ' || CAST(round(100 * COALESCE((SELECT ap FROM top), 0)) AS varchar) || '%', "
            f"CAST(NULL AS varchar), 'alert', 'Woreda reliability', CAST(NULL AS varchar), 'triangle-alert' "
            # Tiles 5 and 6: completeness from observed vs expected woreda-weeks;
            # alarm coverage = share of in-scope woredas the model evaluated in
            # its latest week.
            f"UNION ALL SELECT 5, 'Reporting Completeness', "
            f"CAST(round(100.0 * (SELECT obs FROM rep) / (SELECT exp FROM rep)) AS varchar), 'percent', "
            f"'Woreda-weeks received', CAST(NULL AS varchar), CAST(NULL AS varchar), "
            f"'Received / expected', CAST((SELECT obs FROM rep) AS varchar) || ' / ' || CAST((SELECT exp FROM rep) AS varchar), 'check-circle' "
            f"UNION ALL SELECT 6, 'Alarm Coverage', "
            f"CAST(round(100.0 * (SELECT count(DISTINCT district) FROM tw) "
            f"/ nullif((SELECT count(DISTINCT woreda_uid) FROM scope), 0)) AS varchar), 'percent', "
            f"'Woredas evaluated, latest run', CAST(NULL AS varchar), CAST(NULL AS varchar), "
            f"'Model', '{p['model']}' , 'activity' "
            f") AS x ORDER BY ord")),
    # Alarm signal: observed line, model mean with 95% CI band, alarm threshold
    # and alarm flags, per disease and week over the run window. Woreda-grain
    # forecasts are summed to disease-by-week; alarm prob/flag take the max
    # (any woreda in alarm flags the week).
    "dz-signal-chart": Fetcher(
        schema="bi_idsr", table="mart_ml_disease_input",
        subject="PRG", data_class="DC.PRO",
        # mart_ml_disease_input has no programme_id column, so programme is
        # unsupported (supports.programme=false); disease_code is its programme axis.
        geo_col="region_id", period_col="week_start",
        params_spec={"model": Param("ears", DZ_MODELS)},
        sql=lambda t, w, p: (
            f"WITH b AS (SELECT * FROM {t}{w}), "
            f"obs AS (SELECT disease_code, max(disease_label) AS disease_label, "
            f"year, week, sum(cases) AS observed FROM b GROUP BY 1, 3, 4), "
            f"scope AS (SELECT DISTINCT woreda_uid, disease_code FROM b), "
            f"ml AS ({_ml_latest_run(p['model'])}), "
            f"pred AS (SELECT m.disease, m.year, m.week, "
            f"sum(m.pred_cases_mean) AS pred_mean, sum(m.pred_cases_lwr95) AS pred_lwr, "
            f"sum(m.pred_cases_upr95) AS pred_upr, sum(m.endemic_mean) AS endemic, "
            f"sum(m.alarm_threshold) AS threshold, max(m.alarm_prob) AS alarm_prob, "
            f"max(m.alarm_flag) AS alarm_flag "
            f"FROM ml m JOIN scope s ON s.woreda_uid = m.district AND s.disease_code = m.disease "
            f"GROUP BY 1, 2, 3) "
            f"SELECT COALESCE(o.disease_code, pr.disease) AS disease_code, o.disease_label, "
            f"COALESCE(o.year, pr.year) AS year, COALESCE(o.week, pr.week) AS week, "
            f"format('%d-W%02d', COALESCE(o.year, pr.year), COALESCE(o.week, pr.week)) AS iso_week, "
            f"o.observed, round(pr.pred_mean, 1) AS pred_cases_mean, "
            f"round(pr.pred_lwr, 1) AS pred_cases_lwr95, round(pr.pred_upr, 1) AS pred_cases_upr95, "
            f"round(pr.endemic, 1) AS endemic_mean, round(pr.threshold, 1) AS alarm_threshold, "
            f"round(pr.alarm_prob, 3) AS alarm_prob, pr.alarm_flag "
            f"FROM obs o FULL JOIN pred pr ON pr.disease = o.disease_code "
            f"AND pr.year = o.year AND pr.week = o.week "
            f"ORDER BY 1, 3, 4")),
    # Region choropleth (widget contract):
    #   region                region_id
    #   burden_incidence_100k 12-week cases per 100k on the region's population
    #                         (bi_facility.mart_health_workforce)
    #   readiness_pct         share of tracer commodities with >=1 month stock
    #                         (bi_supply.mart_supply_region_stock)
    #   reliability_pct       reporting completeness (bi_quality.mart_data_quality)
    #   p_alarm               region peak forecast alarm (latest ml_predictions run)
    #   cases_7d              observed total in the latest reported week
    #   cfr_pct               deaths / cases in the latest week
    "dz-region-map": Fetcher(
        schema="bi_idsr", table="mart_idsr_disease_weekly",
        subject="PRG", data_class="DC.PRO",
        geo_col="region_id", prog_col="programme_id", period_col="week_start",
        # spark=1: the scoped region's weekly case series (sidebar sparkline);
        # woreda=1: one row per woreda in the scoped region (single-region
        # ranked list). Same grant and mart as the region shape.
        params_spec={"model": Param("ears", DZ_MODELS),
                     "spark": Param("0", ("0", "1")),
                     "woreda": Param("0", ("0", "1"))},
        sql=lambda t, w, p: (
            _dz_region_spark_sql(t, w) if p.get("spark") == "1"
            else _dz_region_woreda_sql(t, w, p) if p.get("woreda") == "1"
            else _dz_region_map_sql(t, w, p))),
    # High-risk woredas (widget contract: rank, woreda, region, cases_12w,
    # incidence_100k, cfr_pct, p_alarm, readiness, reliability), ranked by the
    # model's latest-week peak alarm probability.
    "dz-woreda-table": Fetcher(
        schema="bi_idsr", table="mart_idsr_disease_weekly",
        subject="PRG", data_class="DC.PRO",
        geo_col="region_id", prog_col="programme_id", period_col="week_start",
        params_spec={"model": Param("ears", DZ_MODELS)},
        sql=lambda t, w, p: (
            f"WITH b AS (SELECT * FROM {t}{w}), "
            f"mx AS (SELECT max(week_start) AS m FROM b), "
            f"recent AS (SELECT woreda_uid, max(woreda_name) AS woreda, max(region_id) AS region, "
            f"sum(new_cases) AS cases_12w, sum(deaths) AS deaths_12w "
            f"FROM b CROSS JOIN mx WHERE week_start >= date_add('day', -84, mx.m) GROUP BY 1), "
            f"scope AS (SELECT DISTINCT woreda_uid, disease_code FROM b), "
            f"ml AS ({_ml_latest_run(p['model'])}), "
            f"mlx AS (SELECT max(year * 100 + week) AS yw FROM ml), "
            f"pr AS (SELECT m.district, max(m.alarm_prob) AS p_alarm FROM ml m CROSS JOIN mlx "
            f"JOIN scope s ON s.woreda_uid = m.district AND s.disease_code = m.disease "
            f"WHERE m.year * 100 + m.week = mlx.yw GROUP BY 1), "
            # incidence uses the woreda population (DHIS2 POP-TOTAL via the ML
            # input mart); reliability = the woreda's HMIS reporting coverage;
            # readiness = its region's commodity readiness (supply data is
            # region-grain). LEFT joins give NULL where a signal is absent.
            f"pop AS (SELECT woreda_uid, max(population) AS pop "
            f"FROM bi_idsr.mart_ml_disease_input WHERE population IS NOT NULL GROUP BY 1), "
            f"rel AS (SELECT woreda_uid, CAST(round(avg(coverage_pct), 1) AS double) AS reliability "
            f"FROM bi_hmis.mart_hmis_indicator_coverage GROUP BY 1), "
            f"rdy AS (SELECT region_id, CAST(round(100.0 * count_if(mos >= 1) "
            f"/ NULLIF(count(*), 0), 1) AS double) AS readiness "
            f"FROM bi_supply.mart_supply_region_stock GROUP BY 1) "
            f"SELECT row_number() OVER (ORDER BY pr.p_alarm DESC NULLS LAST, recent.cases_12w DESC, recent.woreda_uid) AS rank, "
            f"recent.woreda, recent.region, recent.cases_12w, "
            f"CAST(round(1e5 * recent.cases_12w / NULLIF(pop.pop, 0), 1) AS double) AS incidence_100k, "
            f"round(100.0 * recent.deaths_12w / NULLIF(recent.cases_12w, 0), 2) AS cfr_pct, "
            f"round(pr.p_alarm, 3) AS p_alarm, "
            f"rdy.readiness, rel.reliability "
            f"FROM recent LEFT JOIN pr ON pr.district = recent.woreda_uid "
            f"LEFT JOIN pop ON pop.woreda_uid = recent.woreda_uid "
            f"LEFT JOIN rel ON rel.woreda_uid = recent.woreda_uid "
            f"LEFT JOIN rdy ON rdy.region_id = recent.region "
            f"ORDER BY rank LIMIT 12")),

    # internal-bi, disease climate: bi_idsr.mart_idsr_climate_drivers
    "dz-climate-ts": Fetcher(
        schema="bi_idsr", table="mart_idsr_climate_drivers",
        subject="PRG", data_class="DC.PRO",
        geo_col="region_id", prog_col="programme_id", period_col="week_start",
        sql=lambda t, w: (
            f"SELECT iso_week, week_start, region_id, "
            f"round(avg(rainfall_mm), 1) AS rainfall_mm, "
            f"round(avg(temp_celsius), 1) AS temp_celsius, "
            f"round(avg(humidity_pct), 1) AS humidity_pct, "
            f"CAST(sum(cases) AS bigint) AS cases "
            f"FROM {t}{w} GROUP BY 1, 2, 3 "
            f"ORDER BY region_id, week_start LIMIT 2000")),
    "dz-climate-corr": Fetcher(
        schema="bi_idsr", table="mart_idsr_climate_drivers",
        subject="PRG", data_class="DC.PRO",
        geo_col="region_id", prog_col="programme_id", period_col="week_start",
        sql=lambda t, w: (
            f"WITH b AS (SELECT * FROM {t}{w}), "
            f"r AS (SELECT *, row_number() OVER (PARTITION BY region_id, disease_label "
            f"ORDER BY week_start DESC) AS rn FROM b) "
            f"SELECT region_id, disease_label, "
            f"round(rainfall_lag_correlation, 3) AS rainfall_lag_correlation, lag_weeks "
            f"FROM r WHERE rn = 1 ORDER BY region_id, disease_label LIMIT 200")),

    # internal-bi, disease outbreaks: bi_idsr.mart_idsr_outbreaks
    # One row per detected outbreak (region by disease), active and contained.
    # Widget contract: id, disease, region, woreda, declared, days, total,
    # new_7d, cfr_pct, status, rrt, readiness. id, disease, region, declared,
    # days, total, cfr_pct and status come from the outbreaks mart. woreda (the
    # mart is region-grain), new_7d, rrt (no response-team feed) and readiness
    # (no readiness source) are NULL.
    "dz-outbreaks-table": Fetcher(
        schema="bi_idsr", table="mart_idsr_outbreaks",
        subject="PRG", data_class="DC.PRO", geo_col="region_id",
        period_col="declared_date",   # period = outbreaks declared in the window
        sql=lambda t, w: (
            f"SELECT outbreak_id AS id, disease_label AS disease, region_id AS region, "
            f"CAST(NULL AS varchar) AS woreda, declared_date AS declared, "
            f"CAST(weeks_active * 7 AS bigint) AS days, CAST(cases_total AS bigint) AS total, "
            f"CAST(NULL AS bigint) AS new_7d, round(cfr_pct, 1) AS cfr_pct, status, "
            f"CAST(NULL AS varchar) AS rrt, CAST(NULL AS varchar) AS readiness "
            f"FROM {t}{w} ORDER BY declared_date DESC, outbreak_id LIMIT 100")),

    # internal-bi, service: bi_hmis / bi_service
    # Clinical-guideline compliance (widget contract: kind, month, programme,
    # compliance_pct): coverage_pct per indicator, month and programme.
    "sv-compliance": Fetcher(
        schema="bi_hmis", table="mart_hmis_indicator_coverage",
        subject="PRG", data_class="DC.PRO",
        geo_col="region_id", prog_col="programme_id",
        period_col="month_id", period_kind="month",
        sql=lambda t, w: (
            f"SELECT indicator_id AS kind, CAST(month_id AS varchar) AS month, programme_id AS programme, "
            f"round(avg(coverage_pct), 1) AS compliance_pct "
            # Full GROUP BY key in the ORDER BY so indicator ties do not reshuffle.
            f"FROM {t}{w} GROUP BY indicator_id, month_id, programme_id "
            f"ORDER BY month_id, programme_id, indicator_id LIMIT 100")),
    # Flagship indicator attainment. target_pct and timeliness_pct are NULL:
    # the feed has no targets (none are invented) and no timeliness signal.
    # status comes from current_pct bands, confidence from reporting
    # completeness, mapping from the facility registry.
    "sv-flagship-table": Fetcher(
        schema="bi_hmis", table="mart_hmis_indicator_coverage",
        subject="PRG", data_class="DC.PRO",
        geo_col="region_id", prog_col="programme_id",
        period_col="month_id", period_kind="month",
        # Attainment is averaged over all months in the window, so it responds
        # to the period filter. Ordering is total (indicator and programme
        # tie-break) so ties do not reshuffle.
        sql=lambda t, w: (
            f"WITH b AS (SELECT * FROM {t}{w}), "
            f"a AS (SELECT indicator_id, programme_id, round(avg(coverage_pct), 1) AS current_pct, "
            f"round(100.0 * sum(facilities_reporting) / nullif(sum(facilities_expected), 0), 1) AS completeness_pct "
            f"FROM b GROUP BY 1, 2) "
            f"SELECT indicator_id AS indicator, programme_id AS programme, current_pct, "
            f"CAST(NULL AS double) AS target_pct, "
            f"CASE WHEN current_pct >= 80 THEN 'on_track' WHEN current_pct >= 60 THEN 'watch' ELSE 'at_risk' END AS status, "
            f"completeness_pct, CAST(NULL AS double) AS timeliness_pct, 'In registry' AS mapping_status, "
            f"CASE WHEN completeness_pct >= 90 THEN 'High' WHEN completeness_pct >= 70 THEN 'Medium' ELSE 'Low' END AS confidence_status "
            f"FROM a ORDER BY current_pct DESC, indicator, programme LIMIT 12")),
    # Cascade funnels (widget contract: stage, pct, with step 1 = 100%). The
    # retained % is each step's value_total divided by the first step's. The
    # programme is fixed per widget (prog_col=None): the widget is the
    # programme view, so only geography row filters apply.
    # mart_programme_cascade is year-grain (year_id), so period_kind='year':
    # sub-year windows coarsen to the anchor year and last_24mo to two years.
    # This keeps the funnels visible under the filter bar's default period
    # instead of hiding the cascade band.
    "sv-cascade-hiv": Fetcher(
        schema="bi_service", table="mart_programme_cascade",
        subject="PRG", data_class="DC.PRO", geo_col="region_id",
        period_col="year_id", period_kind="year",
        sql=lambda t, w: (
            f"WITH c AS (SELECT step_order, step_indicator AS stage, sum(value_total) AS v "
            f"FROM {t}{w} {'AND' if w else 'WHERE'} programme_id = 'HIV' GROUP BY 1, 2), "
            f"f AS (SELECT v AS first FROM c WHERE step_order = (SELECT min(step_order) FROM c)) "
            f"SELECT c.stage, round(100.0 * c.v / nullif((SELECT first FROM f), 0), 1) AS pct FROM c ORDER BY c.step_order")),
    "sv-cascade-tb": Fetcher(
        schema="bi_service", table="mart_programme_cascade",
        subject="PRG", data_class="DC.PRO", geo_col="region_id",
        period_col="year_id", period_kind="year",
        sql=lambda t, w: (
            f"WITH c AS (SELECT step_order, step_indicator AS stage, sum(value_total) AS v "
            f"FROM {t}{w} {'AND' if w else 'WHERE'} programme_id = 'TB' GROUP BY 1, 2), "
            f"f AS (SELECT v AS first FROM c WHERE step_order = (SELECT min(step_order) FROM c)) "
            f"SELECT c.stage, round(100.0 * c.v / nullif((SELECT first FROM f), 0), 1) AS pct FROM c ORDER BY c.step_order")),
    "sv-cascade-mnch": Fetcher(
        schema="bi_service", table="mart_programme_cascade",
        subject="PRG", data_class="DC.PRO", geo_col="region_id",
        period_col="year_id", period_kind="year",
        sql=lambda t, w: (
            f"WITH c AS (SELECT step_order, step_indicator AS stage, sum(value_total) AS v "
            f"FROM {t}{w} {'AND' if w else 'WHERE'} programme_id = 'MNCH' GROUP BY 1, 2), "
            f"f AS (SELECT v AS first FROM c WHERE step_order = (SELECT min(step_order) FROM c)) "
            f"SELECT c.stage, round(100.0 * c.v / nullif((SELECT first FROM f), 0), 1) AS pct FROM c ORDER BY c.step_order")),
    "sv-cascade-ncd": Fetcher(
        schema="bi_service", table="mart_programme_cascade",
        subject="PRG", data_class="DC.PRO", geo_col="region_id",
        period_col="year_id", period_kind="year",
        sql=lambda t, w: (
            f"WITH c AS (SELECT step_order, step_indicator AS stage, sum(value_total) AS v "
            f"FROM {t}{w} {'AND' if w else 'WHERE'} programme_id = 'NCD' GROUP BY 1, 2), "
            f"f AS (SELECT v AS first FROM c WHERE step_order = (SELECT min(step_order) FROM c)) "
            f"SELECT c.stage, round(100.0 * c.v / nullif((SELECT first FROM f), 0), 1) AS pct FROM c ORDER BY c.step_order")),

    # internal-bi, resource (supply): bi_supply.mart_resource_allocation
    # Month by commodity grain, national (no region column, so geo_col=None).
    # rm-hero: stockout-watch headline with commodities_at_risk (mos<3),
    # commodity_region_pairs and already_stockout/urgent (mos<1).
    # value_at_risk_etb, services_exposed and estimated_service_disruption are
    # NULL: the feed has no pricing or commodity-to-service linkage.
    # No period support: this is a current snapshot and every max-anchored
    # window contains the latest month, so a period filter cannot change it.
    # With supports.period=false the widget hides when the user narrows the
    # period rather than showing unchanged numbers.
    "rm-hero": Fetcher(
        schema="bi_supply", table="mart_resource_allocation",
        subject="PRD", data_class="DC.STK", prog_col="programme_id",
        commodity_col="commodity_name",
        sql=lambda t, w: (
            f"WITH b AS (SELECT * FROM {t}{w}), mx AS (SELECT max(month_id) AS m FROM b), "
            f"nat AS (SELECT count(*) FILTER (WHERE mos < 3) AS at_risk, count(*) FILTER (WHERE mos < 1) AS stockout "
            f"FROM b JOIN mx ON b.month_id = mx.m), "
            # rp inherits b's row filters (including the programme cap) via the
            # commodity subselect; a plain WHERE on region_stock would leak
            # out-of-scope pairs to programme-scoped users.
            f"rp AS (SELECT count(*) FILTER (WHERE mos < 3) AS pairs FROM bi_supply.mart_supply_region_stock s "
            f"WHERE s.month_id = (SELECT m FROM mx) "
            f"AND s.commodity_name IN (SELECT DISTINCT commodity_name FROM b)) "
            f"SELECT CAST(nat.at_risk AS bigint) AS commodities_at_risk, CAST(rp.pairs AS bigint) AS commodity_region_pairs, "
            f"CAST(nat.stockout AS bigint) AS already_stockout, CAST(NULL AS double) AS value_at_risk_etb, "
            f"CAST(NULL AS bigint) AS services_exposed, CAST(NULL AS double) AS estimated_service_disruption, "
            f"CAST(nat.stockout AS bigint) AS urgent_items FROM nat CROSS JOIN rp")),
    # rm-statboard: commodity-status metric strip (kind, label, value, unit,
    # target, delta_text, delta_good, spark). target, delta and spark are NULL
    # (no targets or prior period). No period support: it shows the latest
    # month's state, which a window cannot change.
    "rm-statboard": Fetcher(
        schema="bi_supply", table="mart_resource_allocation",
        subject="PRD", data_class="DC.STK", prog_col="programme_id",
        commodity_col="commodity_name",
        sql=lambda t, w: (
            f"WITH b AS (SELECT * FROM {t}{w}), mx AS (SELECT max(month_id) AS m FROM b), "
            f"a AS (SELECT round(100e0 * count(*) FILTER (WHERE mos >= 3) / nullif(count(*), 0), 1) AS avail, "
            f"round(avg(mos), 1) AS mos, count(*) FILTER (WHERE mos < 1) AS stockouts, "
            f"CAST(sum(wastage_units) AS bigint) AS wastage, CAST(sum(received) AS bigint) AS received "
            f"FROM b JOIN mx ON b.month_id = mx.m) "
            f"SELECT kind, label, value, unit, target, delta_text, delta_good, spark FROM ("
            f"SELECT 1 AS ord, 'availability' AS kind, 'Stock Availability' AS label, CAST(CAST((SELECT avail FROM a) AS decimal(8,1)) AS varchar) AS value, "
            f"'percent' AS unit, CAST(NULL AS varchar) AS target, CAST(NULL AS varchar) AS delta_text, true AS delta_good, CAST(NULL AS varchar) AS spark "
            f"UNION ALL SELECT 2, 'mos', 'Avg Months of Stock', CAST(CAST((SELECT mos FROM a) AS decimal(8,1)) AS varchar), 'months', NULL, NULL, true, NULL "
            f"UNION ALL SELECT 3, 'stockout', 'Commodities Stocked Out', CAST((SELECT stockouts FROM a) AS varchar), 'count', NULL, NULL, false, NULL "
            f"UNION ALL SELECT 4, 'wastage', 'Wastage Units', CAST((SELECT wastage FROM a) AS varchar), 'count', NULL, NULL, false, NULL "
            f") x ORDER BY ord")),
    # rm-forecast: days-to-stockout timeline (commodity, days_left, floor_days,
    # horizon_days) from the supply forecast mart. floor/horizon are the chart's
    # zero line and 90-day window. No period support: the mart is a single
    # as_of forward-looking snapshot, so a history window cannot change it.
    "rm-forecast": Fetcher(
        schema="bi_supply", table="mart_supply_stockout_forecast",
        subject="PRD", data_class="DC.STK", geo_col=None,
        commodity_col="commodity_name",
        sql=lambda t, w: (
            f"SELECT commodity_name AS commodity, CAST(projected_days_remaining AS bigint) AS days_left, "
            f"0 AS floor_days, 90 AS horizon_days FROM {t}{w} "
            f"ORDER BY projected_days_remaining, commodity_name LIMIT 12")),
    # rm-wastage (widget contract: commodity, wastage_score, use_band). Score =
    # % of received units wasted; band from the score.
    "rm-wastage": Fetcher(
        schema="bi_supply", table="mart_resource_allocation",
        subject="PRD", data_class="DC.STK", prog_col="programme_id",
        commodity_col="commodity_name",
        period_col="month_id", period_kind="month",
        sql=lambda t, w: (
            f"WITH b AS (SELECT commodity_name, sum(wastage_units) AS wu, sum(received) AS rc FROM {t}{w} GROUP BY 1) "
            f"SELECT commodity_name AS commodity, round(100.0 * wu / nullif(rc, 0), 1) AS wastage_score, "
            f"CASE WHEN 100.0 * wu / nullif(rc, 0) >= 10 THEN 'High' "
            f"WHEN 100.0 * wu / nullif(rc, 0) >= 3 THEN 'Moderate' ELSE 'Low' END AS use_band "
            f"FROM b WHERE rc > 0 ORDER BY wastage_score DESC, commodity LIMIT 14")),

    # internal-bi, resource (facilities): bi_facility.mart_facility_capacity
    # Facility grain (one row per MFR facility); aggregate with count(*).
    # Tiers in the data: HP/HC/PHC (primary care), PH/GH/SH (hospitals), MC/DS.
    # subject/data_class mirror the ops_perm.mart_attrs classification for
    # bi_facility_* (PRG/DC.PRF).
    # rm-hotspots (widget contract: rank, region, composite, exposed_services,
    # service_volume_month, trend). composite = % of a region's commodities under
    # stress (mos<3). exposed_services, service_volume_month and trend are NULL
    # (no commodity-to-service linkage). No period support, as for rm-map (same
    # mart, same latest-month stress metric). Rank uses a region tie-break so
    # composite ties do not reshuffle across calls.
    "rm-hotspots": Fetcher(
        schema="bi_supply", table="mart_supply_region_stock",
        subject="PRD", data_class="DC.STK", geo_col="region_id",
        sql=lambda t, w: (
            f"WITH b AS (SELECT * FROM {t}{w}), mx AS (SELECT max(month_id) AS m FROM b), "
            f"s AS (SELECT region_id, round(100.0 * count(*) FILTER (WHERE mos < 3) / nullif(count(*), 0), 1) AS composite "
            f"FROM b JOIN mx ON b.month_id = mx.m GROUP BY 1) "
            f"SELECT row_number() OVER (ORDER BY composite DESC NULLS LAST, region_id) AS rank, region_id AS region, composite, "
            f"CAST(NULL AS bigint) AS exposed_services, CAST(NULL AS bigint) AS service_volume_month, "
            f"CAST(NULL AS varchar) AS trend FROM s ORDER BY rank LIMIT 12")),
    # rm-map (widget contract: region, composite_score, drug_stress, facility_gap,
    # readiness_gap). drug_stress = composite_score = % of a region's commodities
    # under stress; facility_gap and readiness_gap are NULL (no readiness source).
    # No period support: a latest-month stress map that a window cannot change.
    "rm-map": Fetcher(
        schema="bi_supply", table="mart_supply_region_stock",
        subject="PRD", data_class="DC.STK", geo_col="region_id",
        sql=lambda t, w: (
            f"WITH b AS (SELECT * FROM {t}{w}), mx AS (SELECT max(month_id) AS m FROM b), "
            f"s AS (SELECT region_id, round(100.0 * count(*) FILTER (WHERE mos < 3) / nullif(count(*), 0), 1) AS drug_stress "
            f"FROM b JOIN mx ON b.month_id = mx.m GROUP BY 1) "
            f"SELECT region_id AS region, drug_stress AS composite_score, drug_stress, "
            f"CAST(NULL AS double) AS facility_gap, CAST(NULL AS double) AS readiness_gap FROM s ORDER BY region_id")),
    "rm-facilities": Fetcher(
        schema="bi_facility", table="mart_facility_capacity",
        subject="PRG", data_class="DC.PRF", geo_col="region_id",
        sql=lambda t, w: (
            f"SELECT region_id, facility_tier, count(*) AS n_fac "
            f"FROM {t}{w} GROUP BY region_id, facility_tier "
            f"ORDER BY region_id, facility_tier LIMIT 200")),

    # public: bi_public (deidentified aggregates only)
    "pub-hero": Fetcher(
        schema="bi_public", table="bi_facility_distribution_public",
        subject="PRG", data_class="DC.PRF", granularity="aggregate",
        geo_col="region_id",
        sql=lambda t, w: (
            f"SELECT count(*) AS regions, sum(n_facilities) AS facilities FROM {t}{w}")),
    # National indicator achievement rate by month with reference metadata.
    # Serves both the KPI tiles (latest and prior month per indicator, against
    # the HSDIP target) and the 24-month indicator trend. Region cells are
    # aggregated to national weighted by facilities reporting; the mart has
    # already applied k>=5 per cell.
    "pub-insights": Fetcher(
        schema="bi_public", table="mart_public_indicator_kpis",
        subject="PRG", data_class="DC.PRF", granularity="aggregate",
        geo_col="region_id", prog_col="programme_id",
        sql=lambda t, w: (
            f"SELECT indicator_id, kpi_label, kpi_order, kpi_kind, unit, definition, "
            f"hsdip_target, target_direction, target_source, month_id, "
            f"ROUND(SUM(rate_pct * facilities_reporting) "
            f"      / NULLIF(SUM(facilities_reporting), 0), 1) AS rate_pct, "
            f"SUM(facilities_reporting) AS facilities_reporting "
            f"FROM {t}{w} "
            f"GROUP BY indicator_id, kpi_label, kpi_order, kpi_kind, unit, "
            f"definition, hsdip_target, target_direction, target_source, month_id "
            f"ORDER BY kpi_order, month_id")),
    # Public service readiness: national commodity availability (k>=5 aggregate)
    # from the public supply stockout mart, plus facility distribution totals.
    "pub-supply": Fetcher(
        schema="bi_public", table="mart_supply_stockout_public",
        subject="PRG", data_class="DC.PRF", granularity="aggregate",
        sql=lambda t, w: (
            f"SELECT region_name, n_facilities, n_stocked, stocked_pct, "
            f"(SELECT sum(n_facilities) FROM bi_public.bi_facility_distribution_public) AS total_facilities, "
            f"(SELECT count(*) FROM bi_public.bi_facility_distribution_public) AS total_regions "
            f"FROM {t}{w}")),
    # Region by indicator (latest month) for the "coverage by region" equity bar,
    # coverage-kind indicators only. Region cells carry k>=5 from the mart.
    "pub-region": Fetcher(
        schema="bi_public", table="mart_public_indicator_kpis",
        subject="PRG", data_class="DC.PRF", granularity="aggregate",
        geo_col="region_id", prog_col="programme_id",
        sql=lambda t, w: (
            f"SELECT region_id, region_name, indicator_id, kpi_label, kpi_order, "
            f"rate_pct "
            f"FROM {t}{w} "
            f"{'AND' if w else 'WHERE'} kpi_kind = 'coverage' "
            f"AND month_id = (SELECT max(month_id) FROM {t}) "
            f"ORDER BY region_name, kpi_order")),
    # The admin DQ page has no fetchers here: it reads /gw/dq/* directly through
    # the single adm-dq widget.
}

# Zambia eLMIS supply: public widgets (pub-zm-supply, zm-v1)
# Separate widget ids: et-v3 disables these and zm-v1 disables the DHIS2
# coverage widgets, so the country switch is catalog configuration only. Same
# OPA attributes as pub-region so R-PUB-ANON can read them. All from the
# bi_supply eLMIS marts, with the same row shape as pub-insights/pub-region.
FETCHERS["pub-zm-kpi"] = Fetcher(
    schema="bi_supply", table="mart_pct_kpi",
    subject="PRG", data_class="DC.PRF", granularity="aggregate",
    geo_col="region_id", prog_col="programme_id",
    sql=lambda t, w: (
        f"WITH k AS (SELECT kpi_code, kpi_name, month_id, "
        f"sum(numerator) AS num, sum(denominator) AS den FROM {t}{w} "
        f"{'AND' if w else 'WHERE'} kpi_code IN "
        f"('osa_s8','order_fill','stockout_exposure','stockout_days_rate') "
        f"GROUP BY 1,2,3) "
        f"SELECT kpi_code AS indicator_id, kpi_name AS kpi_label, "
        f"CASE kpi_code WHEN 'osa_s8' THEN 1 WHEN 'order_fill' THEN 2 "
        f"WHEN 'stockout_exposure' THEN 3 ELSE 4 END AS kpi_order, "
        f"'coverage' AS kpi_kind, month_id, "
        f"round(100.0*num/NULLIF(den,0),1) AS rate_pct, "
        f"CASE kpi_code WHEN 'osa_s8' THEN '90' WHEN 'order_fill' THEN '90' "
        f"ELSE '' END AS hsdip_target, 'platform KPI' AS target_source, "
        f"CASE WHEN kpi_code LIKE 'stockout%' THEN 'lower_better' "
        f"ELSE 'higher_better' END AS target_direction "
        f"FROM k ORDER BY month_id, kpi_order"))

FETCHERS["pub-zm-province"] = Fetcher(
    schema="bi_supply", table="mart_pct_kpi",
    subject="PRG", data_class="DC.PRF", granularity="aggregate",
    geo_col="region_id", prog_col="programme_id",
    sql=lambda t, w: (
        f"SELECT region_name, kpi_name AS kpi_label, "
        f"round(100.0*sum(numerator)/NULLIF(sum(denominator),0),1) AS rate_pct "
        f"FROM {t}{w} "
        f"{'AND' if w else 'WHERE'} kpi_code = 'osa_s8' AND region_name IS NOT NULL "
        f"AND month_id = (SELECT max(month_id) FROM {t}) "
        f"GROUP BY region_name, kpi_name ORDER BY region_name"))


# Health workforce (MFR facility staffing to region density)
FETCHERS["rm-workforce"] = Fetcher(
    schema="bi_facility", table="mart_health_workforce",
    subject="PRG", data_class="DC.PRF", geo_col="region_id",
    sql=lambda t, w: (
        f"SELECT region_id, hcw_total, density_per_10k, phys, nurse, hew "
        f"FROM {t}{w} ORDER BY density_per_10k DESC NULLS LAST, region_id"))


# Equipment availability (MFR FHIR Device, national inventory by type)
# rm-equipment: working/broken/missing per equipment_type (stacked bar).
# National aggregate: no geo_col because the mart has no region dimension.
FETCHERS["rm-equipment"] = Fetcher(
    schema="bi_facility", table="mart_equipment_availability",
    subject="PRG", data_class="DC.PRF",
    sql=lambda t, w: (
        f"SELECT equipment_type, working, broken, missing "
        f"FROM {t}{w} ORDER BY total DESC, equipment_type"))


# EPI cold-chain safety (DHIS2 EPI indicators)
# rm-cold-chain: latest-month excursions and AEFI, national aggregate (widget
# contract: temperature_excursions, affected_batches, recipients_exposed,
# linked_aefi_events, batch_notes). affected_batches, recipients_exposed and
# batch_notes are NULL (no batch-level cold-chain feed). No period support: a
# max-anchored window always contains the latest month, so a period filter
# cannot change the snapshot.
FETCHERS["rm-cold-chain"] = Fetcher(
    schema="bi_hmis", table="mart_epi_cold_chain_safety",
    subject="PRG", data_class="DC.CAT",
    sql=lambda t, w: (
        f"WITH b AS (SELECT * FROM {t}{w}), mx AS (SELECT max(month_id) AS m FROM b) "
        f"SELECT CAST(b.excursions AS bigint) AS temperature_excursions, CAST(NULL AS bigint) AS affected_batches, "
        f"CAST(NULL AS bigint) AS recipients_exposed, CAST(b.aefi_total AS bigint) AS linked_aefi_events, "
        f"CAST(NULL AS varchar) AS batch_notes FROM b CROSS JOIN mx WHERE b.month_id = mx.m"))


# Region-grained supply (HCMIS EPSS hub mapped to region_id)
# rm-stock-heatmap: latest-month months-on-hand per commodity and region (the
# widget pivots these rows into a commodity by region heatmap). rm-events: one
# row per stockout episode. Both set geo_col=region_id, so the geography filter
# scopes them.
FETCHERS["rm-stock-heatmap"] = Fetcher(
    schema="bi_supply", table="mart_supply_region_stock",
    subject="PRD", data_class="DC.STK", geo_col="region_id", prog_col="programme_id",
    commodity_col="commodity_name",
    period_col="month_id", period_kind="month",
    sql=lambda t, w: (
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"mx AS (SELECT max(month_id) AS m FROM b) "
        f"SELECT b.commodity_name, b.region_id, round(b.mos, 1) AS mos "
        f"FROM b CROSS JOIN mx WHERE b.month_id = mx.m AND b.mos IS NOT NULL "
        f"ORDER BY b.commodity_name, b.region_id"))

FETCHERS["rm-events"] = Fetcher(
    schema="bi_supply", table="mart_supply_stockout_events",
    subject="PRD", data_class="DC.STK", geo_col="region_id", prog_col="programme_id",
    commodity_col="commodity_name",
    period_col="episode_start_month", period_kind="month",   # episodes starting in the window
    sql=lambda t, w: (
        f"SELECT region_id, commodity_name, episode_start_month, "
        f"months_out, days_out, severity, status "
        f"FROM {t}{w} ORDER BY episode_start_month DESC, region_id, commodity_name, episode_id LIMIT 50"))



# Overview digest widgets
# The overview page summarises each other page in a few widgets. Per-page
# digests reuse that page's fetcher (same mart, same OPA subject and scope), so
# no SQL is duplicated. Only the "national snapshot" band needs its own
# cross-mart query.
# ov-disease-pulse: overview mini-KPI (widget contract: cases_wk, alarms_now,
# active_diseases). It has its own fetcher because dz-kpi-strip returns the
# tile shape.
FETCHERS["ov-disease-pulse"] = Fetcher(
    schema="bi_idsr", table="mart_idsr_disease_weekly",
    subject="PRG", data_class="DC.PRO", geo_col="region_id", prog_col="programme_id",
    period_col="week_start",
    params_spec={"model": Param("ears", DZ_MODELS)},
    sql=lambda t, w, p: (
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"mx AS (SELECT max(week_start) AS m FROM b), "
        f"o AS (SELECT COALESCE(sum(new_cases) FILTER (WHERE week_start = mx.m), 0) AS cases_wk, "
        f"count(DISTINCT disease_label) FILTER (WHERE week_start = mx.m) AS active_diseases "
        f"FROM b CROSS JOIN mx), "
        f"scope AS (SELECT DISTINCT woreda_uid, disease_code FROM b), "
        f"ml AS ({_ml_latest_run(p['model'])}), "
        f"mlx AS (SELECT max(year * 100 + week) AS yw FROM ml), "
        f"pr AS (SELECT count(*) FILTER (WHERE m.alarm_flag = 1) AS alarms_now "
        f"FROM ml m CROSS JOIN mlx JOIN scope s ON s.woreda_uid = m.district "
        f"AND s.disease_code = m.disease WHERE m.year * 100 + m.week = mlx.yw) "
        f"SELECT o.cases_wk, pr.alarms_now, o.active_diseases FROM o CROSS JOIN pr"))

# internal-bi, Overview
# ov-disease-snapshot: new_cases in the window, alert_count and the top
# diseases currently in alarm.
FETCHERS["ov-disease-snapshot"] = Fetcher(
    schema="bi_idsr", table="mart_idsr_disease_weekly",
    subject="PRG", data_class="DC.PRO", geo_col="region_id", prog_col="programme_id",
    period_col="week_start",
    params_spec={"model": Param("ears", DZ_MODELS)},
    sql=lambda t, w, p: (
        # new_cases = cases in the period window, so it responds to the filter.
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"o AS (SELECT COALESCE(sum(new_cases), 0) AS new_cases FROM b), "
        f"lbl AS (SELECT disease_code, max(disease_label) AS dl FROM b GROUP BY 1), "
        f"scope AS (SELECT DISTINCT woreda_uid, disease_code FROM b), "
        f"ml AS ({_ml_latest_run(p['model'])}), "
        f"mlx AS (SELECT max(year * 100 + week) AS yw FROM ml), "
        # Per-disease peak alarm and the region where it peaks (max_by), returned
        # as the nested-array contract [[disease, region, pct]]. Trino arrays
        # serialise to JSON lists through the widget-data endpoint.
        f"worreg AS (SELECT DISTINCT woreda_uid, region_id FROM b), "
        f"rn AS (SELECT region_id, max(region_name) AS region_name "
        f"FROM bi_public.public_region_reference GROUP BY 1), "
        f"al AS (SELECT m.disease, max(m.alarm_flag) AS f, max(m.alarm_prob) AS ap, "
        f"max_by(COALESCE(rn.region_name, worreg.region_id), m.alarm_prob) AS region "
        f"FROM ml m CROSS JOIN mlx "
        f"JOIN scope s ON s.woreda_uid = m.district AND s.disease_code = m.disease "
        f"JOIN worreg ON worreg.woreda_uid = m.district "
        f"LEFT JOIN rn ON rn.region_id = worreg.region_id "
        f"WHERE m.year * 100 + m.week = mlx.yw GROUP BY 1), "
        f"flagged AS (SELECT al.*, lbl.dl FROM al JOIN lbl ON lbl.disease_code = al.disease WHERE al.f = 1), "
        # dl tie-break: alarm-prob ties must not reshuffle the top 3 across calls.
        f"top3 AS (SELECT dl, region, ap FROM flagged ORDER BY ap DESC, dl LIMIT 3), "
        f"tp AS (SELECT CAST((SELECT count(*) FROM flagged) AS bigint) AS alert_count, "
        f"(SELECT array_agg(array[dl, region, CAST(round(100 * ap) AS varchar)] ORDER BY ap DESC, dl) FROM top3) AS top_alerts) "
        f"SELECT o.new_cases, tp.alert_count, tp.top_alerts FROM o CROSS JOIN tp"))

# ov-trust-completeness: reporting completeness KPI (value, suffix, note,
# delta_text, delta_good, spark). value = share of known woredas reporting in
# the latest ISO week.
FETCHERS["ov-trust-completeness"] = Fetcher(
    schema="bi_idsr", table="mart_idsr_disease_weekly",
    subject="PRG", data_class="DC.PRO", geo_col="region_id", prog_col="programme_id",
    period_col="week_start",
    sql=lambda t, w: (
        # spark = per-week reporting completeness (share of known woredas
        # reporting in each of the last 6 ISO weeks); delta against the prior week.
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"tot AS (SELECT count(DISTINCT woreda_uid) AS n FROM b), "
        f"wk AS (SELECT iso_week, round(100.0 * count(DISTINCT woreda_uid) / max(tot.n)) AS pct "
        f"FROM b CROSS JOIN tot GROUP BY iso_week ORDER BY iso_week DESC LIMIT 6), "
        f"latest AS (SELECT pct FROM wk ORDER BY iso_week DESC LIMIT 1), "
        f"prior AS (SELECT pct FROM wk ORDER BY iso_week DESC OFFSET 1 LIMIT 1) "
        f"SELECT (SELECT pct FROM latest) AS value, '%' AS suffix, "
        f"'Woredas reporting, latest week' AS note, "
        f"(CASE WHEN (SELECT pct FROM latest) >= (SELECT pct FROM prior) THEN '▲ ' ELSE '▼ ' END) "
        f"|| CAST(abs((SELECT pct FROM latest) - (SELECT pct FROM prior)) AS varchar) || ' pp vs prior week' AS delta_text, "
        f"(SELECT pct FROM latest) >= (SELECT pct FROM prior) AS delta_good, "
        f"(SELECT array_agg(pct ORDER BY iso_week) FROM wk) AS spark"))

# ov-act-first-woredas: woredas prioritised by model alarm (rank, woreda,
# region, p_alarm). readiness_index and reporting_reliability_pct are NULL (no
# source mart); evidence is a short reason; action_priority comes from the
# alarm band.
FETCHERS["ov-act-first-woredas"] = Fetcher(
    schema="bi_idsr", table="mart_idsr_disease_weekly",
    subject="PRG", data_class="DC.PRO", geo_col="region_id", prog_col="programme_id",
    # No period support: the list ranks by the latest ML run's alarm
    # probability, which a period window cannot change, so the widget hides
    # when the period is narrowed.
    params_spec={"model": Param("ears", DZ_MODELS)},
    sql=lambda t, w, p: (
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"names AS (SELECT woreda_uid, max(woreda_name) AS woreda, max(region_id) AS region FROM b GROUP BY 1), "
        f"scope AS (SELECT DISTINCT woreda_uid, disease_code FROM b), "
        f"ml AS ({_ml_latest_run(p['model'])}), "
        f"mlx AS (SELECT max(year * 100 + week) AS yw FROM ml), "
        f"pr AS (SELECT m.district, max(m.alarm_prob) AS p_alarm, max_by(m.disease, m.alarm_prob) AS top_dis "
        f"FROM ml m CROSS JOIN mlx JOIN scope s ON s.woreda_uid = m.district AND s.disease_code = m.disease "
        f"WHERE m.year * 100 + m.week = mlx.yw GROUP BY 1) "
        # district tie-break: alarm-prob ties (many zeros) must not reshuffle ranks.
        f"SELECT row_number() OVER (ORDER BY pr.p_alarm DESC NULLS LAST, pr.district) AS rank, "
        f"n.woreda, n.region, round(pr.p_alarm, 3) AS p_alarm, "
        f"CAST(NULL AS double) AS readiness_index, CAST(NULL AS double) AS reporting_reliability_pct, "
        f"'Elevated ' || pr.top_dis || ' alarm' AS evidence, "
        f"CASE WHEN pr.p_alarm >= 0.8 THEN 'Immediate' WHEN pr.p_alarm >= 0.5 THEN 'This week' ELSE 'Monitor' END AS action_priority "
        f"FROM pr JOIN names n ON n.woreda_uid = pr.district ORDER BY rank LIMIT 8"))

# ov-benchmarks: 4 headline stat cards (icon, value, label, note) from the facility
# registry and workforce marts: facilities, population, regions, woredas covered.
FETCHERS["ov-benchmarks"] = Fetcher(
    schema="bi_facility", table="mart_facility_capacity",
    subject="PRG", data_class="DC.PRF", geo_col="region_id",
    sql=lambda t, w: (
        f"WITH f AS (SELECT * FROM {t}{w}), "
        f"fs AS (SELECT count(*) AS facilities, count(DISTINCT region_id) AS regions, "
        # 'Woredas covered' = distinct facility-parent org units in the DHIS2 path
        # (/root/region/zone/woreda/facility with the leaf stripped).
        # mart_facility_capacity has no woreda column, so the path is the woreda
        # signal. It follows the region filter because f is already narrowed.
        f"count(DISTINCT regexp_replace(dhis2_path, '/[^/]+$', '')) AS woredas FROM f), "
        # Population follows the region filter: sum only the regions present in
        # the region-filtered facility set, not the national total.
        f"pop AS (SELECT sum(population) AS population FROM bi_facility.mart_health_workforce "
        f"WHERE region_id IN (SELECT DISTINCT region_id FROM f)) "
        f"SELECT icon, value, label, note FROM ("
        f"SELECT 1 AS ord, 'building' AS icon, CAST((SELECT facilities FROM fs) AS varchar) AS value, "
        f"'Facilities' AS label, 'On the master registry' AS note "
        f"UNION ALL SELECT 2, 'users', CAST(CAST(round((SELECT population FROM pop) / 1e6, 1) AS decimal(8,1)) AS varchar) || 'M', 'Population', 'Catchment served' "
        f"UNION ALL SELECT 3, 'map', CAST((SELECT regions FROM fs) AS varchar), 'Regions', 'Administrative regions' "
        f"UNION ALL SELECT 4, 'map', CAST((SELECT woredas FROM fs) AS varchar), 'Woredas covered', 'Districts with a facility' "
        f") x ORDER BY ord"))

# ov-trust-mapping: facility-registry coverage (% facilities placed in the DHIS2 org
# hierarchy). GPS lat/long is not populated in the feed, so the registry path is
# the mapping signal. No geography support: dhis2_path coverage is 100% in every
# region, so a region filter cannot change the value; with
# supports.geography=false the widget hides under a region filter.
FETCHERS["ov-trust-mapping"] = Fetcher(
    schema="bi_facility", table="mart_facility_capacity",
    subject="PRG", data_class="DC.PRF",
    sql=lambda t, w: (
        f"WITH f AS (SELECT * FROM {t}{w}) "
        f"SELECT round(100.0 * count(*) FILTER (WHERE dhis2_path IS NOT NULL) / greatest(count(*), 1), 1) AS value, "
        f"'%' AS suffix, 'Facilities in the registry' AS note, CAST(NULL AS varchar) AS delta_text, "
        f"true AS delta_good, CAST(NULL AS varchar) AS spark FROM f"))

# ov-trust-silent: active-but-silent facilities (registered but not reporting).
FETCHERS["ov-trust-silent"] = Fetcher(
    schema="bi_facility", table="mart_facility_capacity",
    subject="PRG", data_class="DC.PRF", geo_col="region_id",
    sql=lambda t, w: (
        f"WITH f AS (SELECT * FROM {t}{w}) "
        f"SELECT CAST(count(*) FILTER (WHERE reporting_status = 'silent') AS varchar) AS value, "
        f"'' AS suffix, 'Active but not reporting' AS note, CAST(NULL AS varchar) AS delta_text, "
        f"false AS delta_good, CAST(NULL AS varchar) AS spark FROM f"))

# ov-reliability-table: per-region service-reliability composite. coverage_pct
# and coverage_confidence_pct (reporting completeness) from bi_hmis;
# commodity_readiness_pct (% commodities with >=1 month of stock) from bi_supply.
# composite = 0.40 * coverage + 0.35 * confidence + 0.25 * readiness.
# pop_weight_status is 'Unweighted' because no population weighting is applied.
FETCHERS["ov-reliability-table"] = Fetcher(
    schema="bi_hmis", table="mart_hmis_indicator_coverage",
    subject="PRG", data_class="DC.PRO", geo_col="region_id", prog_col="programme_id",
    period_col="month_id", period_kind="month",
    # Coverage is averaged over all months in the window. The supply-readiness
    # leg stays latest-month (a different mart and a current-state signal), so
    # the composite varies with the window through the coverage leg. Rank uses a
    # region tie-break so composite ties do not reshuffle.
    sql=lambda t, w: (
        f"WITH cov AS (SELECT * FROM {t}{w}), "
        f"cvr AS (SELECT region_id, round(avg(coverage_pct), 1) AS coverage_pct, "
        f"round(100.0 * sum(facilities_reporting) / nullif(sum(facilities_expected), 0), 1) AS conf "
        f"FROM cov GROUP BY 1), "
        f"smx AS (SELECT max(month_id) AS m FROM bi_supply.mart_supply_region_stock), "
        f"rdy AS (SELECT s.region_id, round(100.0 * count(*) FILTER (WHERE s.mos >= 1) / nullif(count(*), 0), 1) AS readiness "
        f"FROM bi_supply.mart_supply_region_stock s CROSS JOIN smx WHERE s.month_id = smx.m GROUP BY 1) "
        f"SELECT row_number() OVER (ORDER BY (COALESCE(cvr.coverage_pct, 0) * 0.40 + COALESCE(cvr.conf, 0) * 0.35 + COALESCE(rdy.readiness, 0) * 0.25) DESC, "
        f"COALESCE(cvr.region_id, rdy.region_id)) AS rank, "
        f"COALESCE(cvr.region_id, rdy.region_id) AS region, "
        f"round(COALESCE(cvr.coverage_pct, 0) * 0.40 + COALESCE(cvr.conf, 0) * 0.35 + COALESCE(rdy.readiness, 0) * 0.25, 1) AS composite_score, "
        f"cvr.coverage_pct, cvr.conf AS coverage_confidence_pct, rdy.readiness AS commodity_readiness_pct, "
        f"'Unweighted' AS pop_weight_status "
        f"FROM cvr FULL JOIN rdy ON rdy.region_id = cvr.region_id ORDER BY rank LIMIT 15"))

# internal-bi, Service
# sv-topline-kpis: four service headline tiles (metric, value, unit, subtitle,
# delta_text, target_text, icon). target_text and delta_text are NULL (no
# targets or prior period in the feed).
FETCHERS["sv-topline-kpis"] = Fetcher(
    schema="bi_hmis", table="mart_hmis_indicator_coverage",
    subject="PRG", data_class="DC.PRO", geo_col="region_id", prog_col="programme_id",
    period_col="month_id", period_kind="month",
    # Aggregates over all months in the period window rather than the latest
    # month: a max-anchored window always contains the latest month, so a
    # latest-month figure would be identical for every window. Facilities
    # Reporting is the monthly average over the window (a plain sum would
    # double-count facilities across months).
    sql=lambda t, w: (
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"a AS (SELECT round(avg(coverage_pct), 1) AS cov, "
        f"round(100.0 * sum(facilities_reporting) / nullif(sum(facilities_expected), 0), 1) AS comp, "
        f"CAST(round(sum(facilities_reporting) * 1.0 / nullif(count(DISTINCT month_id), 0)) AS bigint) AS fac, "
        f"count(DISTINCT indicator_id) AS inds FROM b) "
        f"SELECT metric, value, unit, subtitle, delta_text, target_text, icon FROM ("
        f"SELECT 1 AS ord, 'Avg Coverage' AS metric, CAST((SELECT cov FROM a) AS varchar) AS value, 'percent' AS unit, "
        f"'Across tracked indicators' AS subtitle, CAST(NULL AS varchar) AS delta_text, CAST(NULL AS varchar) AS target_text, 'activity' AS icon "
        f"UNION ALL SELECT 2, 'Reporting Completeness', CAST((SELECT comp FROM a) AS varchar), 'percent', 'Facilities reporting', CAST(NULL AS varchar), CAST(NULL AS varchar), 'check-circle' "
        f"UNION ALL SELECT 3, 'Facilities Reporting', CAST((SELECT fac FROM a) AS varchar), 'count', 'Monthly average', CAST(NULL AS varchar), CAST(NULL AS varchar), 'building' "
        f"UNION ALL SELECT 4, 'Indicators Tracked', CAST((SELECT inds FROM a) AS varchar), 'count', 'HMIS indicators', CAST(NULL AS varchar), CAST(NULL AS varchar), 'list' "
        f") x ORDER BY ord"))

# sv-continuity-mnch: MNCH continuum retention funnel (stage, retention_pct, scope)
# from the programme coverage view; scope is the fixed label 'EMR-active'.
FETCHERS["sv-continuity-mnch"] = Fetcher(
    schema="bi_programme", table="mnch_coverage_v",
    subject="PRG", data_class="DC.PRO", geo_col="region_code",
    sql=lambda t, w: (
        f"WITH a AS (SELECT round(avg(anc4), 1) AS anc4, round(avg(skilled_birth), 1) AS sb, "
        f"round(avg(postnatal), 1) AS pnc FROM {t}{w}) "
        f"SELECT stage, retention_pct, 'EMR-active' AS scope FROM ("
        f"SELECT 1 AS ord, 'ANC4+' AS stage, (SELECT anc4 FROM a) AS retention_pct "
        f"UNION ALL SELECT 2, 'Skilled birth', (SELECT sb FROM a) "
        f"UNION ALL SELECT 3, 'Postnatal care', (SELECT pnc FROM a) "
        f") x ORDER BY ord"))

# sv-dropoff-region: per-region MNCH drop-off decomposition (region, anc_dropout_pct,
# delivery_gap_pct, pnc_gap_pct, total_dropoff_pct): gaps between continuum stages.
FETCHERS["sv-dropoff-region"] = Fetcher(
    schema="bi_programme", table="mnch_coverage_v",
    subject="PRG", data_class="DC.PRO", geo_col="region_code",
    sql=lambda t, w: (
        f"SELECT region_code AS region, "
        f"round(100 - avg(anc4), 1) AS anc_dropout_pct, "
        f"round(avg(anc4) - avg(skilled_birth), 1) AS delivery_gap_pct, "
        f"round(avg(skilled_birth) - avg(postnatal), 1) AS pnc_gap_pct, "
        f"round(100 - avg(postnatal), 1) AS total_dropoff_pct "
        f"FROM {t}{w} GROUP BY region_code "
        f"ORDER BY total_dropoff_pct DESC, region_code LIMIT 15"))

# sv-zero-dose: unreached children against vaccine last-mile stress, from three
# feeds (DHIS2 immunization, DHIS2 population, mBrana cold-chain logs).
# expected_infants is a labelled estimate (population x 3.2% crude birth rate);
# hotspot = burden >=20% and mBrana zero-stock >=15%. Latest population year
# only (snapshot semantics).
FETCHERS["sv-zero-dose"] = Fetcher(
    schema="bi_hmis", table="mart_epi_zero_dose",
    subject="PRG", data_class="DC.PRO", geo_col="region_id",
    sql=lambda t, w: (
        f"WITH b AS (SELECT * FROM {t}{w}), mx AS (SELECT max(year_id) AS y FROM b) "
        f"SELECT b.region_id, b.year_id, CAST(b.expected_infants AS bigint) AS expected_infants, "
        f"CAST(b.penta1_annualized AS bigint) AS penta1_annualized, "
        f"b.reach_index, b.mbrana_zero_stock_pct, b.hotspot "
        f"FROM b JOIN mx ON b.year_id = mx.y "
        f"ORDER BY b.hotspot DESC, b.reach_index ASC NULLS LAST, b.region_id LIMIT 14"))

# sv-risk-woredas: composite woreda risk from coverage gap and reporting
# completeness. stockout_days is NULL (supply is region-grain, not woreda).
# risk_score = 0.6 * coverage_gap + 0.4 * (100 - completeness). Band thresholds
# follow DHIS2 reporting-rate norms (>=90% good, 80-90% watch, <80% poor):
# High >= 15 corresponds to blended reporting below about 82%, Medium >= 8 to
# below about 91%.
FETCHERS["sv-risk-woredas"] = Fetcher(
    schema="bi_hmis", table="mart_hmis_indicator_coverage",
    subject="PRG", data_class="DC.PRO", geo_col="region_id", prog_col="programme_id",
    period_col="month_id", period_kind="month",
    # Gaps are averaged over all months in the window. Ordering is total
    # (woreda_uid tie-break) so risk-score ties do not reshuffle the ranking.
    sql=lambda t, w: (
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"wz AS (SELECT woreda_uid, max(region_id) AS region, round(100 - avg(coverage_pct), 1) AS coverage_gap, "
        f"round(100.0 * sum(facilities_reporting) / nullif(sum(facilities_expected), 0), 1) AS completeness "
        # The mart carries woreda_name (resolved from the DHIS2 org-unit tree at
        # build time), so the widget shows display names, not surrogate uids.
        f"FROM b GROUP BY 1), "
        f"nm AS (SELECT woreda_uid, max(woreda_name) AS woreda_name FROM b GROUP BY 1) "
        f"SELECT COALESCE(nm.woreda_name, wz.woreda_uid) AS woreda, region, coverage_gap AS coverage_gap_pct, "
        f"CAST(NULL AS bigint) AS stockout_days, completeness AS reporting_completeness_pct, "
        f"round(coverage_gap * 0.6 + (100 - completeness) * 0.4, 1) AS risk_score, "
        f"CASE WHEN coverage_gap * 0.6 + (100 - completeness) * 0.4 >= 15 THEN 'High' "
        f"WHEN coverage_gap * 0.6 + (100 - completeness) * 0.4 >= 8 THEN 'Medium' ELSE 'Low' END AS risk_band "
        f"FROM wz LEFT JOIN nm ON nm.woreda_uid = wz.woreda_uid "
        f"ORDER BY risk_score DESC, wz.woreda_uid LIMIT 12"))

# sv-facility-outliers: per-facility observation outliers from the EMR feed:
# facility observation counts against the cross-facility mean, flagged by
# z-distance. Only aggregated counts are returned (hence DC.PRO); no patient
# rows leave the query.
FETCHERS["sv-facility-outliers"] = Fetcher(
    schema="bi_staging", table="bi_demo_patient_observations",
    subject="PRG", data_class="DC.PRO", geo_col=None,
    period_col="encounter_date",
    sql=lambda t, w: (
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"fc AS (SELECT facility_code, observation, count(*) AS value FROM b GROUP BY 1, 2), "
        f"ex AS (SELECT observation, avg(value) AS expected, stddev_pop(value) AS sd FROM fc GROUP BY 1) "
        f"SELECT fc.facility_code AS facility, fc.observation AS indicator, CAST(fc.value AS bigint) AS value, "
        f"CAST(round(ex.expected, 0) AS bigint) AS expected, "
        f"CASE WHEN ex.sd > 0 AND abs(fc.value - ex.expected) / ex.sd >= 2 THEN 'high' "
        f"WHEN ex.sd > 0 AND abs(fc.value - ex.expected) / ex.sd >= 1 THEN 'moderate' ELSE 'low' END AS severity "
        f"FROM fc JOIN ex ON ex.observation = fc.observation "
        f"WHERE ex.sd > 0 AND abs(fc.value - ex.expected) / ex.sd >= 1 "
        f"ORDER BY abs(fc.value - ex.expected) / ex.sd DESC, fc.facility_code, fc.observation LIMIT 8"))

# sv-agreement-donut and sv-data-trust-table: DHIS2 vs EMR agreement from the
# reconciliation mart mart_reported_vs_recorded (region by indicator by month).
# The EMR facilities are not in the DHIS2 org tree, so region is the
# comparison grain.
FETCHERS["sv-agreement-donut"] = Fetcher(
    schema="bi_service", table="mart_reported_vs_recorded",
    subject="PRG", data_class="DC.PRO", geo_col="region_id",
    period_col="month_id", period_kind="month",
    sql=lambda t, w: (
        f"WITH b AS (SELECT * FROM {t}{w}), mx AS (SELECT max(month_id) AS m FROM b), "
        f"cur AS (SELECT agreement_pct FROM b JOIN mx ON b.month_id = mx.m), "
        f"prev AS (SELECT avg(agreement_pct) AS a FROM b JOIN mx "
        f"ON b.month_id = CAST(date_format(date_add('month', -1, date_parse(mx.m || '-01', '%Y-%m-%d')), '%Y-%m') AS varchar)) "
        f"SELECT round(avg(agreement_pct), 0) AS overall_agreement_pct, "
        f"round(100.0 * count_if(agreement_pct >= 80) / nullif(count(*), 0), 0) AS agree_pct, "
        f"round(100.0 * count_if(agreement_pct >= 50 AND agreement_pct < 80) / nullif(count(*), 0), 0) AS moderate_pct, "
        f"round(100.0 * count_if(agreement_pct < 50) / nullif(count(*), 0), 0) AS low_pct, "
        f"round(avg(agreement_pct) - (SELECT a FROM prev), 1) AS delta_pp "
        f"FROM cur"))
# sv-rr-timeliness: measured reporting timeliness from the eLMIS archive, the
# only source that records when each report arrived. No geo_col: the mart is
# month by programme with no geography, so a scoped user sees the national
# figure rather than an empty table (the widget is national by design).
FETCHERS["sv-rr-timeliness"] = Fetcher(
    schema="bi_supply", table="mart_elmis_hist_timeliness",
    subject="PRD", data_class="DC.STK",
    period_col="month_id", period_kind="month",
    sql=lambda t, w: (
        f"SELECT month_id, programme_name, requisitions, on_time_requisitions, "
        f"on_time_pct, median_lag_days FROM {t}{w} "
        f"ORDER BY month_id DESC, programme_name"))

FETCHERS["sv-data-trust-table"] = Fetcher(
    schema="bi_service", table="mart_reported_vs_recorded",
    subject="PRG", data_class="DC.PRO", geo_col="region_id",
    period_col="month_id", period_kind="month",
    # Agreement is averaged per region, indicator and observation over all
    # months in the window. Ordering is total (region, indicator, observation
    # tie-breaks after the agreement sort).
    sql=lambda t, w: (
        f"WITH b AS (SELECT * FROM {t}{w}), "
        f"agg AS (SELECT region_id, indicator_id, observation, "
        f"round(avg(agreement_pct), 1) AS agreement, "
        f"round(avg(dhis2_rate_per_facility)) AS dhis2_rate, "
        f"round(avg(emr_rate_per_facility)) AS emr_rate "
        f"FROM b GROUP BY 1, 2, 3), "
        # Region display names from the public reference (id to name).
        f"rn AS (SELECT region_id, max(region_name) AS region_name "
        f"FROM bi_public.public_region_reference GROUP BY 1) "
        f"SELECT COALESCE(rn.region_name, agg.region_id) AS facility, "
        f"agg.observation || ' vs ' || agg.indicator_id AS indicator, "
        f"agg.agreement AS agreement_ratio_pct, "
        f"CAST(agg.dhis2_rate AS bigint) AS expected_dhis2, "
        f"CAST(agg.emr_rate AS bigint) AS actual_emr, "
        f"CAST(agg.emr_rate - agg.dhis2_rate AS bigint) AS difference, "
        f"CASE WHEN agg.agreement < 60 THEN 'Alert' "
        f"WHEN agg.agreement < 80 THEN 'Watch' ELSE 'OK' END AS severity "
        f"FROM agg LEFT JOIN rn ON rn.region_id = agg.region_id "
        f"ORDER BY agg.agreement ASC NULLS LAST, agg.region_id, agg.indicator_id, agg.observation LIMIT 10"))

# rm-act-week: cross-domain join of IDSR outbreak pressure (4-week cases and
# trend) with response-commodity stock (catchment-blended latest-month mos),
# through the disease_response_map seed, ranked by act_score (cases by supply
# severity). supply_status 'no_countermeasure' means no tracked commodity
# answers that disease. Snapshot semantics like rm-hero (latest 4 IDSR weeks,
# latest stock month), so no period_col; geography row filters apply.
FETCHERS["rm-act-week"] = Fetcher(
    schema="bi_supply", table="mart_supply_outbreak_response",
    subject="PRD", data_class="DC.STK", geo_col="region_id", prog_col="programme_id",
    sql=lambda t, w: (
        f"SELECT region_id, disease_code, disease_label, CAST(cases_4w AS bigint) AS cases_4w, "
        f"trend_ratio, commodity_name, commodity_role, mos, supply_status, act_score "
        f"FROM {t}{w} ORDER BY act_score DESC, region_id, disease_code, commodity_name LIMIT 24"))

# rm-stockout-impact: stockout burden in services (mart_stockout_service_impact:
# days_out x the enabled indicator's monthly regional volume / 30; the
# commodity-to-service link comes from commodity_reference.enables_indicator).
# estimated_missed_services is a derived estimate, or NULL where there is no
# link. facilities_affected and trend are NULL (no facility grain, no prior
# window). Snapshot semantics: the mart holds a fixed 12-month window.
FETCHERS["rm-stockout-impact"] = Fetcher(
    schema="bi_supply", table="mart_stockout_service_impact",
    subject="PRD", data_class="DC.STK", geo_col="region_id", prog_col="programme_id",
    sql=lambda t, w: (
        f"SELECT commodity_name AS commodity, region_id AS region, CAST(days_out_12m AS bigint) AS days_out_ytd, "
        f"CAST(NULL AS bigint) AS facilities_affected, CAST(est_missed_services AS bigint) AS estimated_missed_services, "
        f"service_disruption_pct, CAST(NULL AS varchar) AS trend "
        f"FROM {t}{w} ORDER BY est_missed_services DESC NULLS LAST, days_out_ytd DESC, commodity, region LIMIT 10"))
# ov-disease-outbreaks: overview mini-list (widget contract: disease_label,
# region_id, cases_total, status). Separate from dz-outbreaks-table, which
# returns the fuller disease-page columns.
FETCHERS["ov-disease-outbreaks"] = Fetcher(
    schema="bi_idsr", table="mart_idsr_outbreaks",
    subject="PRG", data_class="DC.PRO", geo_col="region_id",
    period_col="declared_date",
    sql=lambda t, w: (
        f"SELECT disease_label, region_id, CAST(cases_total AS bigint) AS cases_total, status "
        f"FROM {t}{w} ORDER BY cases_total DESC, outbreak_id LIMIT 30"))
FETCHERS["ov-resource-status"]     = FETCHERS["rm-hero"]
FETCHERS["ov-resource-facilities"] = FETCHERS["rm-hotspots"]
FETCHERS["ov-service-attainment"]  = FETCHERS["sv-flagship-table"]
FETCHERS["ov-service-cascade"]     = FETCHERS["sv-compliance"]

# ov-source-pulse: per-source trust pulse from mart_data_quality ("which feeds
# can I trust today"). Completeness is graded against the reporting norms in
# the requirements (>=90 ok, 80-90 watch, <80 poor). Sources without a computed
# metric (EWARS, climate) show no_metric. With geo_col set, a region-scoped
# user sees their own region's source trust.
FETCHERS["ov-source-pulse"] = Fetcher(
    schema="bi_quality", table="mart_data_quality",
    subject="PRG", data_class="DC.PRO", geo_col="region_id",
    sql=lambda t, w: (
        f"SELECT source, "
        f"round(avg(completeness_pct), 1) AS completeness_pct, "
        f"CAST(sum(reports_received) AS bigint) AS reports_received, "
        f"CAST(sum(reports_expected) AS bigint) AS reports_expected, "
        f"max(period_id) AS latest_period, "
        f"CASE WHEN avg(completeness_pct) IS NULL THEN 'no_metric' "
        f"WHEN avg(completeness_pct) >= 90 THEN 'ok' "
        f"WHEN avg(completeness_pct) >= 80 THEN 'watch' ELSE 'poor' END AS status "
        f"FROM {t}{w} GROUP BY source ORDER BY completeness_pct NULLS LAST, source"))

# ov-brief: the policy-maker decision brief, one cross-domain summary: top
# outbreak-supply action, active outbreaks, consumption-weighted supply
# availability, national reporting completeness and the weakest programme
# cascade. Every line derives from an existing mart; severity drives the chip.
FETCHERS["ov-brief"] = Fetcher(
    schema="bi_supply", table="mart_supply_outbreak_response",
    subject="PRG", data_class="DC.CAT",
    sql=lambda t, w: (
        "SELECT kind, headline, severity FROM ("
        "SELECT 1 AS ord, 'act' AS kind, "
        "(SELECT 'Act first: ' || coalesce(rr.region_name, o.region_id) || ' - ' || o.disease_label || ', ' || CAST(CAST(o.cases_4w AS bigint) AS varchar) || ' cases/4w; ' || o.commodity_name || ' at ' || format('%.1f', o.mos) || ' months' "
        " FROM marts.bi_supply.mart_supply_outbreak_response o LEFT JOIN marts.bi_public.public_region_reference rr ON rr.region_id = o.region_id "
        " WHERE o.supply_status IN ('stockout','critical','tight') ORDER BY o.act_score DESC LIMIT 1) AS headline, "
        "(SELECT supply_status FROM marts.bi_supply.mart_supply_outbreak_response WHERE supply_status IN ('stockout','critical','tight') ORDER BY act_score DESC LIMIT 1) AS severity "
        "UNION ALL SELECT 2, 'surveillance', "
        "(SELECT 'Active outbreaks: ' || CAST(count(*) AS varchar) || ' under response' FROM marts.bi_idsr.mart_idsr_outbreaks WHERE status = 'active'), "
        "(SELECT CASE WHEN count(*) > 5 THEN 'critical' WHEN count(*) > 0 THEN 'tight' ELSE 'ok' END FROM marts.bi_idsr.mart_idsr_outbreaks WHERE status = 'active') "
        "UNION ALL SELECT 3, 'supply', "
        "(SELECT 'Supply: ' || CAST(CAST(round(100.0 * sum(CASE WHEN mos >= 3 THEN amc ELSE 0 END) / nullif(sum(amc), 0), 0) AS bigint) AS varchar) || '% of consumption volume covered >=3 months; ' || CAST(count(*) FILTER (WHERE mos < 1) AS varchar) || ' commodities critical' "
        " FROM marts.bi_supply.mart_resource_allocation WHERE month_id = (SELECT max(month_id) FROM marts.bi_supply.mart_resource_allocation)), "
        "(SELECT CASE WHEN count(*) FILTER (WHERE mos < 1) > 0 THEN 'critical' WHEN count(*) FILTER (WHERE mos < 3) > 3 THEN 'tight' ELSE 'ok' END "
        " FROM marts.bi_supply.mart_resource_allocation WHERE month_id = (SELECT max(month_id) FROM marts.bi_supply.mart_resource_allocation)) "
        "UNION ALL SELECT 4, 'trust', "
        "(SELECT 'Reporting: ' || format('%.1f', 100.0 * sum(facilities_reporting) / nullif(sum(facilities_expected), 0)) || '% national completeness' "
        " FROM marts.bi_hmis.mart_hmis_indicator_coverage), "
        "(SELECT CASE WHEN 100.0 * sum(facilities_reporting) / nullif(sum(facilities_expected), 0) >= 90 THEN 'ok' "
        "        WHEN 100.0 * sum(facilities_reporting) / nullif(sum(facilities_expected), 0) >= 80 THEN 'tight' ELSE 'critical' END "
        " FROM marts.bi_hmis.mart_hmis_indicator_coverage) "
        "UNION ALL SELECT 5, 'service', "
        "(SELECT 'Weakest cascade: ' || programme_id || ' retains ' || format('%.1f', min_ret) || '% of entry cohort' FROM ("
        "  SELECT programme_id, min(retention_pct_vs_entry) AS min_ret FROM marts.bi_service.mart_programme_cascade "
        "  WHERE year_id = (SELECT max(year_id) FROM marts.bi_service.mart_programme_cascade) GROUP BY programme_id) x ORDER BY min_ret ASC LIMIT 1), "
        "'info' "
        ") b WHERE headline IS NOT NULL ORDER BY ord"))

# ov-national-kpis: national snapshot, one row of headline numbers across the
# four domains. National aggregate (DC.CAT, no geography or programme scope):
# it is the context header, not scoped detail. It uses period_param rather
# than period_col: each figure windows its own mart's time column (outbreaks
# flagged in the window, regions reporting in the window, coverage over the
# window's months). Supply stays latest-month, which every max-anchored window
# contains; facilities is a registry count with no time dimension. The header
# therefore responds to the period filter instead of hiding under the filter
# bar's default period.
FETCHERS["ov-national-kpis"] = Fetcher(
    schema="bi_idsr", table="mart_idsr_outbreaks",
    subject="PRG", data_class="DC.CAT", period_param=True,
    sql=lambda t, w, period=None: (
        "SELECT "
        "(SELECT count(*) FROM marts.bi_idsr.mart_idsr_outbreaks WHERE status = 'active'"
        + _and_period(period, "last_flagged_date", "date", "marts.bi_idsr.mart_idsr_outbreaks")
        + ") AS active_outbreaks, "
        "(SELECT count(DISTINCT region_id) FROM marts.bi_idsr.mart_idsr_disease_weekly"
        + _where_period(period, "week_start", "date", "marts.bi_idsr.mart_idsr_disease_weekly")
        + ") AS regions_reporting, "
        "(SELECT count(*) FROM marts.bi_facility.mart_facility_capacity) AS facilities, "
        # Supply availability weighted by consumption (amc): the share of
        # consumption volume with >=3 months of stock. An equal-weight commodity
        # average would count a low-volume reagent the same as amoxicillin.
        "(SELECT round(100.0 * sum(CASE WHEN mos >= 3 THEN amc ELSE 0 END) / nullif(sum(amc), 0), 0) "
        " FROM marts.bi_supply.mart_resource_allocation "
        " WHERE month_id = (SELECT max(month_id) FROM marts.bi_supply.mart_resource_allocation"
        + _where_period(period, "month_id", "month", "marts.bi_supply.mart_resource_allocation")
        + ")) AS supply_avail_pct, "
        # Coverage is population-weighted: region coverage is a ratio of sums (so
        # a small woreda does not count as much as Addis Ababa), then regions are
        # blended by population from mart_health_workforce.
        "(SELECT round(sum(rc.cov * p.pop) / nullif(sum(p.pop), 0), 1) FROM "
        " (SELECT region_id, 100.0 * sum(facilities_reporting) / nullif(sum(facilities_expected), 0) AS cov "
        "  FROM marts.bi_hmis.mart_hmis_indicator_coverage"
        + _where_period(period, "month_id", "month", "marts.bi_hmis.mart_hmis_indicator_coverage")
        + "  GROUP BY region_id) rc "
        " JOIN (SELECT region_id, sum(population) AS pop "
        "       FROM marts.bi_facility.mart_health_workforce GROUP BY region_id) p "
        " ON p.region_id = rc.region_id) AS avg_coverage"))



# Widgets whose fetcher reads more than its declared mart. The OPA decision and
# the masking obligation come from the declared (first) mart only, so every
# other mart listed must carry the same access attributes.
# bundle/scripts/test_gen_exposures.py enforces this; gen-exposures.py reads the
# map for lineage.
WIDGET_MARTS: dict[str, list[tuple[str, str]]] = {}

def widget_supports(widget_id: str) -> dict[str, bool] | None:
    """supports{} for the resolved dashboard spec: the global-filter dims the
    widget's bound mart can apply. None for unbound widgets: the spec omits the
    key and the host treats missing supports as never-hide. Call after all
    FETCHERS entries exist (aliases such as ov-resource-status, which shares
    rm-hero's Fetcher object, resolve through the shared object)."""
    fetcher = FETCHERS.get(widget_id)
    return fetcher.supports() if fetcher is not None else None
