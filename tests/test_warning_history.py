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


# --- the live log (flood_warnings_log) -------------------------------------------------

def log_rows(rows):
    """rows: (area, change_type, severity_level, severity_changed, message_changed, polled)."""
    return pd.DataFrame({
        "flood_area_id": [r[0] for r in rows], "change_type": [r[1] for r in rows],
        "severity_level": [r[2] for r in rows], "time_severity_changed": [r[3] for r in rows],
        "time_message_changed": [r[4] for r in rows], "ingested_at": [r[5] for r in rows],
    })


def test_log_events_use_the_eas_own_times_and_levels():
    from utils.warning_history import events_from_log
    ev, rep = events_from_log(log_rows([
        ("011FWFNC1D", "NEW", 2, "2026-07-01T09:00:00", None, "2026-07-01T09:20:00"),
        ("011FWFNC1D", "MESSAGE_CHANGE", 2, "2026-07-01T09:00:00", "2026-07-01T11:00:00", "2026-07-01T11:10:00"),
        ("011FWFNC1D", "SEVERITY_CHANGE", 1, "2026-07-01T13:00:00", "2026-07-01T13:00:00", "2026-07-01T13:10:00"),
        ("011FWFNC1D", "SEVERITY_CHANGE", 4, "2026-07-01T20:00:00", "2026-07-01T20:00:00", "2026-07-01T20:10:00"),
        ("011FWFNC1D", "CLOSED", 4, None, None, "2026-07-02T21:00:00"),            # the 4 was seen: nothing to add
    ]))
    assert list(ev["level"]) == ["flood_warning", "flood_warning", "severe_flood_warning", "off"]
    assert list(ev["time"]) == list(pd.to_datetime(["2026-07-01 09:00", "2026-07-01 11:00", "2026-07-01 13:00", "2026-07-01 20:00"]))
    assert (ev["origin"] == "flood_warnings_log").all() and rep["closed_fallback"] == 0


def test_a_closed_without_a_level_4_is_a_switch_off_at_the_poll_that_noticed():
    from utils.warning_history import events_from_log, build_history, combine_events
    ev, rep = events_from_log(log_rows([
        ("011WAFDW", "NEW", 3, "2026-07-01T09:00:00", None, "2026-07-01T09:20:00"),
        ("011WAFDW", "CLOSED", 3, None, None, "2026-07-01T15:30:00"),
    ]))
    assert rep["closed_fallback"] == 1 and list(ev["kind"]) == ["message", "closed_fallback"]
    (episodes, periods, _) = build_history(ev)
    assert episodes.iloc[0]["ended_by"] == "dropped_from_feed" and episodes.iloc[0]["duration_hours"] == 6.5
    assert periods.iloc[0]["ended_by"] == "dropped_from_feed"
    assert episodes.iloc[0]["start_origin"] == episodes.iloc[0]["end_origin"] == "flood_warnings_log"


def test_the_file_and_the_log_join_into_one_episode_with_both_origins():
    from utils.warning_history import events_from_log, combine_events
    file_ev, _ = normalise_messages(raw([("011FWFNC1D", "2026-06-07T08:00:00Z", W)]))
    log_ev, _ = events_from_log(log_rows([
        ("011FWFNC1D", "SEVERITY_CHANGE", 4, "2026-06-09T10:00:00", "2026-06-09T10:00:00", "2026-06-09T10:10:00")]))
    events, dups = combine_events(file_ev, log_ev)
    ep, per, _ = build_history(events, gap=(pd.Timestamp("2026-06-08"), pd.Timestamp("2026-06-09")))
    e = ep.iloc[0]
    assert len(ep) == 1 and e["start_origin"] == "history_file" and e["end_origin"] == "flood_warnings_log"
    assert e["duration_hours"] == 50.0 and bool(e["spans_gap"])         # it crosses the stretch no source covers


def test_a_repeat_in_both_sources_is_kept_once_and_an_episode_inside_one_source_does_not_span_the_gap():
    from utils.warning_history import events_from_log, combine_events
    file_ev, _ = normalise_messages(raw([("011FWFNC1D", "2026-06-07T08:00:00Z", W), ("011FWFNC1D", "2026-06-07T12:00:00Z", OFF)]))
    log_ev, _ = events_from_log(log_rows([
        ("011FWFNC1D", "NEW", 2, "2026-06-07T08:00:00", None, "2026-06-07T08:20:00")]))      # the same message
    events, dups = combine_events(file_ev, log_ev)
    assert dups == 1 and list(events["origin"]) == ["history_file", "history_file"]       # the file's copy wins
    ep, _, _ = build_history(events, gap=(pd.Timestamp("2026-06-08"), pd.Timestamp("2026-06-09")))
    assert not bool(ep.iloc[0]["spans_gap"])


def test_an_area_still_on_when_the_file_ends_and_never_seen_again_is_open_across_the_gap():
    from utils.warning_history import combine_events
    file_ev, _ = normalise_messages(raw([("011FWFNC1D", "2026-06-07T08:00:00Z", W)]))
    events, _ = combine_events(file_ev)
    ep, _, _ = build_history(events, as_of=pd.Timestamp("2026-10-20"),
                             gap=(pd.Timestamp("2026-06-08"), pd.Timestamp("2026-07-01")))
    assert bool(ep.iloc[0]["is_open"]) and bool(ep.iloc[0]["spans_gap"]) and bool(ep.iloc[0]["stale_open"])


def test_bad_log_rows_are_counted():
    from utils.warning_history import events_from_log
    ev, rep = events_from_log(log_rows([
        (None, "NEW", 2, "2026-07-01T09:00:00", None, "2026-07-01T09:20:00"),
        ("A1FWFX", "NEW", 9, "2026-07-01T09:00:00", None, "2026-07-01T09:20:00"),
        ("A1FWFX", "NEW", 2, None, None, None)]))
    assert len(ev) == 0 and rep["no_code"] == 1 and rep["unknown_level"] == 1 and rep["no_time"] == 1
