# Tests for the 100 m grid helpers. Run with:  pytest tests
import os
import sys

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.grid100 import (  # noqa: E402
    CELL_M, aggregate_to_areas, apportion_to_cells, band_rank, cell_centre, cell_key, cell_xy, polygon_cells, polygons_to_cells,
    risk_score, worst_band_by_cell,
)


def test_a_cell_key_round_trips_and_is_unique_for_the_whole_grid():
    e, n = np.array([412345.0, 0.0, 699999.0]), np.array([287501.0, 0.0, 1_199_999.0])
    k = cell_key(e, n)
    x, y = cell_xy(k)
    assert list(x) == [412300, 0, 699900] and list(y) == [287500, 0, 1_199_900]
    assert len(set(k)) == 3
    cx, cy = cell_centre(k[0])
    assert (cx, cy) == (412350.0, 287550.0)


def test_a_polygon_owns_the_cells_whose_centre_it_contains():
    sq = box(500_000, 200_000, 501_000, 201_000)                    # exactly 1 km: 10 by 10 cells
    assert len(polygon_cells(sq)) == 100
    half = box(500_000, 200_000, 500_540, 200_100)                   # five cell centres (50, 150 ... 450) fall inside, the sixth (550) does not
    assert len(polygon_cells(half)) == 5
    tiny = box(500_010, 200_010, 500_020, 200_020)                   # smaller than a cell and holds no centre: it still owns one
    k = polygon_cells(tiny)
    assert len(k) == 1 and k[0] == cell_key(500_015, 200_015)
    assert len(polygon_cells(None)) == 0


def test_apportioning_conserves_totals_even_for_a_tiny_polygon():
    geoms = [box(500_000, 200_000, 501_000, 201_000), box(501_000, 200_000, 501_030, 200_030)]
    cells = polygons_to_cells(geoms, ["A", "B"])
    vals = pd.DataFrame({"id": ["A", "B"], "population": [1000.0, 7.0]})
    grid = apportion_to_cells(cells, vals, ["population"])
    assert abs(grid["population"].sum() - 1007.0) < 1e-9
    assert abs(grid.loc[grid["cell_key"] == cell_key(500_050, 200_050), "population"].iloc[0] - 10.0) < 1e-9   # 1000 over 100 cells


def test_the_worst_band_wins_where_bands_overlap():
    cells = pd.DataFrame({"cell_key": [1, 1, 2, 3], "band": ["Low", "High", "Very low", "unknown"]})
    out = worst_band_by_cell(cells).set_index("cell_key")["rank"]
    assert out.to_dict() == {1: 4, 2: 1, 3: 0}
    assert [band_rank(x) for x in ["Medium", " HIGH ", "very_low", None, np.nan, "x"]] == [3, 4, 1, 0, 0, 0]


def test_cells_add_up_to_areas_and_split_by_band():
    grid = pd.DataFrame({"cell_key": [10, 11, 12, 13], "population": [5.0, 5.0, 10.0, 20.0], "haz_rs": [4, 3, 3, 0]})
    area_cells = pd.DataFrame({"id": ["w1", "w1", "w1", "w2"], "cell_key": [10, 11, 12, 13]})
    tot = aggregate_to_areas(grid, area_cells, ["population"]).set_index("id")
    assert tot.loc["w1", "population"] == 20.0 and tot.loc["w2", "population"] == 20.0 and tot.loc["w1", "cells"] == 3
    by = aggregate_to_areas(grid, area_cells, ["population"], by="haz_rs").set_index(["id", "haz_rs"])["population"]
    assert by.loc[("w1", 3)] == 15.0 and by.loc[("w1", 4)] == 5.0


def test_the_score_needs_a_hazard_and_rises_with_exposure_vulnerability_and_critical_sites():
    g = pd.DataFrame({"haz_rs": [0, 4, 4, 4, 4], "haz_sw": [0, 0, 0, 0, 0], "population": [500, 10, 500, 500, 500],
                      "imd_decile": [1, 10, 10, 1, 10], "critical_sites": [3, 0, 0, 0, 3]})
    s = risk_score(g, {"pop": 0.4, "vulnerability": 0.3, "critical": 0.3})
    assert s[0] == 0                                                   # no hazard, no score, whatever is there
    assert s[2] > s[1]                                                 # more people
    assert s[3] > s[2]                                                 # more deprived
    assert s[4] > s[2]                                                 # a critical site
    assert 0 <= s.min() and s.max() <= 100
    assert (risk_score(g.drop(columns=["critical_sites"]), {"pop": 1}) >= 0).all()     # a missing column counts as zero
