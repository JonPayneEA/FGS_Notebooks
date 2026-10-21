# Tests for forecast verification. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.verification import (  # noqa: E402
    build_verification, daily_in_force, spans_from_episodes,
)

NOW = pd.Timestamp("2026-10-21 12:00")
LAST = "2026-10-20"


def episodes(rows):
    """rows: (episode_id, area, type, start, end or None, stale)."""
    return pd.DataFrame({
        "episode_id": [r[0] for r in rows], "ea_area_code": [r[1] for r in rows], "area_type": [r[2] for r in rows],
        "start_time": pd.to_datetime([r[3] for r in rows]), "end_time": pd.to_datetime([r[4] for r in rows]),
        "stale_open": [r[5] for r in rows]})


def links(pairs):
    return pd.DataFrame(pairs, columns=["ea_area_code", "constituency_id"])


def test_a_multi_day_episode_counts_on_every_date_it_touches():
    ep = episodes([("e1", "A", "flood_warning", "2026-10-10 22:00", "2026-10-12 03:00", False)])
    d = daily_in_force(spans_from_episodes(ep, None, NOW), links([("A", "c1")]), LAST)
    assert list(d["date"].dt.strftime("%m-%d")) == ["10-10", "10-11", "10-12"]
    assert list(d["warnings"]) == [1, 1, 1] and list(d["alerts"]) == [0, 0, 0]


def test_areas_count_once_per_constituency_and_date_however_often_issued():
    ep = episodes([("e1", "A", "flood_alert", "2026-10-10 01:00", "2026-10-10 05:00", False),
                   ("e2", "A", "flood_alert", "2026-10-10 18:00", "2026-10-10 22:00", False),
                   ("e3", "B", "flood_alert", "2026-10-10 09:00", "2026-10-10 10:00", False)])
    d = daily_in_force(spans_from_episodes(ep, None, NOW), links([("A", "c1"), ("B", "c1")]), LAST)
    assert d.loc[0, "alerts"] == 2                       # areas A and B, A not twice


def test_an_area_in_two_constituencies_counts_in_both():
    ep = episodes([("e1", "A", "flood_warning", "2026-10-10 01:00", "2026-10-10 05:00", False)])
    d = daily_in_force(spans_from_episodes(ep, None, NOW), links([("A", "c1"), ("A", "c2")]), LAST)
    assert sorted(d["constituency_id"]) == ["c1", "c2"]


def test_an_open_episode_runs_to_now_but_a_stale_one_counts_only_its_first_day():
    ep = episodes([("open", "A", "flood_warning", "2026-10-18 09:00", None, False),
                   ("stale", "B", "flood_alert", "2026-08-01 09:00", None, True)])
    d = daily_in_force(spans_from_episodes(ep, None, NOW), links([("A", "c1"), ("B", "c2")]), LAST)
    assert list(d[d["constituency_id"] == "c1"]["date"].dt.strftime("%m-%d")) == ["10-18", "10-19", "10-20"]   # stops at LAST
    assert list(d[d["constituency_id"] == "c2"]["date"].dt.strftime("%m-%d")) == ["08-01"]


def test_severe_periods_count_as_their_own_kind():
    ep = episodes([("e1", "A", "flood_warning", "2026-10-10 01:00", "2026-10-11 20:00", False)])
    per = pd.DataFrame({"episode_id": ["e1"], "ea_area_code": ["A"], "level": ["severe_flood_warning"],
                        "start_time": pd.to_datetime(["2026-10-10 12:00"]), "end_time": pd.to_datetime(["2026-10-10 18:00"])})
    d = daily_in_force(spans_from_episodes(ep, per, NOW), links([("A", "c1")]), LAST).set_index("date")
    assert list(d["warnings"]) == [1, 1] and list(d["severe"]) == [1, 0]


def forecast(rows):
    return pd.DataFrame(rows, columns=["constituency_id", "forecast_date", "lead_day", "forecast_rank"])


def coverage(dates, leads=(1, 2, 3, 4, 5)):
    return pd.DataFrame([(d, k) for d in dates for k in leads], columns=["forecast_date", "lead_day"])


def outcomes(rows):
    return pd.DataFrame(rows, columns=["constituency_id", "date", "alerts", "warnings", "severe"]).assign(
        date=lambda x: pd.to_datetime(x["date"]))


def test_hit_false_alarm_and_miss_each_get_a_row():
    f = forecast([("c1", "2026-10-10", 1, 3),          # forecast and happened: hit
                  ("c2", "2026-10-10", 1, 3)])         # forecast, nothing: false alarm
    o = outcomes([("c1", "2026-10-10", 0, 1, 0),
                  ("c3", "2026-10-10", 1, 0, 0)])      # happened, not forecast: miss
    v = build_verification(f, coverage(["2026-10-10"], leads=(1,)), o, LAST)
    r = v.set_index("constituency_id")
    assert r.loc["c1", ["forecast_rank", "warnings"]].tolist() == [3, 1]
    assert r.loc["c2", ["forecast_rank", "warnings", "alerts"]].tolist() == [3, 0, 0]
    assert r.loc["c3", ["forecast_rank", "alerts"]].tolist() == [0, 1]
    assert len(v) == 3


def test_a_miss_is_scored_only_at_leads_that_had_a_statement():
    o = outcomes([("c1", "2026-10-10", 0, 1, 0)])
    v = build_verification(forecast([]), coverage(["2026-10-10"], leads=(1, 2)), o, LAST)
    assert sorted(v["lead_day"]) == [1, 2]               # not 3 to 5: no statement then, so no miss


def test_the_forecast_rank_is_the_worst_where_repeated_and_a_future_date_is_dropped():
    f = forecast([("c1", "2026-10-10", 2, 2), ("c1", "2026-10-10", 2, 4), ("c1", "2026-10-21", 1, 4)])
    v = build_verification(f, coverage(["2026-10-10", "2026-10-21"]), outcomes([]), LAST)
    assert v["forecast_rank"].tolist() == [4]            # the worst of the two, and today is not scored
    assert v["forecast_date"].tolist() == ["2026-10-10"]


def test_nothing_forecast_and_nothing_in_force_has_no_row():
    v = build_verification(forecast([]), coverage(["2026-10-10"]), outcomes([]), LAST)
    assert len(v) == 0 and list(v.columns)[:3] == ["constituency_id", "forecast_date", "lead_day"]
