# Tests for the MP briefing helpers. Run with:  pytest tests
# Needs pandas only (the areas sentence is passed in, so pyspark is not imported).
import os
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.briefing import (  # noqa: E402
    build_draft, day_dates, name_key, optional_bool, situation_from_intersections,
)

CONST = pd.DataFrame({
    "constituency_id": ["C1", "C2", "C3", "C4"],
    "constituency_name": ["Carlisle", "Penrith & Solway", "Hexham", "Lewes"],
})
MP = pd.DataFrame({
    "constituency_name": ["Carlisle", "Penrith and Solway", "Hexham"],
    "member_id": [3, 2, 1], "name": ["A Smith", "B Jones", "C Brown"],
    "email": ["a@x", "b@x", "c@x"],
})


def inter(rows):
    return pd.DataFrame(rows, columns=["constituency_id", "forecast_date", "day_index", "source",
                                       "risk_level", "intersection_pct"])


ROWS = inter([
    ("C1", "2026-10-19", 1, "river",   "Low",    0.5),
    ("C1", "2026-10-20", 2, "river",   "Medium", 0.5),
    ("C1", "2026-10-20", 2, "surface", "Medium", 0.2),
    ("C1", "2026-10-21", 3, "river",   "Low",    0.5),
    ("C2", "2026-10-20", 2, "river",   "High",   0.001),   # below the floor, ignored
    ("C2", "2026-10-21", 3, "river",   "Low",    0.4),
])


def situation(escalated=None):
    return situation_from_intersections(ROWS, CONST, MP, escalated, 4812, "2026-10-19T09:30:00", 0.01)


def test_worst_rank_date_sources_and_day_ranks():
    s = situation().set_index("constituency_id")
    assert s.loc["C1", "fgs_worst_rank"] == 3
    assert s.loc["C1", "fgs_worst_date"] == "2026-10-20"
    assert s.loc["C1", "fgs_worst_sources"] == "river, surface"
    assert [s.loc["C1", f"fgs_rank_day{n}"] for n in range(1, 6)] == [2, 3, 2, 0, 0]


def test_small_overlaps_are_ignored_and_quiet_constituencies_keep_a_row():
    s = situation().set_index("constituency_id")
    assert s.loc["C2", "fgs_worst_rank"] == 2          # the High at 0.001 does not count
    assert s.loc["C4", "fgs_worst_rank"] == 0
    assert len(s) == 4


def test_mp_matching_uses_the_name_key():
    s = situation().set_index("constituency_id")
    assert s.loc["C2", "mp_name"] == "B Jones"          # "&" matches "and"
    assert not s.loc["C4", "mp_matched"]
    assert name_key("Ynys Môn") == name_key("Ynys Mon")


def test_escalation_is_unknown_when_the_statement_was_never_compared():
    assert situation(None)["fgs_escalated"].isna().all()
    s = situation({"C1"}).set_index("constituency_id")
    assert bool(s.loc["C1", "fgs_escalated"]) and not bool(s.loc["C2", "fgs_escalated"])


def test_warnings_in_force_are_not_held_for_past_statements():
    s = situation()
    assert s[["severe_warnings", "warnings", "alerts", "has_active_warning"]].isna().all().all()


def test_empty_intersections_give_a_row_per_constituency_with_no_risk():
    s = situation_from_intersections(inter([]), CONST, MP, None, 1, "2026-10-19", 0.01)
    assert len(s) == 4 and (s["fgs_worst_rank"] == 0).all()


def test_draft_text_for_a_past_statement():
    s = situation(escalated={"C1"}).set_index("constituency_id", drop=False)
    c = s.loc["C1"]
    detail = [{"source": "river", "risk_rank": 3, "forecast_date": "2026-10-20",
               "impact_label": "Minor", "likelihood_label": "Likely"}]
    days = day_dates([(1, "2026-10-19"), (2, "2026-10-20")])
    subject, body = build_draft(c, detail, [], days, areas_sentence=lambda *a: None)
    assert subject == "Flood forecast: Medium risk for Carlisle on Tuesday 20 October 2026"
    assert "Dear A Smith," in body
    assert "River flooding: Minor impact, Likely likelihood" in body
    assert "Day 5, Friday 23 October 2026: No risk flagged" in body
    assert "higher than in the previous statement" in body
    assert "currently has" not in body                  # in-force counts are unknown, so left out


def test_draft_says_nothing_about_escalation_when_unknown():
    c = situation(None).set_index("constituency_id", drop=False).loc["C1"]
    _, body = build_draft(c, [], [], day_dates([(1, "2026-10-19")]), areas_sentence=lambda *a: None)
    assert "higher than" not in body


def test_areas_under_the_forecast_use_only_the_worst_risk_and_sources():
    c = situation().set_index("constituency_id", drop=False).loc["C1"]
    areas = [
        {"source": "river", "risk_rank": 3, "ea_area_code": "A1", "ea_area_type": "flood_warning"},
        {"source": "river", "risk_rank": 3, "ea_area_code": "A2", "ea_area_type": "flood_alert"},
        {"source": "river", "risk_rank": 2, "ea_area_code": "A3", "ea_area_type": "flood_alert"},   # lower risk
        {"source": "coastal", "risk_rank": 3, "ea_area_code": "A4", "ea_area_type": "flood_warning"},  # other source
    ]
    seen = {}
    build_draft(c, [], areas, day_dates([(1, "2026-10-19")]),
                areas_sentence=lambda w, a, risk, src: seen.update(w=w, a=a, risk=risk) or "x")
    assert seen == {"w": 1, "a": 1, "risk": "Medium"}


def test_helpers():
    assert optional_bool(None) is None and optional_bool(float("nan")) is None
    assert optional_bool(True) is True and optional_bool(0) is False
    assert day_dates([(2, "2026-10-20")])[1] == date(2026, 10, 19)


def test_stale_setting_is_caught(tmp_path):
    import pytest
    from utils.briefing import check_setting_is_current
    cfg = tmp_path / "config.py"
    cfg.write_text("OTHER = 9\nBRIEFING_MIN_RISK_RANK = 1   # any polygon\n")
    check_setting_is_current(str(cfg), "BRIEFING_MIN_RISK_RANK", 1)          # current: silent
    with pytest.raises(RuntimeError, match="restartPython"):
        check_setting_is_current(str(cfg), "BRIEFING_MIN_RISK_RANK", 3)      # stale: stops
    check_setting_is_current(str(tmp_path / "missing.py"), "BRIEFING_MIN_RISK_RANK", 3)   # unreadable: silent
    cfg.write_text("BRIEFING_MIN_RISK_RANK = compute()\n")
    check_setting_is_current(str(cfg), "BRIEFING_MIN_RISK_RANK", 3)          # not a plain number: silent
