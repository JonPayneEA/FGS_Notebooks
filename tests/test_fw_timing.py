# Tests for flood warning timing validation. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.fw_timing import (  # noqa: E402
    choose_measure, gauge_thresholds, prepare_thresholds, resolve_columns, summarise, upcrossings, validate_gauge, warning_issues,
)

T0 = pd.Timestamp("2026-01-01 00:00")


def series(values, start=T0, step_hours=1):
    return pd.DataFrame({"date_time": [start + pd.Timedelta(hours=step_hours * i) for i in range(len(values))], "value": values})


RAW = pd.DataFrame({"FFFS_ID": ["F1", "F1", "F1", "F2", "F2", "F3"], "ParameterID": ["H.obs"] * 6,
                    "ThresholdType": ["FW", "RES", "Alert", "RES", "FW", "FW"], "Value": ["2.0", "3.0", "1.0", "5.0", "4.0", "x"],
                    "TargetLeadTime": [None, "6", None, "2", None, None], "WarningArea": ["011FWFA", "011FWFA", "011FWFA", "011FWFB", "011FWFB", "011FWFC"]})


def test_the_thresholds_table_is_read_by_guessed_or_named_columns_and_labels():
    thr, rep = prepare_thresholds(RAW)
    assert rep["columns"]["fffs_id"] == "FFFS_ID" and rep["columns"]["lead"] == "TargetLeadTime"
    assert rep["dropped_no_kind"] == 1 and rep["dropped_no_value"] == 1 and len(thr) == 4        # "Alert" and the non-number are dropped
    g = gauge_thresholds(thr).set_index("fffs_id")
    assert g.loc["F1", "res_value"] == 3.0 and g.loc["F1", "fw_value"] == 2.0 and g.loc["F1", "target_lead_hours"] == 6.0
    assert g.loc["F2", "target_lead_hours"] == 2.0 and "F3" not in g.index                       # no RES threshold: nothing to validate
    renamed = RAW.rename(columns={"Value": "Level (m)"})
    assert resolve_columns(renamed, {"value": "level m"})["value"] == "Level (m)"
    try:
        resolve_columns(RAW.drop(columns=["Value"]))
        assert False
    except ValueError as e:
        assert "Columns present" in str(e)


def test_crossings_count_once_until_the_readings_have_stayed_below_for_the_reset_time():
    s = series([0, 3, 3, 2, 3, 0, 0, 0, 0, 0, 0, 0, 0, 3])
    assert upcrossings(s, 3.0, reset_hours=6) == [T0 + pd.Timedelta(hours=1), T0 + pd.Timedelta(hours=13)]   # the flicker at hour 4 is the same event
    assert upcrossings(series([5, 5, 0]), 3.0) == []                                                          # started above: no crossing seen


def issues(*rows):
    return pd.DataFrame(rows, columns=["episode_id", "ea_area_code", "issue_time", "end_time"]).assign(
        issue_time=lambda d: pd.to_datetime(d["issue_time"]), end_time=lambda d: pd.to_datetime(d["end_time"]))


def test_a_warning_issued_before_the_res_crossing_is_on_time_only_if_it_met_the_target():
    gauge = {"fffs_id": "F1", "family": "level", "area_code": "A", "res_value": 3.0, "fw_value": 2.0, "target_lead_hours": 6.0}
    s = series([0, 0, 2.5, 2.6, 2.7, 2.8, 2.9, 3.2, 3.4, 3.0, 1, 0], step_hours=2)              # FW crossed at hour 4; RES at hour 14
    on_time = validate_gauge(gauge, s, issues(("e1", "A", T0 + pd.Timedelta(hours=6), T0 + pd.Timedelta(hours=30))))
    assert [r["outcome"] for r in on_time] == ["on time"] and on_time[0]["lead_hours"] == 8.0
    assert on_time[0]["trigger_time"] == T0 + pd.Timedelta(hours=4) and on_time[0]["issue_delay_hours"] == 2.0
    short = validate_gauge(gauge, s, issues(("e1", "A", T0 + pd.Timedelta(hours=11), T0 + pd.Timedelta(hours=30))))
    assert short[0]["outcome"] == "too short" and short[0]["lead_hours"] == 3.0


def test_late_missed_and_res_not_reached_and_no_readings():
    gauge = {"fffs_id": "F1", "family": "level", "area_code": "A", "res_value": 3.0, "fw_value": 2.0, "target_lead_hours": 6.0}
    s = series([0, 0, 2.5, 3.4, 3.0, 1, 0, 0], step_hours=2)                                       # RES crossed at hour 6
    late = validate_gauge(gauge, s, issues(("e1", "A", T0 + pd.Timedelta(hours=9), T0 + pd.Timedelta(hours=30))))
    assert [r["outcome"] for r in late] == ["late"] and late[0]["lead_hours"] == -3.0
    missed = validate_gauge(gauge, s, issues())
    assert [r["outcome"] for r in missed] == ["missed"]
    far = validate_gauge(gauge, s, issues(("e1", "A", T0 + pd.Timedelta(hours=60), T0 + pd.Timedelta(hours=70))))
    assert sorted(r["outcome"] for r in far) == ["missed", "no readings"]
    calm = series([0, 0.5, 0.6, 0.4], step_hours=2)
    assert [r["outcome"] for r in validate_gauge(gauge, calm, issues(("e1", "A", T0 + pd.Timedelta(hours=2), T0 + pd.Timedelta(hours=6))))] == ["RES not reached"]


def test_warning_issues_take_the_first_warning_level_period_and_ignore_alerts():
    periods = pd.DataFrame({"episode_id": ["e1", "e1", "e2"], "ea_area_code": ["A", "A", "B"],
                            "level": ["flood_alert", "flood_warning", "flood_alert"],
                            "start_time": ["2026-01-01 00:00", "2026-01-01 05:00", "2026-01-02 00:00"]})
    episodes = pd.DataFrame({"episode_id": ["e1", "e2"], "end_time": ["2026-01-01 20:00", None]})
    out = warning_issues(periods, episodes, as_of="2026-02-01")
    assert list(out["episode_id"]) == ["e1"] and out.iloc[0]["issue_time"] == pd.Timestamp("2026-01-01 05:00")


def test_the_measure_is_instantaneous_first_then_most_readings():
    lines = pd.DataFrame({"measure_uri": ["m/mean", "m/inst-few", "m/inst-many"], "register_value_type": ["mean", "instantaneous", "instantaneous"],
                          "n_readings": [9000, 10, 500]})
    assert choose_measure(lines) == "m/inst-many"
    assert choose_measure(lines.iloc[0:0]) is None


def test_the_summary_counts_outcomes_and_their_share_of_res_events():
    r = pd.DataFrame({"kind": ["res_event", "res_event", "res_event", "warning_only"], "outcome": ["on time", "on time", "missed", "RES not reached"],
                      "lead_hours": [8.0, 4.0, float("nan"), float("nan")]})
    s = summarise(r).set_index("outcome")
    assert s.loc["on time", "rows"] == 2 and s.loc["on time", "median_lead_hours"] == 6.0 and abs(s.loc["on time", "share_of_res_events"] - 0.667) < 0.001
