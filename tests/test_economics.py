# Tests for the economics assumptions. Run with:  pytest tests
import math
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.economics import BANDS, SCENARIOS, central_probability, check_assumptions, default_rows, missing_rows  # noqa: E402


def test_defaults_give_a_probability_for_every_band_and_scenario_and_leave_the_damage_empty():
    d = default_rows()
    assert len(d) == 4 * 3 + 3
    p = d[d["item"].str.startswith("prob_")]
    assert p["value"].notna().all() and len(p) == 12
    dm = d[d["item"] == "damage_per_household"]
    assert len(dm) == 3 and dm["value"].isna().all()
    assert central_probability("high") == 0.05 and math.isclose(central_probability("medium"), math.sqrt(0.01 * 0.033))
    assert check_assumptions(d) == []                                     # the seed is internally consistent


def test_a_rerun_adds_what_is_missing_and_never_overwrites_a_value_that_was_set():
    d = default_rows()
    d.loc[(d["item"] == "damage_per_household") & (d["scenario"] == "central"), "value"] = 30000.0
    d = d[d["item"] != "prob_low"]                                         # a missing item comes back
    m = missing_rows(d)
    assert set(m["item"]) == {"prob_low"} and len(m) == 3
    assert len(missing_rows(default_rows())) == 0 and len(missing_rows(None)) == 15


def test_the_checks_name_the_problem():
    d = default_rows()
    d.loc[(d["item"] == "damage_per_household"), "value"] = [50000.0, 30000.0, 70000.0]
    assert any("run low, central, high in order" in x for x in check_assumptions(d))
    e = default_rows()
    e.loc[(e["item"] == "prob_high") & (e["scenario"] == "high"), "value"] = 1.5
    assert any("between 0 and 1" in x for x in check_assumptions(e))
    f = default_rows()
    f.loc[(f["item"] == "prob_very_low") & (f["scenario"] == "central"), "value"] = 0.5
    assert any("rarer band" in x for x in check_assumptions(f))
