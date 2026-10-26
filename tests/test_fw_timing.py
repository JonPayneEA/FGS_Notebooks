# Tests for flood warning timing validation. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.fw_timing import (  # noqa: E402
    gauges_from_links, summarise, upcrossings, validate_gauge, warning_issues,
)

T0 = pd.Timestamp("2026-01-01 00:00")


def series(values, start=T0, step_hours=1):
    return pd.DataFrame({"date_time": [start + pd.Timedelta(hours=step_hours * i) for i in range(len(values))], "value": values})


def link_row(key, type_, purpose, value, area, lead="180", status="linked", season="All", uri="u1", unit="mALD"):
    return {"key": key, "fffs_id": key, "family": "level", "measure_uri": uri, "type": type_, "purpose": purpose, "value": value, "unit": unit,
            "target_area": area, "target_lead_minutes": lead, "season": season, "link_status": status}


LINKS = pd.DataFrame([
    link_row("130TH", "RES", "FAL", "0.99", "061WAF23Crk"),                    # the alert-level RES row is not used
    link_row("130TH", "ACTCON", "FW", "1.29", "061FWF23Ewen"),
    link_row("130TH", "RES", "FW", "1.94", "061FWF23Ewen", lead="360"),
    link_row("144TH", "RES", "FW", "1.6", "061FWF23SKCrk", uri="u2"),
    link_row("144TH", "RES", "FW", "1.8", "061FWF23SKCrk", uri="u2"),         # two RES rows: the lowest is used
    link_row("155TH", "RES", "FW", "1.55", "061FWF23Swill", status="unit_mismatch", uri=None),      # not linked: left out
    link_row("184TH", "ACTCON", "FW", "1.5", "061FWF23Key", uri="u4"),         # no RES row: nothing to validate
    link_row("200TH", "RES", "FW", "2.0", "061FWF23Win", season="Winter", uri="u5"),               # another season
])


def test_gauges_come_from_the_linked_res_and_trigger_rows_only():
    g = gauges_from_links(LINKS).set_index("fffs_id")
    assert set(g.index) == {"130TH", "144TH"}
    assert g.loc["130TH", "res_value"] == 1.94 and g.loc["130TH", "fw_value"] == 1.29        # RES/FW and ACTCON/FW, not the alert row
    assert g.loc["130TH", "target_lead_hours"] == 6.0                                        # minutes in the file, hours here
    assert g.loc["130TH", "area_code"] == "061FWF23Ewen" and g.loc["130TH", "measure_uri"] == "u1" and g.loc["130TH", "unit"] == "mALD"
    assert g.loc["144TH", "res_value"] == 1.6 and pd.isna(g.loc["144TH", "fw_value"])
    assert "155TH" not in g.index and "184TH" not in g.index and "200TH" not in g.index
    assert "200TH" in set(gauges_from_links(LINKS, season="Winter")["fffs_id"])
    assert gauges_from_links(LINKS.iloc[0:0]).empty


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


def test_the_summary_counts_outcomes_and_their_share_of_res_events():
    r = pd.DataFrame({"kind": ["res_event", "res_event", "res_event", "warning_only"], "outcome": ["on time", "on time", "missed", "RES not reached"],
                      "lead_hours": [8.0, 4.0, float("nan"), float("nan")]})
    s = summarise(r).set_index("outcome")
    assert s.loc["on time", "rows"] == 2 and s.loc["on time", "median_lead_hours"] == 6.0 and abs(s.loc["on time", "share_of_res_events"] - 0.667) < 0.001
