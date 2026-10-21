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
#
# TWO SOURCES. The message history file covers the past; flood_warnings_log (job 11) is
# the live record from when polling began. events_from_log() turns the log into the same
# kind of event, combine_events() joins the two, and build_history() notes where each
# episode's start and end came from, and which cross the gap between the two sources.
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

ORIGIN_FILE, ORIGIN_LOG = "history_file", "flood_warnings_log"
KIND_MESSAGE, KIND_CLOSED = "message", "closed_fallback"

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
    df["origin"] = ORIGIN_FILE
    df["kind"] = KIND_MESSAGE

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


def _naive_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_localize(None)


def events_from_log(log: pd.DataFrame):
    """
    Events from flood_warnings_log (job 11), in the same form as normalise_messages.

    The log has one row each time the live feed changed for an area:
      NEW, SEVERITY_CHANGE  a level in force from time_severity_changed (the EA's own time),
                            or the poll time if that is missing. Level 4 is "no longer in force".
      MESSAGE_CHANGE        the same level with new text: an update, at time_message_changed.
      CLOSED                the area left the feed. If its last level was 4 the end is already
                            recorded; if not (the 4 was never seen) it is a switch off at the
                            poll that noticed, marked closed_fallback.

    Returns:
        (events, report): events with ea_area_code, time, level, text, source, origin and kind,
        and counts: rows, no_code, no_time, unknown_level, closed_fallback.
    """
    report = {"rows": int(len(log))}
    df = pd.DataFrame({
        "ea_area_code": log["flood_area_id"].astype("string").str.strip(),
        "level": pd.to_numeric(log["severity_level"], errors="coerce").map(NUMBER_TO_LEVEL),
        "change_type": log["change_type"].astype("string").str.upper(),
    })
    sev_time = _naive_utc(log["time_severity_changed"]) if "time_severity_changed" in log else pd.Series(pd.NaT, index=log.index)
    msg_time = _naive_utc(log["time_message_changed"]) if "time_message_changed" in log else pd.Series(pd.NaT, index=log.index)
    polled = _naive_utc(log["ingested_at"]) if "ingested_at" in log else pd.Series(pd.NaT, index=log.index)
    is_msg = df["change_type"] == "MESSAGE_CHANGE"
    is_closed = df["change_type"] == "CLOSED"
    time = sev_time.where(~is_msg, msg_time)
    time = time.fillna(polled)
    time = time.where(~is_closed, polled)            # a CLOSED is when the poll noticed it had gone
    df["time"] = time
    df["text"] = df["change_type"]
    df["source"] = "flood_warnings_log"
    df["origin"] = ORIGIN_LOG
    df["kind"] = KIND_MESSAGE

    # A CLOSED whose last level was 4 adds nothing. One whose last level was 1 to 3 is a switch off.
    fallback = is_closed & df["level"].notna() & (df["level"] != LEVEL_OFF)
    df.loc[fallback, "level"] = LEVEL_OFF
    df.loc[fallback, "kind"] = KIND_CLOSED
    report["closed_fallback"] = int(fallback.sum())
    skip = is_closed & ~fallback

    no_code = df["ea_area_code"].isna() | (df["ea_area_code"] == "")
    no_time = df["time"].isna()
    no_level = df["level"].isna() & ~is_closed
    report["no_code"] = int(no_code.sum())
    report["no_time"] = int((no_time & ~no_code).sum())
    report["unknown_level"] = int((no_level & ~no_code & ~no_time).sum())
    df = df[~(no_code | no_time | no_level | skip)].copy()
    return df[["ea_area_code", "time", "level", "text", "source", "origin", "kind"]].reset_index(drop=True), report


def combine_events(*frames):
    """
    One sorted event list from several (the history file's, then the log's). An exact
    repeat (the same area, time and level) is kept once, from the earlier frame.
    Returns (events, duplicates).
    """
    df = pd.concat([f for f in frames if f is not None and len(f)], ignore_index=True)
    before = len(df)
    df = df.drop_duplicates(subset=["ea_area_code", "time", "level"], keep="first")
    df["_tie"] = df["level"].map(_TIE_ORDER)
    df = df.sort_values(["ea_area_code", "time", "_tie"], kind="mergesort").drop(columns="_tie")
    return df.reset_index(drop=True), before - len(df)


def _minutes(start, end) -> float:
    return round((end - start).total_seconds() / 60.0, 2)


def build_history(events: pd.DataFrame, as_of=None, gap=None):
    """
    Episodes and periods from the clean events of `normalise_messages`.

    Args:
        events: ea_area_code, time, level, sorted by area then time.
        as_of:  The end of the data. Anything still in force then is open, and its
                duration is measured to here. Defaults to the latest event time.
        gap:    (start, end) of a stretch no source covers, such as the time between the end of
                the history file and the start of the log. Episodes that start before it and
                end after it (or never end) are marked spans_gap.

    Returns:
        (episodes, periods, stats). Episodes have one row per run in force: episode_id,
        ea_area_code, area_type, start_time, end_time (null while open), duration_minutes,
        duration_hours, start_level, peak_level, had_severe, severe_minutes,
        n_severe_periods, n_messages, ended_by (no_longer_in_force, dropped_from_feed or open),
        is_open, stale_open, start_origin, end_origin, spans_gap. Periods have one row per
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
        origins = g["origin"] if "origin" in g else pd.Series(ORIGIN_FILE, index=g.index)
        kinds = g["kind"] if "kind" in g else pd.Series(KIND_MESSAGE, index=g.index)
        for t, lvl, origin, kind in zip(g["time"], g["level"], origins, kinds):
            if lvl == LEVEL_OFF:
                if ep is None:
                    orphan_off += 1
                    continue
                how = "dropped_from_feed" if kind == KIND_CLOSED else "no_longer_in_force"
                close_period(per, t, how)
                ep["end_time"], ep["ended_by"], ep["end_origin"] = t, how, origin
                episodes.append(ep)
                ep = per = None
                continue

            if ep is None:
                n_per = 1
                ep = {"episode_id": f"{code}_{t:%Y%m%dT%H%M}", "ea_area_code": code, "area_type": area_type(code),
                      "start_time": t, "start_level": lvl, "peak_level": lvl, "n_messages": 1,
                      "severe_minutes": 0.0, "n_severe_periods": 0, "had_severe": False,
                      "start_origin": origin, "end_origin": None}
                per = {"period_id": f"{ep['episode_id']}_{n_per}", "episode_id": ep["episode_id"], "ea_area_code": code,
                       "level": lvl, "start_time": t, "started_by": "issued", "start_origin": origin}
            else:
                ep["n_messages"] += 1
                if lvl != per["level"]:
                    up = LEVEL_HEIGHT[lvl] > LEVEL_HEIGHT[per["level"]]
                    change = "upgraded" if up else "downgraded"
                    close_period(per, t, change)
                    n_per += 1
                    per = {"period_id": f"{ep['episode_id']}_{n_per}", "episode_id": ep["episode_id"], "ea_area_code": code,
                           "level": lvl, "start_time": t, "started_by": change, "start_origin": origin}
                    if LEVEL_HEIGHT[lvl] > LEVEL_HEIGHT[ep["peak_level"]]:
                        ep["peak_level"] = lvl
            if lvl == LEVEL_SEVERE:
                ep["had_severe"] = True

        if ep is not None:                                   # still in force when the data ends
            per["end_time"], per["ended_by"] = None, "open"
            periods.append(per)
            ep["end_time"], ep["ended_by"], ep["end_origin"] = None, "open", None
            episodes.append(ep)

    ep_df = pd.DataFrame(episodes)
    per_df = pd.DataFrame(periods)
    cols_ep = ["episode_id", "ea_area_code", "area_type", "start_time", "end_time", "duration_minutes", "duration_hours",
               "start_level", "peak_level", "had_severe", "severe_minutes", "n_severe_periods", "n_messages",
               "ended_by", "is_open", "stale_open", "start_origin", "end_origin", "spans_gap"]
    cols_per = ["period_id", "episode_id", "ea_area_code", "level", "start_time", "end_time", "duration_minutes",
                "duration_hours", "started_by", "ended_by", "is_open", "start_origin"]
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
    if gap is not None and not pd.isna(gap[0]) and not pd.isna(gap[1]) and gap[1] > gap[0]:
        ep_df["spans_gap"] = (ep_df["start_time"] <= gap[0]) & (ep_df["end_time"].isna() | (ep_df["end_time"] >= gap[1]))
    else:
        ep_df["spans_gap"] = False
    return ep_df[cols_ep], per_df[cols_per], {"orphan_off": orphan_off}


def should_skip_rebuild(built_at, log_through, log_latest, now, fresh_minutes) -> bool:
    """
    Whether a scheduled rebuild can be skipped: the tables were built less than
    `fresh_minutes` ago AND the log has nothing newer than what that build used.

    A scheduled run every 15 minutes would otherwise replay the whole history on every poll
    although flood_warnings_log only gains rows when something changes. The age limit still
    forces a rebuild every `fresh_minutes`, so the open episodes' durations keep growing.

    Args:
        built_at:    When the tables were last built (None if never).
        log_through: The latest log poll that build used (None if unknown or the log was not used).
        log_latest:  The latest poll in the log now (None if there is no log).
        now:         The current time.
        fresh_minutes: 0 or less never skips.
    """
    if fresh_minutes is None or fresh_minutes <= 0 or built_at is None or pd.isna(built_at):
        return False
    if (pd.Timestamp(now) - pd.Timestamp(built_at)) >= pd.Timedelta(minutes=fresh_minutes):
        return False
    if log_latest is None or pd.isna(log_latest):
        return True                                   # no log to be newer than the build
    if log_through is None or pd.isna(log_through):
        return False                                  # an older build with no record of the log
    return pd.Timestamp(log_latest) <= pd.Timestamp(log_through)
