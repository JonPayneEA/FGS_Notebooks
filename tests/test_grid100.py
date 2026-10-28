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
    risk_score, worst_band_by_cell, covered_cells, union_total, primary_area, area_overlap_stats, overlap_depth, pair_overlaps,
    allocate_to_points, points_in_areas, band_shares_from_samples,
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


def test_surface_water_is_left_out_of_the_score_unless_asked_for():
    g = pd.DataFrame({"haz_rs": [0, 0, 4], "haz_sw": [0, 4, 4], "population": [500, 500, 500], "imd_decile": [1, 1, 1], "critical_sites": [3, 3, 3]})
    w = {"pop": 0.4, "vulnerability": 0.3, "critical": 0.3}
    default = risk_score(g, w)
    assert default[1] == 0 and default[2] > 0                         # surface water alone scores nothing by default
    with_sw = risk_score(g, w, include_surface=True)
    assert with_sw[1] > 0 and with_sw[2] == default[2]                # and counts when asked; the worse of the two never lowers a score


def nested_example():
    # An alert area A covers cells 1 to 6. Two warning areas sit inside it: W1 on cells 2, 3 and W2 on cells 3, 4 (they overlap on 3).
    area_cells = pd.DataFrame({"id": ["A"] * 6 + ["W1", "W1", "W2", "W2"], "cell_key": [1, 2, 3, 4, 5, 6, 2, 3, 3, 4]})
    meta = pd.DataFrame({"id": ["A", "W1", "W2"], "is_warning": [0, 1, 1], "area_km2": [5.0, 1.0, 1.2], "in_force": [1, 1, 1], "severity": [1, 2, 2]})      # severity as a rank: alert 1, warning 2, severe 3
    grid = pd.DataFrame({"cell_key": [1, 2, 3, 4, 5, 6, 7], "population": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 999.0]})
    return area_cells, meta, grid


def test_a_total_over_overlapping_areas_counts_each_cell_once():
    area_cells, meta, grid = nested_example()
    assert list(covered_cells(area_cells)) == [1, 2, 3, 4, 5, 6]
    assert union_total(grid, area_cells, ["population"])["population"] == 210.0          # not the sum of the area totals (210 + 50 + 70)
    assert union_total(grid, area_cells, ["population"], ids=["W1", "W2"])["population"] == 90.0
    assert list(covered_cells(area_cells, ids=["W1"])) == [2, 3]


def test_each_cell_goes_to_one_primary_area_by_precedence():
    area_cells, meta, grid = nested_example()
    p = primary_area(area_cells, meta).set_index("cell_key")
    assert p.loc[1, "primary_id"] == "A" and p.loc[5, "primary_id"] == "A"              # only the alert area covers them
    assert p.loc[2, "primary_id"] == "W1"                                               # in force, then the higher severity: the warning beats the alert
    assert p.loc[3, "primary_id"] in ("W1", "W2") and p.loc[3, "n_areas"] == 3
    # a stricter, explicit order: a warning before an alert, then the smaller area
    q = primary_area(area_cells, meta, order=[("is_warning", False), ("area_km2", True), ("id", True)]).set_index("cell_key")
    assert q.loc[2, "primary_id"] == "W1" and q.loc[3, "primary_id"] == "W1" and q.loc[4, "primary_id"] == "W2" and q.loc[1, "primary_id"] == "A"


def test_gross_unique_and_split_totals_separate_what_is_shared():
    area_cells, meta, grid = nested_example()
    order = [("is_warning", False), ("area_km2", True), ("id", True)]
    s = area_overlap_stats(grid, area_cells, meta, ["population"], order).set_index("id")
    assert s.loc["A", "population_gross"] == 210.0 and s.loc["W1", "population_gross"] == 50.0 and s.loc["W2", "population_gross"] == 70.0
    # unique and split both add up across areas to the union total, so they are safe to add; gross is not
    assert abs(s["population_unique"].sum() - 210.0) < 1e-9 and abs(s["population_split"].sum() - 210.0) < 1e-9
    assert s["population_gross"].sum() == 330.0
    assert s.loc["W1", "population_unique"] == 50.0 and s.loc["W2", "population_unique"] == 40.0 and s.loc["A", "population_unique"] == 120.0
    assert s.loc["A", "population_shared"] == 90.0


def test_the_depth_and_pairs_of_overlap_between_warning_areas_are_measured():
    # three warning areas: W1 cells 1-4, W2 cells 3-6, W3 the same ground as W2 (a duplicate code)
    area_cells = pd.DataFrame({"id": ["W1"] * 4 + ["W2"] * 4 + ["W3"] * 4, "cell_key": [1, 2, 3, 4, 3, 4, 5, 6, 3, 4, 5, 6]})
    grid = pd.DataFrame({"cell_key": [1, 2, 3, 4, 5, 6], "population": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0]})
    d = overlap_depth(area_cells, grid, ["population"]).set_index("n_areas")
    # cells 1 and 2 are covered once, 5 and 6 twice (W2 and W3), 3 and 4 three times
    assert d.loc[1, "cells"] == 2 and d.loc[2, "cells"] == 2 and d.loc[3, "cells"] == 2
    assert d["population"].sum() == 60.0
    p = pair_overlaps(area_cells, grid, ["population"])
    top = p.iloc[0]
    assert (top["id_a"], top["id_b"]) == ("W2", "W3") and top["shared_cells"] == 4 and top["share_of_a"] == 1.0 and top["share_of_b"] == 1.0   # the same ground twice
    w1w2 = p[(p["id_a"] == "W1") & (p["id_b"] == "W2")].iloc[0]
    assert w1w2["shared_cells"] == 2 and w1w2["population"] == 20.0 and w1w2["share_of_a"] == 0.5
    assert pair_overlaps(area_cells[area_cells["id"] == "W1"], grid, ["population"]).empty


def test_population_is_shared_among_an_output_areas_address_points_and_totals_are_conserved():
    pts = pd.DataFrame({"point_id": ["a", "b", "c", "d"], "oa": ["OA1", "OA1", "OA1", "OA2"]})
    totals = pd.DataFrame({"oa": ["OA1", "OA2", "OA3"], "population": [300.0, 80.0, 40.0], "over65": [30.0, 8.0, 4.0]})
    d, missing = allocate_to_points(pts, totals, ["population", "over65"])
    assert d.set_index("point_id").loc["a", "population"] == 100.0 and d.set_index("point_id").loc["d", "population"] == 80.0
    assert abs(d["population"].sum() - 380.0) < 1e-9                       # OA3 has no points: reported, not lost
    assert list(missing["oa"]) == ["OA3"] and missing["population"].iloc[0] == 40.0


def test_points_are_placed_in_areas_exactly_and_overlapping_areas_give_two_rows():
    import geopandas as gpd
    from shapely.geometry import Point
    pts = gpd.GeoDataFrame({"point_id": ["in_a", "in_both", "out", "edge"]},
                           geometry=[Point(5, 5), Point(15, 5), Point(50, 50), Point(10, 5)], crs=27700)
    areas = gpd.GeoDataFrame({"area_id": ["A", "B"]}, geometry=[box(0, 0, 20, 10), box(10, 0, 30, 10)], crs=27700)
    j = points_in_areas(pts, areas)
    got = set(map(tuple, j[["point_id", "area_id"]].to_numpy()))
    assert got == {("in_a", "A"), ("in_both", "A"), ("in_both", "B"), ("edge", "A"), ("edge", "B")}      # a boundary point is inside; "out" is in none
    # the overlap rules work on points too: rename the unit column
    unit = j.rename(columns={"point_id": "cell_key", "area_id": "id"})
    grid = pd.DataFrame({"cell_key": ["in_a", "in_both", "edge", "out"], "population": [10.0, 20.0, 5.0, 99.0]})
    assert union_total(grid, unit, ["population"])["population"] == 35.0


def test_band_shares_keep_the_share_in_each_band_and_not_just_the_worst():
    samples = pd.DataFrame({"cell_key": [1] * 10 + [2] * 4, "band": ["High"] + ["None"] * 9 + ["Low", "Low", "Medium", "Low"]})
    s = band_shares_from_samples(samples).set_index("cell_key")
    assert s.loc[1, "share_4"] == 0.1 and s.loc[1, "share_0"] == 0.9 and s.loc[1, "worst_band"] == 4     # one sample in ten is High
    assert s.loc[2, "share_2"] == 0.75 and s.loc[2, "share_3"] == 0.25 and s.loc[2, "worst_band"] == 3
