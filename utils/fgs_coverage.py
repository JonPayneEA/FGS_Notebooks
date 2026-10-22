# =============================================================================
# utils/fgs_coverage.py
# Pure pandas: for each flood alert and warning, was it under an FGS risk polygon, and
# was that forecast issued before it, on the day, or after? No Spark, so it can be
# tested without Databricks. Used by jobs/26_warning_fgs_coverage.
#
# THE QUESTION. A warning (an episode from job 25) is for one EA area and runs from the
# day it was issued to the day it ended. The FGS marks areas it expects to be at risk,
# on a forecast date, in a statement issued on some day. Was the area under an FGS risk
# polygon when the warning was issued, and was that forecast made before, or only on the day?
# A polygon is linked to an area (job 07) when it overlapped the area or, for a coastal
# warning area, lay over a linked parent alert area.
#
# THE REFERENCE IS THE ISSUE DAY. Where there is an impact the FGS keeps a coloured polygon
# over the area for the whole of the warning, so the polygons on later days say nothing about
# timing. Only the forecast for the day the warning was issued is compared:
#   before      a statement issued on an EARLIER day put a polygon over the area for the
#               issue day. The FGS gave notice. lead_days says how many days earlier.
#   same_day    the earliest such statement was issued on the issue day itself.
#   later       no statement covered the issue day, but a polygon covered a LATER day the
#               warning was in force. The FGS caught up, or the impact came after the issue.
#   none        no polygon covered the area on any day the warning was in force.
# Comparison is by day. Hours are not compared, because statement and message times are not
# held in one zone.
#
# THE RISK THRESHOLD. A Very Low polygon covers a great deal of England, so "covered" depends
# on how serious a polygon must be. For each rank 1 to 4 the result carries the earliest
# statement that covered the issue day at that rank or higher (first_cover_ge1 .. _ge4), and
# later_rank, the worst rank over the later days. The app chooses the threshold.
#
# IN FORCE. A warning counts for every date from its first day to its last, up to
# MAX_SPAN_DAYS. One open for over 30 days (stale_open) most likely never received its "no longer
# in force" message and counts only for its first day.
#
# WHAT THIS IS NOT. A warning not under a polygon is not necessarily a missed forecast: the FGS
# forecasts risk across regions and the EA issues warnings for specific rivers and coasts. It
# measures how often the polygons and the warnings agree.
# =============================================================================

import pandas as pd

MAX_SPAN_DAYS = 60
RANKS = (1, 2, 3, 4)

COVERAGE_COLUMNS = ["episode_id", "ea_area_code", "area_type", "issue_date", "max_rank", "issue_day_rank",
                    "later_rank", "rank_before", "rank_same_day", "first_cover_ge1", "first_cover_ge2",
                    "first_cover_ge3", "first_cover_ge4", "sources"]


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
        COVERAGE_COLUMNS, one row per episode. issue_day_rank is the worst rank any statement
        gave the area for the issue day; later_rank the worst for any later day in force;
        max_rank the worst of both; rank_before and rank_same_day split issue_day_rank by whether
        the statement was issued on an earlier day or on the issue day. A first_cover_ge column is
        NaT where no statement covered the issue day at that rank or higher.
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
    ranks = ("max_rank", "issue_day_rank", "later_rank", "rank_before", "rank_same_day")
    if m.empty:
        for c in ranks:
            out[c] = 0
        for r in RANKS:
            out[f"first_cover_ge{r}"] = pd.NaT
        out["sources"] = None
        return out.reset_index()[COVERAGE_COLUMNS]

    on_day = m[m["d"] == m["issue_date"]]
    later = m[m["d"] > m["issue_date"]]
    out["max_rank"] = m.groupby("episode_id")["rk"].max()
    out["issue_day_rank"] = on_day.groupby("episode_id")["rk"].max()
    out["later_rank"] = later.groupby("episode_id")["rk"].max()
    out["rank_before"] = on_day[on_day["s"] < on_day["issue_date"]].groupby("episode_id")["rk"].max()
    out["rank_same_day"] = on_day[on_day["s"] >= on_day["issue_date"]].groupby("episode_id")["rk"].max()
    for r in RANKS:
        out[f"first_cover_ge{r}"] = on_day[on_day["rk"] >= r].groupby("episode_id")["s"].min()
    # The sources of the polygons that decide it: the issue day's, else the later days'.
    src_on = on_day.groupby("episode_id")["sources"].agg(lambda s: _join_sources(s))
    src_all = m.groupby("episode_id")["sources"].agg(lambda s: _join_sources(s))
    out["sources"] = src_on.reindex(out.index).where(src_on.reindex(out.index).notna() & (src_on.reindex(out.index) != ""), src_all.reindex(out.index))
    for c in ranks:
        out[c] = out[c].fillna(0).astype(int)
    out["sources"] = out["sources"].where(out["max_rank"] > 0, None)
    return out.reset_index()[COVERAGE_COLUMNS]


def _join_sources(values) -> str:
    return ",".join(sorted({p for v in values.dropna() for p in str(v).split(",") if p}))


def category(issue_date, first_cover, later_rank=0, rank=1) -> str:
    """
    before, same_day, later or none, for one episode at one risk threshold.
    first_cover is the earliest statement covering the issue day at that rank or higher;
    later_rank the worst rank over the later days in force.
    """
    if first_cover is not None and not pd.isna(first_cover):
        a, b = pd.Timestamp(first_cover).normalize(), pd.Timestamp(issue_date).normalize()
        return "before" if a < b else "same_day"
    if later_rank is not None and not pd.isna(later_rank) and int(later_rank) >= int(rank):
        return "later"
    return "none"
