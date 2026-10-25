# =============================================================================
# utils/fw_timing.py
# Pure pandas: was each flood warning issued in time? No Spark, so it can be tested without
# Databricks. Used by jobs/27_fw_timing_validation, which runs in the restricted schema because
# the thresholds are restricted.
#
# THE QUESTION. A gauge has thresholds, keyed to an FFFS_ID and a parameter. Crossing the FW
# threshold is what triggers a flood warning. A higher threshold at the same gauge, RES, is the level at which
# the impact is reached, and the warning must be out before the reading first crosses it, by at least
# the target lead time. The thresholds table carries the target in hours.
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

import re

import pandas as pd

from utils.telemetry_mapping import clean, family_of_code, family_of_text, id_key

WARNING_LEVELS = {"flood_warning", "severe_flood_warning"}

# Column names tried, in order, when the notebook is not told which column holds what.
COLUMN_GUESSES = {
    "fffs_id": ["fffsid", "fffs"],
    "parameter": ["parameterid", "parameter", "param"],
    "type": ["thresholdtype", "type", "kind", "leveltype", "thresholdname", "threshold_name", "name"],
    "value": ["thresholdvalue", "value", "threshold", "level"],
    "lead": ["targetleadtime", "targetleadtimehours", "leadtime", "targetlead", "lead", "leadhours"],
    "area": ["warningareacode", "warningarea", "tacode", "fwacode", "areacode", "fwa", "taccode", "targetarea"],
}


def _norm(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def resolve_columns(raw: pd.DataFrame, columns: dict = None) -> dict:
    """
    Which column of the thresholds table holds each field. `columns` names them (any case, spaces and underscores
    ignored); a field not named is looked for among COLUMN_GUESSES. fffs_id, type and value are required.
    Returns {field: actual column name or None}.
    """
    present = {_norm(c): c for c in raw.columns}
    out = {}
    for field, guesses in COLUMN_GUESSES.items():
        named = (columns or {}).get(field)
        if named:
            if _norm(named) not in present:
                raise ValueError(f"The {field} column '{named}' is not in the thresholds table. Columns present: {list(raw.columns)}")
            out[field] = present[_norm(named)]
        else:
            out[field] = next((present[g] for g in guesses if g in present), None)
    missing = [f for f in ("fffs_id", "type", "value") if out[f] is None]
    if missing:
        raise ValueError(f"Could not find the column for {missing} in the thresholds table. Name them in the widgets. "
                         f"Columns present: {list(raw.columns)}")
    return out


def _is_label(text, label) -> bool:
    t = (clean(text) or "").lower()
    lab = (clean(label) or "").lower()
    return bool(lab) and (t == lab or lab in re.findall(r"[a-z0-9]+", t))


def prepare_thresholds(raw: pd.DataFrame, columns: dict = None, fw_label: str = "FW", res_label: str = "RES"):
    """
    The thresholds table in a fixed shape: one row per threshold with fffs_id, key, family, kind ("fw" or "res"),
    value, target_lead_hours and area_code. A row whose type is neither label, or whose value is not a number, is
    dropped. Returns (thresholds, report); the report has the resolved columns, the type values found with their
    counts, and the numbers dropped.
    """
    cols = resolve_columns(raw, columns)
    df = pd.DataFrame({"fffs_id": raw[cols["fffs_id"]].map(clean)})
    df["key"] = df["fffs_id"].map(id_key)
    param = raw[cols["parameter"]] if cols["parameter"] else pd.Series([None] * len(raw), index=raw.index)
    df["parameter"] = param.map(clean).values
    df["family"] = [family_of_code(p) or family_of_text(p) for p in df["parameter"]]
    types = raw[cols["type"]].map(clean)
    df["kind"] = [("res" if _is_label(t, res_label) else "fw" if _is_label(t, fw_label) else None) for t in types]
    df["value"] = pd.to_numeric(raw[cols["value"]], errors="coerce").values
    df["target_lead_hours"] = pd.to_numeric(raw[cols["lead"]], errors="coerce").values if cols["lead"] else float("nan")
    df["area_code"] = raw[cols["area"]].map(clean).values if cols["area"] else None
    report = {"columns": cols, "type_values": types.value_counts(dropna=False).to_dict(), "rows": int(len(df)),
              "dropped_no_kind": int(df["kind"].isna().sum()), "dropped_no_value": int((df["kind"].notna() & df["value"].isna()).sum())}
    df = df[df["kind"].notna() & df["value"].notna() & df["key"].notna()].reset_index(drop=True)
    return df, report


def gauge_thresholds(thr: pd.DataFrame) -> pd.DataFrame:
    """
    One row per gauge (FFFS key, family, area) with fw_value, res_value and target_lead_hours. Where a gauge has several
    FW or RES thresholds the lowest of each is used, and the target is the one on the RES row (else the FW row). A gauge with
    no RES threshold has nothing to validate against and is left out.
    """
    out = []
    for (key, family, area), g in thr.groupby(["key", "family", "area_code"], dropna=False):
        res, fw = g[g["kind"] == "res"], g[g["kind"] == "fw"]
        if res.empty:
            continue
        lead = res["target_lead_hours"].dropna()
        lead = lead if len(lead) else fw["target_lead_hours"].dropna()
        out.append({"key": key, "fffs_id": g["fffs_id"].iloc[0], "family": family, "area_code": area,
                    "res_value": float(res["value"].min()), "fw_value": float(fw["value"].min()) if len(fw) else float("nan"),
                    "target_lead_hours": float(lead.min()) if len(lead) else float("nan")})
    return pd.DataFrame(out, columns=["key", "fffs_id", "family", "area_code", "res_value", "fw_value", "target_lead_hours"])


def choose_measure(lines: pd.DataFrame):
    """
    The measure whose readings stand for a gauge, from its matched mapping lines (measure_uri, register_value_type,
    n_readings): instantaneous first, then the most readings, then the URI. Returns the measure_uri, or None.
    """
    g = lines[lines["measure_uri"].notna()]
    if g.empty:
        return None
    inst = g["register_value_type"].map(lambda v: 0 if (clean(v) or "").lower() == "instantaneous" else 1) if "register_value_type" in g else 1
    order = g.assign(_inst=inst, _n=pd.to_numeric(g.get("n_readings"), errors="coerce").fillna(0)).sort_values(
        ["_inst", "_n", "measure_uri"], ascending=[True, False, True])
    return order["measure_uri"].iloc[0]


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
