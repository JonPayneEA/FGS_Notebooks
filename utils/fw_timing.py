# =============================================================================
# utils/fw_timing.py
# Pure pandas: was each flood warning issued in time? No Spark, so it can be tested without
# Databricks. Used by jobs/27_fw_timing_validation, which runs in the restricted deployment because
# the thresholds are restricted. Job 28 links the thresholds to gauge measures first.
#
# THE QUESTION. A gauge has thresholds, linked by job 28 to a gauge measure of the same unit. RES (Type RES, Purpose FW)
# is the level at which impacts are expected, and the flood warning must be out before the reading first crosses it, by at
# least the target lead time (TargetLeadTimeMinutes in the file). The level that triggers the warning (Type ACTCON,
# Purpose FW) is used only to say how long the warning took after the trigger was crossed.
#
# WHAT IS MEASURED. For every upward crossing of the RES threshold in the readings (an EVENT), the
# warning in force for the gauge's warning area at that moment:
#   on time       issued at least the target lead time before the crossing
#   too short     issued before the crossing, but by less than the target
#   late          no warning in force at the crossing; one was issued within late_hours after it
#   missed        no warning in force, and none issued within late_hours after
# and for every warning with no RES crossing while it was in force:
#   RES not reached   the warning was issued and the gauge never got that high
# A warning with no readings at all while it was in force is "no readings".
# Also reported, for each: trigger_time (the last FW-threshold crossing before the event, within
# lookback_hours) and issue_delay_hours (issue time minus trigger time: how long the warning took after the trigger).
#
# A CROSSING is an upward crossing of the threshold. The next one counts only after the readings have
# stayed below it for reset_hours, so a reading that flickers about the threshold is one event. A series that starts above
# the threshold has no crossing observed, so none is counted.
#
# WARNING-LEVEL. Only flood warnings and severe flood warnings count. A flood alert does not. The issue
# time is the start of the first flood-warning-level period of the episode.
# =============================================================================

import pandas as pd

from utils.telemetry_mapping import clean

WARNING_LEVELS = {"flood_warning", "severe_flood_warning"}

def gauges_from_links(links: pd.DataFrame, res_type: str = "RES", trigger_type: str = "ACTCON", purpose: str = "FW", season: str = "All") -> pd.DataFrame:
    """
    One row per gauge (FFFS key, family, measure, warning area) with res_value, fw_value and target_lead_hours, from the
    threshold links table (job 28, ea_fw_threshold_gauge_links). Only thresholds that linked to a measure (link_status linked
    or linked_chosen) count, so the value and the readings are in the same unit. Of those, only rows whose Purpose is
    `purpose` (FW, a flood warning) and whose Season is `season` or blank are used:
      RES      Type == res_type       the level at which impacts are expected; the warning must be out before it is reached
      trigger  Type == trigger_type   the level that triggers the warning (ACTCON); used for the issue delay only
    Where a gauge has several of either (in one warning area) the lowest of each is used. The warning area is the RES row's
    TargetAreaID, and the target lead time (given in minutes in the file) becomes hours. A gauge with no RES row has nothing
    to validate against and is left out.
    """
    cols = ["key", "fffs_id", "family", "measure_uri", "area_code", "res_value", "fw_value", "target_lead_hours", "unit"]
    if links is None or links.empty:
        return pd.DataFrame(columns=cols)
    d = links[links["link_status"].isin(["linked", "linked_chosen"])].copy()
    lab = lambda s: s.map(lambda v: (clean(v) or "").lower())          # noqa: E731
    d = d[lab(d["purpose"]) == purpose.strip().lower()]
    if "season" in d:
        d = d[lab(d["season"]).isin({season.strip().lower(), ""})]
    # The value in the measure's own unit: job 28 converts an mAOD threshold to the gauge's local datum with z (and back), and writes the
    # result to value_in_measure_unit. Where that column is missing or empty the file's own value is used.
    own = pd.to_numeric(d["value"], errors="coerce")
    d["value"] = pd.to_numeric(d["value_in_measure_unit"], errors="coerce").fillna(own) if "value_in_measure_unit" in d else own
    d["lead_h"] = pd.to_numeric(d["target_lead_minutes"], errors="coerce") / 60.0
    d = d[d["value"].notna()]
    out = []
    for (key, family, uri), g in d.groupby(["key", "family", "measure_uri"], dropna=False):
        res = g[lab(g["type"]) == res_type.strip().lower()]
        if res.empty:
            continue
        trig = g[lab(g["type"]) == trigger_type.strip().lower()]
        area = res["target_area"].map(clean).dropna()
        lead = res["lead_h"].dropna()
        out.append({"key": key, "fffs_id": g["fffs_id"].iloc[0], "family": family, "measure_uri": uri,
                    "area_code": area.iloc[0] if len(area) else None, "res_value": float(res["value"].min()),
                    "fw_value": float(trig["value"].min()) if len(trig) else float("nan"),
                    "target_lead_hours": float(lead.min()) if len(lead) else float("nan"), "unit": g["unit"].iloc[0]})
    return pd.DataFrame(out, columns=cols)


def upcrossings(readings: pd.DataFrame, value: float, reset_hours: float = 6.0) -> list:
    """
    The times (Timestamps) at which the readings cross `value` upward. The next crossing counts only after the readings
    have stayed below it for reset_hours. A series that starts above the value has no crossing observed. `readings` has
    date_time and value.
    """
    r = readings.dropna(subset=["date_time", "value"]).sort_values("date_time")
    if r.empty or value is None or pd.isna(value):
        return []
    times = pd.to_datetime(r["date_time"]).tolist()
    values = r["value"].astype(float).tolist()
    reset = pd.Timedelta(hours=reset_hours)
    out, armed, last_above = [], values[0] < value, (times[0] if values[0] >= value else None)
    for t, v in zip(times, values):
        if v >= value:
            if armed:
                out.append(t)
                armed = False
            last_above = t
        elif not armed and last_above is not None and (t - last_above) >= reset:
            armed = True
    return out


def warning_issues(periods: pd.DataFrame, episodes: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """
    One row per episode that reached flood warning level: episode_id, ea_area_code, issue_time (the start of its first
    warning-level period) and end_time (the episode's end; an open episode ends at as_of, default now).
    """
    p = periods[periods["level"].isin(WARNING_LEVELS)].copy()
    p["start_time"] = pd.to_datetime(p["start_time"])
    first = p.sort_values("start_time").groupby("episode_id").agg(issue_time=("start_time", "first"), ea_area_code=("ea_area_code", "first")).reset_index()
    e = episodes[["episode_id", "end_time"]].copy()
    e["end_time"] = pd.to_datetime(e["end_time"])
    out = first.merge(e, on="episode_id", how="left")
    out["end_time"] = out["end_time"].fillna(pd.Timestamp.now() if as_of is None else pd.Timestamp(as_of))
    return out[["episode_id", "ea_area_code", "issue_time", "end_time"]]


RESULT_COLUMNS = ["fffs_id", "family", "area_code", "kind", "outcome", "res_time", "issue_time", "episode_id", "lead_hours",
                  "target_lead_hours", "trigger_time", "issue_delay_hours", "res_value", "fw_value"]


def validate_gauge(gauge: dict, readings: pd.DataFrame, issues: pd.DataFrame, reset_hours: float = 6.0,
                   late_hours: float = 24.0, lookback_hours: float = 72.0) -> list:
    """
    The timing results for one gauge: `gauge` is a gauge_thresholds() row as a dict, `readings` its date_time and value,
    `issues` the warning_issues() rows for its warning area. Returns a list of dicts with RESULT_COLUMNS.
    """
    res_times = upcrossings(readings, gauge["res_value"], reset_hours)
    fw_times = upcrossings(readings, gauge["fw_value"], reset_hours) if not pd.isna(gauge.get("fw_value")) else []
    target = gauge.get("target_lead_hours")
    base = {"fffs_id": gauge["fffs_id"], "family": gauge["family"], "area_code": gauge["area_code"], "target_lead_hours": target,
            "res_value": gauge["res_value"], "fw_value": gauge.get("fw_value")}
    rd = readings.dropna(subset=["date_time"]).copy()
    rd["date_time"] = pd.to_datetime(rd["date_time"])
    rows = []

    def trigger_before(t):
        prior = [x for x in fw_times if t - pd.Timedelta(hours=lookback_hours) <= x <= t]
        return prior[-1] if prior else pd.NaT

    def row(kind, outcome, res_time=pd.NaT, issue=None, episode=None):
        trig = trigger_before(res_time) if not pd.isna(res_time) else (trigger_before(issue) if issue is not None else pd.NaT)
        lead = (res_time - issue).total_seconds() / 3600 if (issue is not None and not pd.isna(res_time)) else float("nan")
        delay = (issue - trig).total_seconds() / 3600 if (issue is not None and not pd.isna(trig)) else float("nan")
        return {**base, "kind": kind, "outcome": outcome, "res_time": res_time, "issue_time": issue if issue is not None else pd.NaT,
                "episode_id": episode, "lead_hours": round(lead, 2) if not pd.isna(lead) else lead, "trigger_time": trig,
                "issue_delay_hours": round(delay, 2) if not pd.isna(delay) else delay}

    for t in res_times:
        live = issues[(issues["issue_time"] <= t) & (issues["end_time"] >= t)].sort_values("issue_time")
        if len(live):
            w = live.iloc[-1]
            lead = (t - w["issue_time"]).total_seconds() / 3600
            outcome = "on time" if pd.isna(target) or lead >= target else "too short"
            rows.append(row("res_event", outcome, t, w["issue_time"], w["episode_id"]))
            continue
        after = issues[(issues["issue_time"] > t) & (issues["issue_time"] <= t + pd.Timedelta(hours=late_hours))].sort_values("issue_time")
        if len(after):
            w = after.iloc[0]
            rows.append(row("res_event", "late", t, w["issue_time"], w["episode_id"]))
        else:
            rows.append(row("res_event", "missed", t))

    linked = {r["episode_id"] for r in rows if r["episode_id"] is not None}       # a late warning is already accounted for by its event
    for _, w in issues.iterrows():
        hit = any(w["issue_time"] <= t <= w["end_time"] for t in res_times)
        if hit or w["episode_id"] in linked:
            continue
        seen = rd[(rd["date_time"] >= w["issue_time"]) & (rd["date_time"] <= w["end_time"])]
        rows.append(row("warning_only", "RES not reached" if len(seen) else "no readings", pd.NaT, w["issue_time"], w["episode_id"]))
    return rows


def summarise(results: pd.DataFrame) -> pd.DataFrame:
    """Rows per outcome, with the median lead time (hours) and the share of the RES events they are."""
    if results.empty:
        return pd.DataFrame(columns=["outcome", "rows", "median_lead_hours", "share_of_res_events"])
    ev = results[results["kind"] == "res_event"]
    out = results.groupby("outcome").agg(rows=("outcome", "size"), median_lead_hours=("lead_hours", "median")).reset_index()
    out["share_of_res_events"] = [round(len(ev[ev["outcome"] == o]) / len(ev), 3) if len(ev) else float("nan") for o in out["outcome"]]
    return out.sort_values("rows", ascending=False).reset_index(drop=True)
