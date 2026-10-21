# =============================================================================
# utils/warning_history.py
# Pure pandas: turn a history of flood warning and alert MESSAGES into when each
# alert, warning and severe flood warning started, ended and how long it lasted.
# No Spark, so it can be tested without Databricks. Used by
# jobs/25_warning_history_periods.
#
# THE RULES (from how the Environment Agency issues messages, per area code):
#   Flood alert                  switches an alert on.
#   Flood warning                switches a warning on, or turns a severe flood
#                                warning DOWN one level.
#   Severe flood warning         raises an existing warning to severe.
#   Warning no longer in force   switches the area off, from any level.
# Any message at the level an area is already at is an update, not a change.
#
# Two kinds of record come out:
#   EPISODE  one run of an area being in force at ANY level, from the first message
#            that switched it on to the "no longer in force" that ended it. A warning
#            that goes severe and back is one episode.
#   PERIOD   one run at ONE level inside an episode, so a warning that goes severe
#            has a flood warning period, a severe flood warning period and a flood
#            warning period again.
# =============================================================================

import pandas as pd

LEVEL_ALERT, LEVEL_WARNING, LEVEL_SEVERE, LEVEL_OFF = (
    "flood_alert", "flood_warning", "severe_flood_warning", "off")

# Message text (lower case) to level.
TEXT_TO_LEVEL = {
    "flood alert": LEVEL_ALERT,
    "flood warning": LEVEL_WARNING,
    "severe flood warning": LEVEL_SEVERE,
    "warning no longer in force": LEVEL_OFF,
}

# The EA's numeric severity: 1 severe, 2 warning, 3 alert, 4 no longer in force.
NUMBER_TO_LEVEL = {1: LEVEL_SEVERE, 2: LEVEL_WARNING, 3: LEVEL_ALERT, 4: LEVEL_OFF}

# How high each level is, so a change can be called an upgrade or a downgrade.
LEVEL_HEIGHT = {LEVEL_ALERT: 1, LEVEL_WARNING: 2, LEVEL_SEVERE: 3}

# At the same time, a switch off is applied last.
_TIE_ORDER = {LEVEL_ALERT: 0, LEVEL_WARNING: 1, LEVEL_SEVERE: 2, LEVEL_OFF: 9}

ALERT_TYPE_CODES = {"WAF", "WAT", "WAC", "FAG", "WAB"}
WARNING_TYPE_CODES = {"FWF", "FWC", "FWG", "FWB", "FWT"}


def area_type(code) -> str:
    """flood_alert or flood_warning, from characters 4 to 6 of the area code ("011FWFNC1D" gives FWF)."""
    t = str(code or "")[3:6]
    if t in ALERT_TYPE_CODES:
        return "flood_alert"
    if t in WARNING_TYPE_CODES:
        return "flood_warning"
    return "unknown"


def _pick_column(df: pd.DataFrame, names) -> str:
    for n in names:
        if n in df.columns:
            return n
    return None


def normalise_messages(raw: pd.DataFrame):
    """
    The message history as clean events: ea_area_code, time, level, text, source.

    The time is read from `dateTime` (ISO) and, where that is missing, from
    `timeOfChange` ("08/06/2026 9:48am"). It is kept as written, with no time zone
    conversion: see the README on what that means. The level is read from the text
    column `Severity`; where only the numeric `severity` is present that is used. Rows
    with no area code, no readable time or an unrecognised severity are dropped and
    counted. Exact repeats (the same area, time and level, as when a message appears in
    both the live feed and the history log) are dropped once.

    Returns:
        (events, report): the events, sorted by area, time and tie order, and a dict of
        counts: rows, no_code, no_time, unknown_severity, duplicates, disagreeing_levels,
        unknown_values (a sample of the severity values that were not recognised).
    """
    report = {"rows": int(len(raw))}
    code_col = _pick_column(raw, ["TACode", "ta_code", "ea_area_code", "fws_tacode"])
    if code_col is None:
        raise ValueError(f"No area code column found. Columns present: {list(raw.columns)}")
    text_col = _pick_column(raw, ["Severity", "severity_text", "severityText"])
    num_col = "severity" if "severity" in raw.columns else None
    if text_col is None and num_col is None:
        raise ValueError(f"No severity column found. Columns present: {list(raw.columns)}")

    df = pd.DataFrame({"ea_area_code": raw[code_col].astype("string").str.strip()})

    # Time: dateTime first, then timeOfChange.
    time = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
    if "dateTime" in raw.columns:
        parsed = pd.to_datetime(raw["dateTime"], errors="coerce", utc=True)
        time = parsed.dt.tz_localize(None)
    if "timeOfChange" in raw.columns:
        fallback = pd.to_datetime(raw["timeOfChange"], errors="coerce", format="%d/%m/%Y %I:%M%p")
        time = time.fillna(fallback)
    df["time"] = time

    # Level: the text, else the number. Where both are present and disagree the text wins and it is counted.
    from_text = (raw[text_col].astype("string").str.strip().str.lower().map(TEXT_TO_LEVEL)
                 if text_col else pd.Series(pd.NA, index=raw.index, dtype="object"))
    from_num = (pd.to_numeric(raw[num_col], errors="coerce").map(NUMBER_TO_LEVEL)
                if num_col else pd.Series(pd.NA, index=raw.index, dtype="object"))
    df["level"] = from_text.where(from_text.notna(), from_num)
    both = from_text.notna() & from_num.notna()
    report["disagreeing_levels"] = int((both & (from_text != from_num)).sum())
    df["text"] = raw[text_col].astype("string") if text_col else pd.NA
    df["source"] = raw["Source"].astype("string") if "Source" in raw.columns else pd.NA

    no_code = df["ea_area_code"].isna() | (df["ea_area_code"] == "")
    no_time = df["time"].isna()
    no_level = df["level"].isna()
    report["no_code"] = int(no_code.sum())
    report["no_time"] = int((no_time & ~no_code).sum())
    report["unknown_severity"] = int((no_level & ~no_code & ~no_time).sum())
    unknown = df.loc[no_level & ~no_code & ~no_time, "text"].dropna()
    report["unknown_values"] = sorted({str(v) for v in unknown.unique()})[:10]

    df = df[~(no_code | no_time | no_level)].copy()
    before = len(df)
    df = df.drop_duplicates(subset=["ea_area_code", "time", "level"], keep="first")
    report["duplicates"] = before - len(df)

    df["_tie"] = df["level"].map(_TIE_ORDER)
    df = df.sort_values(["ea_area_code", "time", "_tie"], kind="mergesort").drop(columns="_tie")
    return df.reset_index(drop=True), report


def _minutes(start, end) -> float:
    return round((end - start).total_seconds() / 60.0, 2)


def build_history(events: pd.DataFrame, as_of=None):
    """
    Episodes and periods from the clean events of `normalise_messages`.

    Args:
        events: ea_area_code, time, level, sorted by area then time.
        as_of:  The end of the data. Anything still in force then is open, and its
                duration is measured to here. Defaults to the latest event time.

    Returns:
        (episodes, periods, stats). Episodes have one row per run in force: episode_id,
        ea_area_code, area_type, start_time, end_time (null while open), duration_minutes,
        duration_hours, start_level, peak_level, had_severe, severe_minutes,
        n_severe_periods, n_messages, ended_by, is_open, stale_open. Periods have one row per
        run at one level: period_id, episode_id, ea_area_code, level, start_time, end_time,
        duration_minutes, duration_hours, started_by, ended_by, is_open. stats counts the
        off messages that had nothing to switch off (orphan_off).
    """
    as_of = pd.Timestamp(as_of) if as_of is not None else (events["time"].max() if len(events) else pd.NaT)
    episodes, periods = [], []
    orphan_off = 0

    def close_period(p, when, ended_by):
        p["end_time"], p["ended_by"] = when, ended_by
        periods.append(p)

    for code, g in events.groupby("ea_area_code", sort=False):
        ep, per, n_per = None, None, 0
        for t, lvl in zip(g["time"], g["level"]):
            if lvl == LEVEL_OFF:
                if ep is None:
                    orphan_off += 1
                    continue
                close_period(per, t, "no_longer_in_force")
                ep["end_time"], ep["ended_by"] = t, "no_longer_in_force"
                episodes.append(ep)
                ep = per = None
                continue

            if ep is None:
                n_per = 1
                ep = {"episode_id": f"{code}_{t:%Y%m%dT%H%M}", "ea_area_code": code, "area_type": area_type(code),
                      "start_time": t, "start_level": lvl, "peak_level": lvl, "n_messages": 1,
                      "severe_minutes": 0.0, "n_severe_periods": 0, "had_severe": False}
                per = {"period_id": f"{ep['episode_id']}_{n_per}", "episode_id": ep["episode_id"], "ea_area_code": code,
                       "level": lvl, "start_time": t, "started_by": "issued"}
            else:
                ep["n_messages"] += 1
                if lvl != per["level"]:
                    up = LEVEL_HEIGHT[lvl] > LEVEL_HEIGHT[per["level"]]
                    change = "upgraded" if up else "downgraded"
                    close_period(per, t, change)
                    n_per += 1
                    per = {"period_id": f"{ep['episode_id']}_{n_per}", "episode_id": ep["episode_id"], "ea_area_code": code,
                           "level": lvl, "start_time": t, "started_by": change}
                    if LEVEL_HEIGHT[lvl] > LEVEL_HEIGHT[ep["peak_level"]]:
                        ep["peak_level"] = lvl
            if lvl == LEVEL_SEVERE:
                ep["had_severe"] = True

        if ep is not None:                                   # still in force when the data ends
            per["end_time"], per["ended_by"] = None, "open"
            periods.append(per)
            ep["end_time"], ep["ended_by"] = None, "open"
            episodes.append(ep)

    ep_df = pd.DataFrame(episodes)
    per_df = pd.DataFrame(periods)
    cols_ep = ["episode_id", "ea_area_code", "area_type", "start_time", "end_time", "duration_minutes", "duration_hours",
               "start_level", "peak_level", "had_severe", "severe_minutes", "n_severe_periods", "n_messages",
               "ended_by", "is_open", "stale_open"]
    cols_per = ["period_id", "episode_id", "ea_area_code", "level", "start_time", "end_time", "duration_minutes",
                "duration_hours", "started_by", "ended_by", "is_open"]
    if per_df.empty:
        return (pd.DataFrame(columns=cols_ep), pd.DataFrame(columns=cols_per), {"orphan_off": orphan_off})

    per_df["is_open"] = per_df["end_time"].isna()
    stop = per_df["end_time"].fillna(as_of)
    per_df["duration_minutes"] = [_minutes(s, e) for s, e in zip(per_df["start_time"], stop)]
    per_df["duration_hours"] = (per_df["duration_minutes"] / 60.0).round(2)

    sev = per_df[per_df["level"] == LEVEL_SEVERE].groupby("episode_id")["duration_minutes"].agg(["sum", "count"])
    ep_df["is_open"] = ep_df["end_time"].isna()
    stop = ep_df["end_time"].fillna(as_of)
    ep_df["duration_minutes"] = [_minutes(s, e) for s, e in zip(ep_df["start_time"], stop)]
    ep_df["duration_hours"] = (ep_df["duration_minutes"] / 60.0).round(2)
    ep_df["severe_minutes"] = ep_df["episode_id"].map(sev["sum"]).fillna(0.0).round(2)
    ep_df["n_severe_periods"] = ep_df["episode_id"].map(sev["count"]).fillna(0).astype(int)
    # Open for over 30 days before the data ends: probably a missing "no longer in force".
    ep_df["stale_open"] = ep_df["is_open"] & ((as_of - ep_df["start_time"]) > pd.Timedelta(days=30))
    return ep_df[cols_ep], per_df[cols_per], {"orphan_off": orphan_off}
