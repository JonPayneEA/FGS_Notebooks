# Tests for the 100 m exposure grid builder. Run with:  pytest tests
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.grid_build import add_score, build_grid, critical_to_cells, points_to_cells, shares_wide  # noqa: E402
from utils.grid100 import cell_key  # noqa: E402

K1, K2 = int(cell_key(500050, 100050)), int(cell_key(500150, 100050))


def pts():
    return pd.DataFrame({
        "cell_key": [K1, K1, K1, K2], "people": [2.0, 3.0, 5.0, 4.0], "households": [1.0, 1.0, 2.0, 2.0], "over65": [0.0, 1.0, 1.0, 0.0],
        "imd_score": [10.0, 30.0, 20.0, 5.0], "imd_decile": [2, 4, 3, 9], "point_source": ["nsul", "nsul", "oa_centroid", "nsul"],
        "rofrs_rank": [4, 4, 0, 1], "fz_rank": [2, 1, 0, 0], "fz_sea_rank": [2, 0, 0, 0], "fz_river_rank": [0, 1, 0, 0], "fz_river_and_sea_rank": [0, 0, 0, 0],
    })


def test_points_add_up_and_split_by_band():
    c = points_to_cells(pts()).set_index("cell_key")
    a = c.loc[K1]
    assert a["people"] == 10 and a["households"] == 4 and a["over65"] == 2 and a["n_points"] == 2      # the centre point is not an address
    assert a["people_rs_high"] == 5 and a["households_rs_high"] == 2 and a["people_rs_low"] == 0
    assert a["people_fz3"] == 2 and a["people_fz2"] == 3 and a["people_fz3_sea"] == 2 and a["people_fz3_river"] == 0
    assert c.loc[K2, "people_rs_very_low"] == 4
    assert c["people"].sum() == pts()["people"].sum()                                                   # nothing is lost


def test_deprivation_is_a_people_weighted_mean():
    c = points_to_cells(pts()).set_index("cell_key")
    assert c.loc[K1, "imd_score"] == pytest.approx((2 * 10 + 3 * 30 + 5 * 20) / 10)
    assert c.loc[K1, "imd_decile"] == round((2 * 2 + 3 * 4 + 5 * 3) / 10)


def test_a_cell_with_no_people_falls_back_to_the_plain_mean_deprivation():
    p = pts().iloc[:1].copy()
    p["people"] = 0.0
    assert points_to_cells(p).iloc[0]["imd_score"] == 10.0


def test_shares_become_columns_and_the_hazard_is_area_weighted():
    long = pd.DataFrame({"source": ["rofrs", "rofrs", "flood_zones", "flood_zones_sea", "rofsw"], "tile": ["A"] * 5, "cell_key": [K1, K1, K1, K1, K1],
                         "band": ["High", "Low", "FZ3", "FZ3", "High"], "share": [0.25, 0.5, 0.4, 0.1, 1.0]})
    w = shares_wide(long).set_index("cell_key")
    assert w.loc[K1, "share_rs_high"] == 0.25 and w.loc[K1, "share_fz3_sea"] == 0.1 and "share_rs_medium" not in w     # surface water is dropped
    g = build_grid(points_to_cells(pts()), shares_wide(long)).set_index("cell_key")
    assert g.loc[K1, "haz_rs"] == pytest.approx(4 * 0.25 + 2 * 0.5)       # a quarter High and half Low is 2.0, not 4
    assert g.loc[K2, "haz_rs"] == 0 and g.loc[K1, "northing"] == 100000 and g.loc[K1, "easting"] == 500000


def test_critical_sites_count_by_group_and_band():
    sites = pd.DataFrame({"easting": [500010, 500020, 500160], "northing": [100010, 100020, 100060], "site_group": ["Health", "education", "utilities"],
                          "rofrs_rank": [4, 1, 0]})
    c = critical_to_cells(sites).set_index("cell_key")
    assert c.loc[K1, "critical_sites"] == 2 and c.loc[K1, "critical_health"] == 1 and c.loc[K1, "critical_education"] == 1
    assert c.loc[K1, "critical_in_rs_high_medium"] == 1 and c.loc[K2, "critical_utilities"] == 1
    g = build_grid(points_to_cells(pts()), pd.DataFrame({"cell_key": np.array([], dtype="int64")}), c)
    assert g.set_index("cell_key").loc[K1, "critical_sites"] == 2


def test_a_cell_with_only_hazard_or_only_critical_sites_is_kept():
    only_shares = pd.DataFrame({"cell_key": [999], "share_rs_high": [1.0]})
    g = build_grid(points_to_cells(pts()), only_shares)
    assert 999 in set(g["cell_key"]) and g.set_index("cell_key").loc[999, "people"] == 0


def test_the_score_needs_a_hazard_and_uses_a_national_population_percentile():
    g = build_grid(points_to_cells(pts()), pd.DataFrame({"cell_key": [K1], "share_rs_high": [1.0]}))
    out = add_score(g, {"pop": 0.4, "vulnerability": 0.3, "critical": 0.3}).set_index("cell_key")
    assert out.loc[K1, "risk_score"] > 0 and out.loc[K2, "risk_score"] == 0      # K2 has people but no hazard
    assert out.loc[K1, "pop_pct"] == 1.0 and 0 < out.loc[K2, "pop_pct"] < 1
