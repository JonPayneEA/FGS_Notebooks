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
