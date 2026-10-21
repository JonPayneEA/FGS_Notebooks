# Tests for the warning history rules. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.warning_history import (  # noqa: E402
    area_type, build_history, normalise_messages,
)

A, W, S, OFF = "Flood alert", "Flood warning", "Severe flood warning", "Warning no longer in force"


def raw(rows):
    """rows: (code, ISO time, severity text)."""
    return pd.DataFrame({
        "TACode": [r[0] for r in rows],
        "dateTime": [r[1] for r in rows],
        "Severity": [r[2] for r in rows],
        "severity": [None] * len(rows),
        "Source": ["FWS"] * len(rows),
    })


def history(rows, as_of=None):
    events, report = normalise_messages(raw(rows))
    return build_history(events, as_of=as_of), report


def test_an_alert_runs_from_flood_alert_to_no_longer_in_force():
    (ep, per, _), _ = history([("011WAFDW", "2026-06-01T09:00:00Z", A), ("011WAFDW", "2026-06-02T09:30:00Z", OFF)])
    assert len(ep) == 1 and len(per) == 1
    e = ep.iloc[0]
    assert e["area_type"] == "flood_alert" and e["start_level"] == "flood_alert"
    assert e["duration_minutes"] == 24 * 60 + 30 and e["duration_hours"] == 24.5
    assert e["ended_by"] == "no_longer_in_force" and not e["is_open"] and not e["had_severe"]


def test_a_warning_is_one_episode_and_updates_do_not_split_it():
    (ep, per, _), _ = history([
        ("011FWFNC1D", "2026-06-01T08:00:00Z", W), ("011FWFNC1D", "2026-06-01T10:00:00Z", W),   # an update
        ("011FWFNC1D", "2026-06-01T12:00:00Z", OFF)])
    assert len(ep) == 1 and len(per) == 1
    assert ep.iloc[0]["duration_hours"] == 4.0 and ep.iloc[0]["n_messages"] == 2
    assert per.iloc[0]["level"] == "flood_warning"


def test_a_severe_warning_comes_from_a_warning_and_goes_back_down_a_level():
    (ep, per, _), _ = history([
        ("011FWFNC1D", "2026-06-01T08:00:00Z", W),
        ("011FWFNC1D", "2026-06-01T10:00:00Z", S),
        ("011FWFNC1D", "2026-06-01T10:30:00Z", S),     # update at severe
        ("011FWFNC1D", "2026-06-01T14:00:00Z", W),     # turned down a level
        ("011FWFNC1D", "2026-06-01T18:00:00Z", OFF)])
    assert len(ep) == 1
    e = ep.iloc[0]
    assert e["duration_hours"] == 10.0                  # the whole time in force, severe included
    assert e["peak_level"] == "severe_flood_warning" and e["had_severe"]
    assert e["severe_minutes"] == 240.0 and e["n_severe_periods"] == 1
    assert list(per["level"]) == ["flood_warning", "severe_flood_warning", "flood_warning"]
    assert list(per["started_by"]) == ["issued", "upgraded", "downgraded"]
    assert list(per["ended_by"]) == ["upgraded", "downgraded", "no_longer_in_force"]
    assert list(per["duration_hours"]) == [2.0, 4.0, 4.0]


def test_severe_can_be_switched_straight_off():
    (ep, per, _), _ = history([("A1FWFX", "2026-06-01T08:00:00Z", W), ("A1FWFX", "2026-06-01T09:00:00Z", S),
                               ("A1FWFX", "2026-06-01T11:00:00Z", OFF)])
    assert list(per["ended_by"]) == ["upgraded", "no_longer_in_force"]
    assert ep.iloc[0]["severe_minutes"] == 120.0


def test_two_runs_in_the_same_area_are_two_episodes_and_areas_stay_apart():
    (ep, _, _), _ = history([
        ("011FWFNC1D", "2026-06-01T08:00:00Z", W), ("011FWFNC1D", "2026-06-01T09:00:00Z", OFF),
        ("011FWFNC1D", "2026-06-05T08:00:00Z", W), ("011FWFNC1D", "2026-06-05T11:00:00Z", OFF),
        ("012FWFL59A", "2026-06-02T08:00:00Z", W), ("012FWFL59A", "2026-06-02T08:30:00Z", OFF)])
    assert len(ep) == 3
    assert sorted(ep["duration_minutes"]) == [30.0, 60.0, 180.0]
    assert ep["episode_id"].is_unique


def test_still_in_force_at_the_end_of_the_data_is_open_and_measured_to_there():
    (ep, per, _), _ = history([("011WAFDW", "2026-06-08T09:48:00Z", A)], as_of="2026-06-08T21:48:00")
    e = ep.iloc[0]
    assert e["is_open"] and pd.isna(e["end_time"]) and e["ended_by"] == "open"
    assert e["duration_hours"] == 12.0 and not e["stale_open"]
    assert bool(per.iloc[0]["is_open"])


def test_an_open_episode_over_thirty_days_old_is_flagged_as_probably_missing_its_end():
    (ep, _, _), _ = history([("011WAFDW", "2026-01-01T09:00:00Z", A), ("OTHER1WAFX", "2026-06-08T09:00:00Z", A)])
    old = ep[ep["ea_area_code"] == "011WAFDW"].iloc[0]
    assert old["is_open"] and old["stale_open"]


def test_a_switch_off_with_nothing_on_is_counted_and_ignored():
    (ep, _, stats), _ = history([("011WAFDW", "2026-06-01T09:00:00Z", OFF)])
    assert len(ep) == 0 and stats["orphan_off"] == 1


def test_severe_with_no_warning_before_it_starts_an_episode_at_severe():
    (ep, per, _), _ = history([("011FWFNC1D", "2026-06-01T09:00:00Z", S), ("011FWFNC1D", "2026-06-01T10:00:00Z", OFF)])
    assert ep.iloc[0]["start_level"] == "severe_flood_warning" and ep.iloc[0]["severe_minutes"] == 60.0


def test_messages_at_the_same_moment_apply_the_switch_off_last():
    (ep, _, _), _ = history([("011FWFNC1D", "2026-06-01T09:00:00Z", OFF), ("011FWFNC1D", "2026-06-01T09:00:00Z", W)])
    assert len(ep) == 1 and ep.iloc[0]["duration_minutes"] == 0.0 and not ep.iloc[0]["is_open"]


def test_messages_are_read_flexibly_and_bad_rows_are_counted():
    df = pd.DataFrame({
        "TACode": ["011WAFDW", "011WAFDW", "011WAFDW", None, "011WAFDW", "011WAFDW"],
        "dateTime": ["2026-06-01T09:00:00Z", "2026-06-01T09:00:00Z", None, "2026-06-01T09:00:00Z",
                     "2026-06-01T10:00:00Z", "2026-06-01T11:00:00Z"],
        "timeOfChange": [None, None, "bad", None, None, None],
        "Severity": [" flood ALERT ", "Flood alert", "Flood alert", "Flood alert", "Something odd", OFF],
        "severity": [3, 3, 3, 3, 9, 4],
    })
    events, report = normalise_messages(df)
    assert list(events["level"]) == ["flood_alert", "off"]
    assert report["rows"] == 6 and report["no_code"] == 1 and report["no_time"] == 1
    assert report["duplicates"] == 1 and report["unknown_severity"] == 1
    assert report["unknown_values"] == ["Something odd"]


def test_the_numeric_severity_is_used_when_there_is_no_text_and_a_disagreement_is_counted():
    df = pd.DataFrame({"TACode": ["X1", "X1"], "dateTime": ["2026-06-01T09:00:00Z", "2026-06-01T10:00:00Z"],
                       "severity": [3, 4]})
    events, _ = normalise_messages(df)
    assert list(events["level"]) == ["flood_alert", "off"]
    both = pd.DataFrame({"TACode": ["X1"], "dateTime": ["2026-06-01T09:00:00Z"], "Severity": [W], "severity": [3]})
    events, report = normalise_messages(both)
    assert list(events["level"]) == ["flood_warning"] and report["disagreeing_levels"] == 1


def test_the_time_falls_back_to_time_of_change_text():
    df = pd.DataFrame({"TACode": ["X1"], "dateTime": [None], "timeOfChange": ["08/06/2026 9:48am"], "Severity": [A]})
    events, _ = normalise_messages(df)
    assert events.iloc[0]["time"] == pd.Timestamp("2026-06-08 09:48")


def test_a_missing_column_gives_a_clear_error():
    with pytest.raises(ValueError, match="No area code column"):
        normalise_messages(pd.DataFrame({"x": [1]}))
    with pytest.raises(ValueError, match="No severity column"):
        normalise_messages(pd.DataFrame({"TACode": ["X1"]}))


def test_area_type_comes_from_the_code():
    assert area_type("011WAFDW") == "flood_alert" and area_type("012FWFL59A") == "flood_warning"
    assert area_type("011FWCX") == "flood_warning" and area_type("011WATX") == "flood_alert"
    assert area_type("") == "unknown"
