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


def _areas():
    return pd.DataFrame({
        "ea_area_code": ["WA1", "WA2", "FW1", "FW2", "FW3", "FWR", "FWX"],
        "ea_area_name": ["Alert 1", "Alert 2", "Warn 1", "Warn 2", "Warn 3", "River warn", "Orphan"],
        "ea_area_type": ["flood_alert", "flood_alert", "flood_warning", "flood_warning", "flood_warning",
                         "flood_warning", "flood_warning"],
        "type_code":    ["WAC", "WAC", "FWC", "FWT", "FWC", "FWF", "FWC"],
        "parent":       ["", "", "WA1", "WA1", "WA2", "WA1", "NOPE"],
    })


def _row(code, kind, poly=1, **extra):
    base = {"statement_id": 1, "poly_id": poly, "day_index": 2, "ea_area_code": code, "ea_area_name": code,
            "ea_area_type": kind, "source": "coastal", "risk_level": "Medium", "intersection_pct": None,
            "link_type": "intersects"}
    base.update(extra)
    return base


def test_warnings_are_raised_through_their_parent_alert_area():
    from utils.geo import link_warnings_to_alerts
    coastal = {"FWC", "WAC", "FWB", "WAB", "FWT", "WAT"}
    new, unmatched = link_warnings_to_alerts([_row("WA1", "flood_alert")], _areas(), coastal)
    assert sorted(r["ea_area_code"] for r in new) == ["FW1", "FW2"]       # WA1's children; FW3 belongs to WA2
    assert all(r["link_type"] == "parent_alert" and r["intersection_pct"] is None for r in new)
    assert all(r["ea_area_type"] == "flood_warning" and r["risk_level"] == "Medium" and r["poly_id"] == 1 for r in new)
    assert {r["ea_area_name"] for r in new} == {"Warn 1", "Warn 2"}
    assert unmatched == 1                                                   # FWX's parent is not a known code


def test_only_the_flood_sources_own_warning_types_are_linked():
    from utils.geo import link_warnings_to_alerts
    new, _ = link_warnings_to_alerts([_row("WA1", "flood_alert")], _areas(), {"FWC", "WAC"})
    assert [r["ea_area_code"] for r in new] == ["FW1"]                     # FWT is tidal, FWR is a river warning


def test_a_warning_already_found_for_the_polygon_is_not_added_twice():
    from utils.geo import link_warnings_to_alerts
    rows = [_row("WA1", "flood_alert"), _row("FW1", "flood_warning")]
    new, _ = link_warnings_to_alerts(rows, _areas(), {"FWC", "FWT", "WAC"})
    assert [r["ea_area_code"] for r in new] == ["FW2"]
    # the same warning under a different polygon is its own row
    again, _ = link_warnings_to_alerts(rows + [_row("WA1", "flood_alert", poly=2)], _areas(), {"FWC", "FWT", "WAC"})
    assert sorted((r["poly_id"], r["ea_area_code"]) for r in again) == [(1, "FW2"), (2, "FW1"), (2, "FW2")]


def test_a_warning_row_never_raises_anything_and_no_alerts_means_no_links():
    from utils.geo import link_warnings_to_alerts
    assert link_warnings_to_alerts([_row("FW1", "flood_warning")], _areas(), {"FWC"})[0] == []
    assert link_warnings_to_alerts([], _areas(), {"FWC"})[0] == []
