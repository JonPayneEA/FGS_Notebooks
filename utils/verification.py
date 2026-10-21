# =============================================================================
# utils/verification.py
# Pure pandas: line each constituency's FGS forecast up against the flood alerts and
# warnings that were actually in force. No Spark, so it can be tested without
# Databricks. Used by jobs/26_fgs_forecast_verification.
#
# THE QUESTION. For a constituency and a date, the forecast made LEAD DAYS earlier
# said some risk (rank 0 to 4). Was an alert or warning in force in the constituency
# on that date? Lead 1 is the statement issued on the day itself, lead 5 the one
# issued four days before.
#
# WHAT "IN FORCE ON A DATE" MEANS. An alert or warning counts for a date if it was in
# force at ANY point in that date (UTC), so one that runs over three days counts on
# all three. Areas are counted once per constituency and date, however many times they
# were issued. An episode open for over 30 days (stale_open) most likely never
# received its "no longer in force" message, so it counts only on the date it began.
# Dates up to yesterday are scored; today is still under way.
#
# SPARSE ON PURPOSE. A row exists only where something was forecast (rank 1 or more)
# or something was in force. A quiet date with nothing forecast has no row, which is
# what makes a hit rate and a precision possible without a row for every
# constituency, date and lead. "Did not happen and was not forecast" is therefore not
# counted, and nothing here can say how often the forecast was right to stay quiet.
#
# THE WARNINGS ARE NOT THE FLOODING. This scores the forecast against the Environment
# Agency's alerts and warnings, which are themselves decisions. It is not a record of
# observed flooding.
# =============================================================================

import pandas as pd

LEVEL_ALERT, LEVEL_WARNING, LEVEL_SEVERE = "flood_alert", "flood_warning", "severe_flood_warning"
KINDS = (LEVEL_ALERT, LEVEL_WARNING, LEVEL_SEVERE)
LEADS = range(1, 6)

OUTPUT_COLUMNS = ["constituency_id", "forecast_date", "lead_day", "forecast_rank", "alerts", "warnings", "severe"]


def _day(series: pd.Series) -> pd.Series:
    """Midnight-normalised, timezone-free timestamps."""
    s = pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)
    return s.dt.normalize()


def spans_from_episodes(episodes: pd.DataFrame, periods: pd.DataFrame, now) -> pd.DataFrame:
    """
    The spans to count, one row per alert, warning or severe period: ea_area_code, kind,
    start_day, end_day. An alert or warning episode is a span of kind flood_alert or
    flood_warning; each severe period is a span of kind severe_flood_warning. A span with
    no end runs to `now`, except one from an episode open for over 30 days, which counts
    only on its start date.
    """
    now_day = pd.Timestamp(now).tz_localize(None).normalize() if pd.Timestamp(now).tzinfo else pd.Timestamp(now).normalize()
    out = []
    if episodes is not None and len(episodes):
        e = episodes[episodes["area_type"].isin([LEVEL_ALERT, LEVEL_WARNING])].copy()
        stale = e["stale_open"].fillna(False).astype(bool) if "stale_open" in e else pd.Series(False, index=e.index)
        start, end = _day(e["start_time"]), _day(e["end_time"])
        end = end.where(end.notna(), now_day)
        end = end.where(~(e["end_time"].isna() & stale), start)
        out.append(pd.DataFrame({"ea_area_code": e["ea_area_code"].values, "kind": e["area_type"].values,
                                 "start_day": start.values, "end_day": end.values}))
    if periods is not None and len(periods):
        p = periods[periods["level"] == LEVEL_SEVERE].copy()
        stale_ids = set()
        if episodes is not None and len(episodes) and "stale_open" in episodes:
            stale_ids = set(episodes.loc[episodes["stale_open"].fillna(False).astype(bool), "episode_id"])
        start, end = _day(p["start_time"]), _day(p["end_time"])
        end = end.where(end.notna(), now_day)
        capped = p["end_time"].isna() & p["episode_id"].isin(stale_ids)
        end = end.where(~capped, start)
        out.append(pd.DataFrame({"ea_area_code": p["ea_area_code"].values, "kind": LEVEL_SEVERE,
                                 "start_day": start.values, "end_day": end.values}))
    if not out:
        return pd.DataFrame(columns=["ea_area_code", "kind", "start_day", "end_day"])
    spans = pd.concat(out, ignore_index=True)
    spans = spans.dropna(subset=["ea_area_code", "start_day", "end_day"])
    spans["end_day"] = spans[["start_day", "end_day"]].max(axis=1)      # never ends before it starts
    return spans


def daily_in_force(spans: pd.DataFrame, links: pd.DataFrame, last_day) -> pd.DataFrame:
    """
    Areas in force per constituency, date and kind: constituency_id, date, alerts, warnings,
    severe. `links` is ea_area_code, constituency_id (the areas linked to each constituency).
    Dates after last_day are dropped.
    """
    cols = ["constituency_id", "date", "alerts", "warnings", "severe"]
    if spans is None or len(spans) == 0 or links is None or len(links) == 0:
        return pd.DataFrame(columns=cols)
    last = pd.Timestamp(last_day).normalize()
    s = spans.copy()
    s["end_day"] = s["end_day"].clip(upper=last)
    s = s[s["start_day"] <= s["end_day"]]
    if s.empty:
        return pd.DataFrame(columns=cols)
    n_days = ((s["end_day"] - s["start_day"]).dt.days + 1).astype(int)
    s = s.loc[s.index.repeat(n_days)].copy()
    s["date"] = s["start_day"] + pd.to_timedelta(s.groupby(level=0).cumcount(), unit="D")
    s = s[["ea_area_code", "kind", "date"]].drop_duplicates()
    m = s.merge(links[["ea_area_code", "constituency_id"]].drop_duplicates(), on="ea_area_code")
    g = m.groupby(["constituency_id", "date", "kind"])["ea_area_code"].nunique().unstack("kind", fill_value=0)
    for kind in KINDS:
        if kind not in g.columns:
            g[kind] = 0
    g = g.rename(columns={LEVEL_ALERT: "alerts", LEVEL_WARNING: "warnings", LEVEL_SEVERE: "severe"})
    return g[["alerts", "warnings", "severe"]].astype(int).reset_index()[cols]


def build_verification(forecast: pd.DataFrame, coverage: pd.DataFrame, outcomes: pd.DataFrame, last_day) -> pd.DataFrame:
    """
    One row per constituency, forecast date and lead day where something was forecast or
    was in force.

    Args:
        forecast:  constituency_id, forecast_date, lead_day, forecast_rank for forecasts of
                   rank 1 or more (the worst across flood sources).
        coverage:  forecast_date, lead_day for every date and lead a statement exists for, so
                   a date with no statement at that lead is not scored as a miss.
        outcomes:  daily_in_force() output.
        last_day:  the last date to score (yesterday).
    """
    last = pd.Timestamp(last_day).normalize()
    f = forecast.copy()
    f["forecast_date"] = pd.to_datetime(f["forecast_date"]).dt.normalize()
    f["lead_day"] = f["lead_day"].astype(int)
    f = f[(f["forecast_date"] <= last) & (f["lead_day"].isin(list(LEADS)))]
    f = f.groupby(["constituency_id", "forecast_date", "lead_day"], as_index=False)["forecast_rank"].max()

    cov = coverage.copy()
    cov["forecast_date"] = pd.to_datetime(cov["forecast_date"]).dt.normalize()
    cov["lead_day"] = cov["lead_day"].astype(int)
    cov = cov.drop_duplicates()

    o = outcomes.rename(columns={"date": "forecast_date"}).copy()
    o["forecast_date"] = pd.to_datetime(o["forecast_date"]).dt.normalize()
    o = o[(o["forecast_date"] <= last) & ((o["alerts"] > 0) | (o["warnings"] > 0) | (o["severe"] > 0))]
    # An outcome is scored at every lead a statement exists for on that date.
    o_leads = o.merge(cov, on="forecast_date")

    keys = pd.concat([f[["constituency_id", "forecast_date", "lead_day"]],
                      o_leads[["constituency_id", "forecast_date", "lead_day"]]]).drop_duplicates()
    out = keys.merge(f, on=["constituency_id", "forecast_date", "lead_day"], how="left")
    out = out.merge(o[["constituency_id", "forecast_date", "alerts", "warnings", "severe"]],
                    on=["constituency_id", "forecast_date"], how="left")
    out["forecast_rank"] = out["forecast_rank"].fillna(0).astype(int)
    for c in ("alerts", "warnings", "severe"):
        out[c] = out[c].fillna(0).astype(int)
    out["lead_day"] = out["lead_day"].astype(int)
    out = out[(out["forecast_rank"] > 0) | (out["alerts"] > 0) | (out["warnings"] > 0) | (out["severe"] > 0)]
    out = out.sort_values(["forecast_date", "constituency_id", "lead_day"]).reset_index(drop=True)
    out["forecast_date"] = out["forecast_date"].dt.date.astype(str)
    return out[OUTPUT_COLUMNS]
