# =============================================================================
# utils/fgs_coverage.py
# Pure pandas: for each flood alert and warning, was it under an FGS risk polygon, and
# was that forecast issued before it, on the day, or after? No Spark, so it can be
# tested without Databricks. Used by jobs/26_warning_fgs_coverage.
#
# THE QUESTION. A warning (an episode from job 25) is for one EA area and runs from the
# day it was issued to the day it ended. The FGS marks areas it expects to be at risk,
# on a forecast date, in a statement issued on some day. The area counts as COVERED if a
# risk polygon was linked to it (job 07: it overlapped the area, or for a coastal
# warning area it lay under a linked parent alert area) for a date the warning was in
# force.
#
# BEFORE, ON THE DAY OR AFTER. Compare the day each covering statement was issued with the
# day the warning was issued:
#   before    the earliest covering statement was issued on an earlier day. The FGS gave notice.
#   same day  the earliest was issued on the same day as the warning. It did not give notice,
#             but it was not late either.
#   after     the earliest was issued on a later day. The warning came first.
#   none      no statement covered the area for any date the warning was in force.
# The earliest covering statement decides, so a warning covered both before and after counts
# as before. lead_days is how many days earlier that statement was issued.
#
# THE RISK THRESHOLD. A Very Low polygon covers a great deal of England, so "covered" depends
# on how serious a polygon must be. The result carries, for each rank 1 to 4, the earliest
# covering statement at that rank or higher (first_cover_ge1 .. first_cover_ge4). The app
# chooses the threshold and reads the category from the matching column.
#
# IN FORCE. A warning counts for every date from its first day to its last. One open for over
# 30 days (stale_open) most likely never received its "no longer in force" message and counts
# only for its first day. A span is capped at MAX_SPAN_DAYS so one bad record cannot run for years.
#
# WHAT THIS IS NOT. A warning not under a polygon is not necessarily a missed forecast: the FGS
# forecasts risk across regions and the EA issues warnings for specific rivers and coasts. It
# measures how often the polygons and the warnings agree.
# =============================================================================

import pandas as pd

MAX_SPAN_DAYS = 60
RANKS = (1, 2, 3, 4)

COVERAGE_COLUMNS = ["episode_id", "ea_area_code", "area_type", "issue_date", "max_rank", "rank_before",
                    "rank_same_day", "rank_after", "first_cover_ge1", "first_cover_ge2", "first_cover_ge3",
                    "first_cover_ge4", "sources"]


def _day(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()


def episode_days(episodes: pd.DataFrame, now) -> pd.DataFrame:
    """One row per episode and date it was in force: episode_id, ea_area_code, issue_date, d."""
    now_day = pd.Timestamp(now)
    now_day = (now_day.tz_localize(None) if now_day.tzinfo else now_day).normalize()
    e = episodes.dropna(subset=["episode_id", "ea_area_code", "start_time"]).copy()
    start = _day(e["start_time"])
    end = _day(e["end_time"])
    stale = e["stale_open"].fillna(False).astype(bool) if "stale_open" in e else pd.Series(False, index=e.index)
    end = end.where(end.notna(), now_day)
    end = end.where(~(e["end_time"].isna() & stale), start)
    end = pd.concat([end, start], axis=1).max(axis=1)
    end = pd.concat([end, start + pd.Timedelta(days=MAX_SPAN_DAYS - 1)], axis=1).min(axis=1)
    n = ((end - start).dt.days + 1).astype(int)
    out = pd.DataFrame({"episode_id": e["episode_id"].values, "ea_area_code": e["ea_area_code"].values,
                        "issue_date": start.values}, index=e.index).loc[e.index.repeat(n)]
    out["d"] = out["issue_date"] + pd.to_timedelta(out.groupby(level=0).cumcount(), unit="D")
    return out.reset_index(drop=True)


def cover_episodes(episodes: pd.DataFrame, coverage: pd.DataFrame, now) -> pd.DataFrame:
    """
    The coverage of each episode.

    Args:
        episodes:  episode_id, ea_area_code, area_type, start_time, end_time, stale_open (job 25).
        coverage:  ea_area_code, d (the forecast date), s (the day the statement was issued),
                   rk (the worst risk rank, 1 to 4, of its polygons linked to the area) and
                   sources (comma-separated flood sources), one row per area, date and issue day.

    Returns:
        COVERAGE_COLUMNS, one row per episode. A first_cover_ge column is NaT where no statement
        covered the episode at that rank or higher.
    """
    days = episode_days(episodes, now)
    base = episodes.dropna(subset=["episode_id", "ea_area_code", "start_time"])[
        ["episode_id", "ea_area_code", "area_type"]].copy()
    base["issue_date"] = _day(episodes.loc[base.index, "start_time"]).values

    cov = coverage.copy()
    cov["d"] = pd.to_datetime(cov["d"]).dt.normalize()
    cov["s"] = pd.to_datetime(cov["s"]).dt.normalize()
    m = days.merge(cov, on=["ea_area_code", "d"], how="inner")

    out = base.set_index("episode_id")
    if m.empty:
        for c in ("max_rank", "rank_before", "rank_same_day", "rank_after"):
            out[c] = 0
        for r in RANKS:
            out[f"first_cover_ge{r}"] = pd.NaT
        out["sources"] = None
        return out.reset_index()[COVERAGE_COLUMNS]

    m["rel"] = (m["s"] - m["issue_date"]).dt.days
    g = m.groupby("episode_id")
    out["max_rank"] = g["rk"].max()
    out["rank_before"] = m[m["rel"] < 0].groupby("episode_id")["rk"].max()
    out["rank_same_day"] = m[m["rel"] == 0].groupby("episode_id")["rk"].max()
    out["rank_after"] = m[m["rel"] > 0].groupby("episode_id")["rk"].max()
    for r in RANKS:
        out[f"first_cover_ge{r}"] = m[m["rk"] >= r].groupby("episode_id")["s"].min()
    out["sources"] = g["sources"].agg(lambda s: ",".join(sorted({p for v in s.dropna() for p in str(v).split(",") if p})))
    for c in ("max_rank", "rank_before", "rank_same_day", "rank_after"):
        out[c] = out[c].fillna(0).astype(int)
    out["sources"] = out["sources"].where(out["max_rank"] > 0, None)
    return out.reset_index()[COVERAGE_COLUMNS]


def category(issue_date, first_cover) -> str:
    """before, same_day, after or none, for one episode at one threshold."""
    if first_cover is None or pd.isna(first_cover):
        return "none"
    a, b = pd.Timestamp(first_cover).normalize(), pd.Timestamp(issue_date).normalize()
    return "before" if a < b else "same_day" if a == b else "after"
