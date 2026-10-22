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


def test_covered_by_a_statement_issued_before_on_the_day_or_after():
    ep = episodes([("b", "A", "flood_warning", "2026-10-10 14:00", "2026-10-10 20:00", False),
                   ("s", "B", "flood_warning", "2026-10-10 14:00", "2026-10-10 20:00", False),
                   ("a", "C", "flood_warning", "2026-10-10 14:00", "2026-10-10 20:00", False),
                   ("n", "D", "flood_warning", "2026-10-10 14:00", "2026-10-10 20:00", False)])
    cov = coverage([("A", "2026-10-10", "2026-10-08", 3, "river"),
                    ("B", "2026-10-10", "2026-10-10", 3, "river"),
                    ("C", "2026-10-10", "2026-10-11", 3, "river")])
    r = one(ep, cov)
    assert [category(r.loc[i, "issue_date"], r.loc[i, "first_cover_ge1"]) for i in "bsan"] == ["before", "same_day", "after", "none"]
    assert r.loc["b", "rank_before"] == 3 and r.loc["b", "rank_same_day"] == 0
    assert r.loc["n", "max_rank"] == 0 and pd.isna(r.loc["n", "sources"])


def test_the_earliest_covering_statement_decides_and_the_threshold_changes_it():
    ep = episodes([("e", "A", "flood_warning", "2026-10-10 14:00", "2026-10-10 20:00", False)])
    cov = coverage([("A", "2026-10-10", "2026-10-07", 1, "river"),     # Very Low, three days ahead
                    ("A", "2026-10-10", "2026-10-09", 3, "river"),     # Medium, the day before
                    ("A", "2026-10-10", "2026-10-11", 4, "coastal")])  # High, after the warning
    r = one(ep, cov).loc["e"]
    assert category(r["issue_date"], r["first_cover_ge1"]) == "before"
    assert (r["issue_date"] - r["first_cover_ge1"]).days == 3
    assert (r["issue_date"] - r["first_cover_ge3"]).days == 1
    assert category(r["issue_date"], r["first_cover_ge4"]) == "after"  # only the late High polygon reaches High
    assert r["max_rank"] == 4 and r["sources"] == "coastal,river"


def test_every_date_the_warning_was_in_force_counts_but_dates_outside_do_not():
    ep = episodes([("e", "A", "flood_alert", "2026-10-10 22:00", "2026-10-12 03:00", False)])
    cov = coverage([("A", "2026-10-09", "2026-10-08", 4, "river"),    # the day before the alert began: no
                    ("A", "2026-10-12", "2026-10-11", 2, "river"),    # the alert's last day, forecast the day before the day itself
                    ("A", "2026-10-13", "2026-10-12", 4, "river")])   # after it ended: no
    r = one(ep, cov).loc["e"]
    assert r["max_rank"] == 2
    assert category(r["issue_date"], r["first_cover_ge1"]) == "after"  # issued 11 Oct, a day after the alert began on 10 Oct


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
    assert len(r) == 1 and r.loc[0, "max_rank"] == 0 and pd.isna(r.loc[0, "first_cover_ge1"])


def test_an_area_covered_for_other_areas_only_is_none():
    ep = episodes([("e", "A", "flood_warning", "2026-10-10 14:00", "2026-10-10 20:00", False)])
    r = one(ep, coverage([("Z", "2026-10-10", "2026-10-09", 4, "river")])).loc["e"]
    assert r["max_rank"] == 0
