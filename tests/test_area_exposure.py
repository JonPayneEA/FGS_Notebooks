# Tests for the warning area exposure helpers. Run with:  pytest tests
import os
import sys

import numpy as np
import pandas as pd
import pytest
import shapely
from shapely.geometry import box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.area_exposure import (  # noqa: E402
    UNIQUE_SPLIT, VALUE_COLS, combine_tiles, constituency_with_gap, coverage_with_gap, finish_areas, point_values, site_stats, tile_stats, to_bng_polygons,
)
from utils.grid_build import points_to_cells  # noqa: E402


def make_points():
    # six addresses on a line; the areas below are boxes along x
    return pd.DataFrame({
        "easting": [100.0, 300.0, 500.0, 700.0, 900.0, 5000.0], "northing": [100.0] * 6,
        "people": [2.0, 4.0, 6.0, 8.0, 10.0, 12.0], "households": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "over65": [0.0, 1.0, 1.0, 2.0, 2.0, 3.0],
        "imd_decile": [1, 1, 2, 5, 10, 3], "point_source": ["nsul"] * 5 + ["oa_centroid"],
        "rofrs_rank": [4, 3, 2, 1, 0, 4], "fz_rank": [2, 2, 1, 0, 0, 2], "fz_sea_rank": [0, 0, 0, 0, 0, 2], "fz_river_rank": [2, 0, 0, 0, 0, 0],
        "fz_river_and_sea_rank": [0, 2, 0, 0, 0, 0], "pcon": ["P1", "P1", "P1", "P2", "P2", "P3"],
    })


# warning A covers x 0-600 (points 0,1,2), warning B covers x 400-1000 (points 2,3,4: point 2 is in both), alert C covers x 0-1000 (points 0 to 4)
GEOMS = [box(0, 0, 600, 200), box(400, 0, 1000, 200), box(0, 0, 1000, 200)]
IDS = ["A", "B", "C"]
KINDS = ["flood_warning", "flood_warning", "flood_alert"]


def test_point_values_split_by_band_and_an_output_area_centre_is_not_an_address():
    v = point_values(make_points())
    assert list(v["addresses"]) == [1, 1, 1, 1, 1, 0]
    assert v.loc[0, "people_rs_high"] == 2 and v.loc[1, "people_rs_medium"] == 4 and v.loc[1, "people_rs_high_medium"] == 4 and v.loc[3, "people_rs_high_medium"] == 0
    assert v.loc[0, "people_fz3"] == 2 and v.loc[2, "people_fz2"] == 6 and v.loc[0, "people_fz3_river"] == 2 and v.loc[1, "people_fz3_both"] == 4 and v.loc[5, "people_fz3_sea"] == 12
    assert v["people_imd_1_2"].tolist() == [2, 4, 6, 0, 0, 0]


def test_gross_unique_and_split_views_of_overlapping_warning_areas():
    s = tile_stats(make_points(), GEOMS, IDS, KINDS)
    a = s["area"]
    A, B, C = a.loc[("flood_warning", "A")], a.loc[("flood_warning", "B")], a.loc[("flood_alert", "C")]
    assert A["people"] == 12 and B["people"] == 24 and C["people"] == 30                      # gross: 2+4+6, 6+8+10, 2+4+6+8+10
    assert A["people_unique"] == 6 and B["people_unique"] == 18                              # point 2 (6 people) is in both warnings
    assert A["people_split"] == 2 + 4 + 3 and B["people_split"] == 3 + 8 + 10
    assert A["people_split"] + B["people_split"] == 2 + 4 + 6 + 8 + 10                       # split adds up to the real total, unique plus half the shared
    assert A["people_unique"] + B["people_unique"] + 6 == 30                                 # unique plus the shared person is the union
    assert C["people_unique"] == 30 and C["people_split"] == 30                              # nothing else of its kind overlaps the alert


def test_a_warning_does_not_make_a_point_shared_with_an_alert():
    s = tile_stats(make_points(), GEOMS, IDS, KINDS)
    assert s["area"].loc[("flood_warning", "A"), "people_unique"] == 6                        # the alert covering the same points does not count against it


def test_overlapping_pairs_are_listed_once_with_what_they_share():
    s = tile_stats(make_points(), GEOMS, IDS, KINDS)
    p = s["pairs"]
    assert list(p.index) == [("flood_warning", "A", "B")] and p.iloc[0]["people"] == 6 and p.iloc[0]["addresses"] == 1


def test_coverage_is_the_distinct_total_and_the_gap_is_what_no_area_covers():
    s = tile_stats(make_points(), GEOMS, IDS, KINDS)
    c = s["coverage"]
    assert c.loc["all", "people"] == 42 and c.loc["flood_warning", "people"] == 30 and c.loc["flood_alert", "people"] == 30
    assert c.loc["all", "people_rs_high_medium"] == 2 + 4 + 12 and c.loc["flood_warning", "people_rs_high_medium"] == 6
    assert c.loc["all", "people_rs_high_medium"] - c.loc["flood_warning", "people_rs_high_medium"] == 12     # the High band person at x = 5000 is in no area


def test_each_area_is_split_by_constituency_and_constituency_totals_show_the_gap():
    s = tile_stats(make_points(), GEOMS, IDS, KINDS)
    ap = s["area_pcon"]
    assert ap.loc[("flood_warning", "B", "P1"), "people"] == 6 and ap.loc[("flood_warning", "B", "P2"), "people"] == 18
    pc = s["pcon"]
    assert pc.loc["P3", "people"] == 12 and pc.loc["P3", "people_in_warning_area"] == 0 and pc.loc["P3", "people_rs_high_medium"] == 12
    assert pc.loc["P1", "people_in_warning_area"] == 12 and pc.loc["P2", "people_in_warning_area"] == 18
    assert pc["people"].sum() == 42


def test_tiles_add_up_to_the_same_answer_as_one_pass():
    pts = make_points()
    whole = tile_stats(pts, GEOMS, IDS, KINDS)
    parts = [tile_stats(pts.iloc[:3], GEOMS, IDS, KINDS), tile_stats(pts.iloc[3:], GEOMS, IDS, KINDS)]
    for key in ("area", "area_pcon", "coverage", "pcon"):
        summed = combine_tiles([p[key] for p in parts], None)
        w = whole[key].sort_index()
        summed = summed.sort_index()
        for c in w.columns:
            # unique and split depend on the whole overlap, which a split of the points does not change: points are independent
            assert np.allclose(summed[c].reindex(w.index).fillna(0).to_numpy(), w[c].to_numpy()), (key, c)


def test_one_area_covering_everything_agrees_with_the_grid_builders_totals():
    pts = make_points().assign(cell_key=[1, 1, 2, 2, 3, 4], imd_score=[10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    cells = points_to_cells(pts)
    s = tile_stats(pts, [box(-1, -1, 1e6, 1e6)], ["ALL"], ["flood_warning"])
    a = s["area"].loc[("flood_warning", "ALL")]
    for c in ["people", "households", "over65", "people_rs_very_low", "people_rs_low", "people_rs_medium", "people_rs_high", "households_rs_high",
              "people_fz2", "people_fz3", "people_fz3_sea", "people_fz3_river", "people_fz3_both"]:
        assert a[c] == pytest.approx(cells[c].sum()), c
    assert a["addresses"] == cells["n_points"].sum()


def test_finish_adds_the_mean_deprivation_and_shares_and_keeps_areas_with_no_addresses():
    s = tile_stats(make_points(), GEOMS, IDS, KINDS)
    meta = pd.DataFrame({"kind": ["flood_warning", "flood_warning", "flood_alert", "flood_warning"], "area_id": ["A", "B", "C", "EMPTY"]})
    f = finish_areas(s["area"], meta).set_index("area_id")
    assert f.loc["A", "imd_mean_decile"] == pytest.approx((2 * 1 + 4 * 1 + 6 * 2) / 12, abs=0.01)
    assert f.loc["A", "people_shared"] == 6 and f.loc["A", "share_people_shared"] == 0.5
    assert f.loc["A", "share_people_high_medium"] == pytest.approx(6 / 12, abs=1e-4)
    assert f.loc["EMPTY", "people"] == 0 and pd.isna(f.loc["EMPTY", "imd_mean_decile"]) and pd.isna(f.loc["EMPTY", "share_people_shared"])
    assert "imd_w" not in f.columns and len(f) == 4


def test_critical_sites_are_counted_per_area_by_group_and_band():
    sites = pd.DataFrame({"easting": [100.0, 500.0, 950.0, 5000.0], "northing": [100.0] * 4, "site_group": ["health", "Education", "utilities", "emergency"],
                          "rofrs_rank": [4, 1, 3, 4], "fz_rank": [2, 0, 1, 2]})
    t = site_stats(sites, GEOMS, IDS, KINDS)
    assert t.loc[("flood_warning", "A"), "sites"] == 2 and t.loc[("flood_warning", "A"), "sites_education"] == 1 and t.loc[("flood_warning", "A"), "sites_rs_high_medium"] == 1
    assert t.loc[("flood_warning", "B"), "sites"] == 2 and t.loc[("flood_warning", "B"), "sites_utilities"] == 1       # the education site at x = 500 is in both warnings
    assert t.loc[("flood_alert", "C"), "sites"] == 3 and t.loc[("flood_alert", "C"), "sites_fz3"] == 1


def test_geojson_areas_become_valid_polygons_in_british_national_grid_and_lines_are_reported():
    pytest.importorskip("pyproj")
    leeds = '{"type":"Polygon","coordinates":[[[-1.56,53.79],[-1.54,53.79],[-1.54,53.81],[-1.56,53.81],[-1.56,53.79]]]}'
    bowtie = '{"type":"Polygon","coordinates":[[[-1.56,53.79],[-1.54,53.81],[-1.54,53.79],[-1.56,53.81],[-1.56,53.79]]]}'      # self-crossing: invalid
    line = '{"type":"LineString","coordinates":[[-1.56,53.79],[-1.54,53.81]]}'
    geoms, status = to_bng_polygons([leeds, bowtie, line, None, "not json"])
    assert status == ["ok", "ok", "not a polygon", "no geometry", "no geometry"]
    assert geoms[0].is_valid and 425000 < geoms[0].centroid.x < 435000 and 430000 < geoms[0].centroid.y < 436000
    assert geoms[1].is_valid and geoms[1].area > 0
    # a file already in British National Grid is taken as it is
    bng, st = to_bng_polygons(['{"type":"Polygon","coordinates":[[[430000,433000],[431000,433000],[431000,434000],[430000,433000]]]}'], source_epsg=27700)
    assert st == ["ok"] and abs(bng[0].centroid.x - 430666.7) < 1


def test_the_gap_is_what_no_area_covers_nationally_and_by_constituency():
    s = tile_stats(make_points(), GEOMS, IDS, KINDS)
    c = coverage_with_gap(s["coverage"]).set_index("kind")
    assert c.loc["flood_warning", "people_not_in_area"] == 12 and c.loc["flood_warning", "people_high_medium_not_in_area"] == 12
    assert c.loc["all", "people_not_in_area"] == 0
    k = constituency_with_gap(s["pcon"]).set_index("constituency_code")
    assert k.loc["P3", "people_high_medium_not_in_warning_area"] == 12 and k.loc["P1", "people_high_medium_not_in_warning_area"] == 0


def test_a_tile_where_no_point_falls_in_any_area_still_gives_consistent_empty_frames():
    far = make_points().assign(easting=[1e6 + i for i in range(6)])
    s = tile_stats(far, GEOMS, IDS, KINDS)
    assert len(s["area"]) == 0 and len(s["pairs"]) == 0 and len(s["area_pcon"]) == 0
    assert s["coverage"].loc["flood_warning", "people"] == 0 and s["coverage"].loc["all", "people"] == 42
    both = combine_tiles([s["area"], tile_stats(make_points(), GEOMS, IDS, KINDS)["area"]], None)
    assert both.loc[("flood_warning", "A"), "people"] == 12
    assert coverage_with_gap(combine_tiles([s["coverage"], tile_stats(make_points(), GEOMS, IDS, KINDS)["coverage"]], None)).set_index("kind").loc["flood_warning", "people"] == 30


def test_a_geos_error_while_repairing_one_polygon_falls_back_and_never_stops_the_run(monkeypatch):
    pytest.importorskip("pyproj")
    bowtie = '{"type":"Polygon","coordinates":[[[-1.56,53.79],[-1.54,53.81],[-1.54,53.79],[-1.56,53.81],[-1.56,53.79]]]}'
    good = '{"type":"Polygon","coordinates":[[[-1.56,53.79],[-1.54,53.79],[-1.54,53.81],[-1.56,53.81],[-1.56,53.79]]]}'

    def boom(g):
        raise shapely.errors.GEOSException("IllegalArgumentException: Overlay input is mixed-dimension")

    monkeypatch.setattr(shapely, "make_valid", boom)
    geoms, status = to_bng_polygons([bowtie, good])
    assert status == ["ok", "ok"] and geoms[0].is_valid and geoms[0].area > 0           # the bowtie was repaired by buffer(0) instead
    monkeypatch.setattr(shapely.geometry.base.BaseGeometry, "buffer", lambda self, *a, **k: (_ for _ in ()).throw(shapely.errors.GEOSException("also fails")))
    geoms, status = to_bng_polygons([bowtie, good])
    assert status == ["could not be repaired", "ok"] and geoms[0] is None and geoms[1] is not None


def test_a_repair_that_leaves_only_a_line_is_reported_not_returned_as_a_polygon():
    pytest.importorskip("pyproj")
    collapsed = '{"type":"Polygon","coordinates":[[[-1.6,53.7],[-1.6,53.7],[-1.6,53.7],[-1.6,53.7]]]}'      # every point the same: no area
    geoms, status = to_bng_polygons([collapsed])
    assert status[0] != "ok" and geoms[0] is None
