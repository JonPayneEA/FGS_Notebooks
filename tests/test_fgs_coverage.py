# Tests for warning coverage by the FGS. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.fgs_coverage import category, cover_episodes, episode_days  # noqa: E402

NOW = pd.Timestamp("2026-10-21 12:00")


def episodes(rows):
    """rows: (episode_id, area, type, start, end or None, stale)."""
    return pd.DataFrame({
        "episode_id": [r[0] for r in rows], "ea_area_code": [r[1] for r in rows], "area_type": [r[2] for r in rows],
        "start_time": pd.to_datetime([r[3] for r in rows]), "end_time": pd.to_datetime([r[4] for r in rows]),
        "stale_open": [r[5] for r in rows]})


def coverage(rows):
    """rows: (area, forecast date, issue day, rank, sources)."""
    return pd.DataFrame(rows, columns=["ea_area_code", "d", "s", "rk", "sources"])


def one(ep, cov):
    return cover_episodes(ep, cov, NOW).set_index("episode_id")


def cat(r, i, rank=1):
    return category(r.loc[i, "issue_date"], r.loc[i, f"first_cover_ge{rank}"], r.loc[i, "later_rank"], rank)


def test_before_same_day_later_or_none_is_decided_by_the_issue_day():
    ep = episodes([("b", "A", "flood_warning", "2026-10-10 14:00", "2026-10-12 20:00", False),
                   ("s", "B", "flood_warning", "2026-10-10 14:00", "2026-10-12 20:00", False),
                   ("l", "C", "flood_warning", "2026-10-10 14:00", "2026-10-12 20:00", False),
                   ("n", "D", "flood_warning", "2026-10-10 14:00", "2026-10-12 20:00", False)])
    cov = coverage([("A", "2026-10-10", "2026-10-08", 3, "river"),       # covered the issue day, two days ahead
                    ("B", "2026-10-10", "2026-10-10", 3, "river"),       # covered the issue day, issued the same day
                    ("C", "2026-10-11", "2026-10-10", 3, "river"),       # only a later day of the warning
                    ("C", "2026-10-12", "2026-10-11", 3, "river")])
    r = one(ep, cov)
    assert [cat(r, i) for i in "bsln"] == ["before", "same_day", "later", "none"]
    assert r.loc["b", "rank_before"] == 3 and r.loc["b", "rank_same_day"] == 0
    assert r.loc["l", "issue_day_rank"] == 0 and r.loc["l", "later_rank"] == 3 and r.loc["l", "max_rank"] == 3
    assert r.loc["n", "max_rank"] == 0 and pd.isna(r.loc["n", "sources"])


def test_a_polygon_that_persists_for_the_duration_does_not_change_a_before():
    ep = episodes([("e", "A", "flood_warning", "2026-10-10 14:00", "2026-10-13 20:00", False)])
    cov = coverage([("A", "2026-10-10", "2026-10-09", 3, "river"),       # the day before: notice
                    ("A", "2026-10-10", "2026-10-10", 3, "river"),
                    ("A", "2026-10-11", "2026-10-10", 3, "river"),       # the polygon stays for the duration
                    ("A", "2026-10-12", "2026-10-11", 3, "river"),
                    ("A", "2026-10-13", "2026-10-12", 3, "river")])
    r = one(ep, cov)
    assert cat(r, "e") == "before"
    assert (r.loc["e", "issue_date"] - r.loc["e", "first_cover_ge1"]).days == 1


def test_a_polygon_for_a_later_day_issued_before_the_warning_is_not_notice_of_the_issue_day():
    ep = episodes([("e", "A", "flood_warning", "2026-10-10 14:00", "2026-10-12 20:00", False)])
    cov = coverage([("A", "2026-10-11", "2026-10-09", 3, "river")])      # issued 9 Oct but only for 11 Oct
    r = one(ep, cov)
    assert cat(r, "e") == "later"
    assert pd.isna(r.loc["e", "first_cover_ge1"]) and r.loc["e", "rank_before"] == 0


def test_the_threshold_changes_the_category_and_the_lead():
    ep = episodes([("e", "A", "flood_warning", "2026-10-10 14:00", "2026-10-11 20:00", False)])
    cov = coverage([("A", "2026-10-10", "2026-10-07", 1, "river"),       # Very Low, three days ahead
                    ("A", "2026-10-10", "2026-10-09", 3, "river"),       # Medium, the day before
                    ("A", "2026-10-11", "2026-10-11", 4, "coastal")])    # High, only on a later day
    r = one(ep, cov)
    assert cat(r, "e", 1) == "before" and (r.loc["e", "issue_date"] - r.loc["e", "first_cover_ge1"]).days == 3
    assert cat(r, "e", 3) == "before" and (r.loc["e", "issue_date"] - r.loc["e", "first_cover_ge3"]).days == 1
    assert cat(r, "e", 4) == "later"
    assert r.loc["e", "max_rank"] == 4 and r.loc["e", "sources"] == "river"   # the issue day's sources decide


def test_dates_outside_the_time_in_force_do_not_count():
    ep = episodes([("e", "A", "flood_alert", "2026-10-10 22:00", "2026-10-12 03:00", False)])
    cov = coverage([("A", "2026-10-09", "2026-10-08", 4, "river"),       # the day before the alert began
                    ("A", "2026-10-13", "2026-10-12", 4, "river")])      # after it ended
    r = one(ep, cov)
    assert cat(r, "e") == "none" and r.loc["e", "max_rank"] == 0


def test_an_open_episode_runs_to_now_and_a_stale_one_counts_only_its_first_day():
    ep = episodes([("open", "A", "flood_warning", "2026-10-20 08:00", None, False),
                   ("stale", "B", "flood_alert", "2026-08-01 08:00", None, True)])
    days = episode_days(ep, NOW)
    assert days[days["episode_id"] == "open"]["d"].dt.strftime("%m-%d").tolist() == ["10-20", "10-21"]
    assert days[days["episode_id"] == "stale"]["d"].dt.strftime("%m-%d").tolist() == ["08-01"]


def test_a_very_long_span_is_capped():
    ep = episodes([("x", "A", "flood_alert", "2020-01-01 00:00", "2026-01-01 00:00", False)])
    assert len(episode_days(ep, NOW)) == 60


def test_no_coverage_at_all_still_returns_every_episode():
    ep = episodes([("e", "A", "flood_warning", "2026-10-10 14:00", "2026-10-10 20:00", False)])
    r = cover_episodes(ep, coverage([]), NOW)
    assert len(r) == 1 and r.loc[0, "max_rank"] == 0 and pd.isna(r.loc[0, "first_cover_ge1"]) and r.loc[0, "later_rank"] == 0


def test_an_area_covered_for_other_areas_only_is_none():
    ep = episodes([("e", "A", "flood_warning", "2026-10-10 14:00", "2026-10-10 20:00", False)])
    r = one(ep, coverage([("Z", "2026-10-10", "2026-10-09", 4, "river")])).loc["e"]
    assert r["max_rank"] == 0
