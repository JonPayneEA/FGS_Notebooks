# Tests for the spatial helpers. Run locally with:  pytest tests
# Needs geopandas and shapely installed.
import os
import sys

import pandas as pd
from shapely.geometry import box, LineString

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.geo import coverage, clean_geodataframe, geojson_str_to_geodataframe  # noqa: E402


def test_polygon_coverage_is_share_of_base_area():
    base = box(0, 0, 10, 10)                 # 100 m2
    overlaps, pct = coverage(base, box(0, 0, 5, 10))
    assert overlaps and abs(pct - 0.5) < 1e-9


def test_touching_edges_are_not_an_overlap():
    base = box(0, 0, 10, 10)
    assert coverage(base, box(10, 0, 20, 10)) == (False, 0.0)


def test_sliver_below_minimum_area_is_ignored():
    base = box(0, 0, 10, 10)
    assert coverage(base, box(9.9999, 0, 20, 10), min_area_m2=1.0)[0] is False


def test_line_through_area_overlaps_with_no_percentage():
    base = box(0, 0, 10, 10)
    overlaps, pct = coverage(base, LineString([(-5, 5), (15, 5)]))
    assert overlaps and pct is None


def test_line_touching_a_corner_is_not_an_overlap():
    base = box(0, 0, 10, 10)
    assert coverage(base, LineString([(10, 10), (20, 20)]))[0] is False


def test_clean_repairs_bowtie_polygon_and_keeps_lines():
    pdf = pd.DataFrame({"geometry": [
        '{"type": "Polygon", "coordinates": [[[0,0],[10,10],[10,0],[0,10],[0,0]]]}',
        '{"type": "LineString", "coordinates": [[0,0],[5,5]]}',
        None,
        "not json",
    ]})
    gdf = geojson_str_to_geodataframe(pdf, crs="EPSG:27700")
    assert gdf.geometry.isna().sum() == 2
    cleaned = clean_geodataframe(gdf)
    assert len(cleaned) == 2
    assert set(cleaned.geom_type) == {"Polygon", "MultiPolygon", "LineString"} & set(cleaned.geom_type)
    assert all(g.is_valid for g in cleaned.geometry)
    assert "LineString" in set(cleaned.geom_type)


def test_buffer_lets_a_coastal_line_find_the_constituency_it_runs_beside():
    import geopandas as gpd
    from utils.geo import buffer_lines
    constituency = box(0, 0, 10_000, 10_000)
    offshore = LineString([(-300, 0), (-300, 10_000)])            # 300 m beyond the coast edge
    gdf = gpd.GeoDataFrame({"k": [1, 2]}, geometry=[offshore, box(20_000, 0, 21_000, 1_000)], crs="EPSG:27700")
    assert coverage(constituency, offshore)[0] is False            # the problem
    wide = buffer_lines(gdf, 500)
    assert coverage(constituency, wide.geometry.iloc[0])[0] is True
    assert wide.geometry.iloc[1].equals(gdf.geometry.iloc[1])      # polygons are untouched
    assert buffer_lines(gdf, 0).geometry.iloc[0].equals(offshore)  # 0 turns it off
    assert gdf.geometry.iloc[0].geom_type == "LineString"          # the input is not modified


def test_a_buffer_too_small_to_reach_does_not_invent_an_overlap():
    import geopandas as gpd
    from utils.geo import buffer_lines
    constituency = box(0, 0, 10_000, 10_000)
    far = gpd.GeoDataFrame({"k": [1]}, geometry=[LineString([(-5_000, 0), (-5_000, 10_000)])], crs="EPSG:27700")
    assert coverage(constituency, buffer_lines(far, 1_000).geometry.iloc[0])[0] is False


def test_statements_to_process_modes():
    from utils.geo import statements_to_process
    current = {1: "a", 2: "b", 3: "c", 4: "d"}
    done    = {1: "a", 2: "b", 3: "OLD"}                      # 3 was modified since, 4 is new
    assert statements_to_process(current, done) == [3, 4]                        # normal run
    assert statements_to_process(current, done, recompute_all=True) == [1, 2, 3, 4]
    assert statements_to_process(current, done, coastal_ids={2}) == [2, 3, 4]    # coastal plus pending
    assert statements_to_process(current, done, coastal_ids=set()) == [3, 4]     # none coastal: pending only
    assert statements_to_process(current, done, True, {2}) == [1, 2, 3, 4]       # recompute_all wins
