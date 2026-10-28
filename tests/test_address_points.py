# Tests for the address point helpers. Run with:  pytest tests
import os
import sys

import numpy as np
import pandas as pd
import pytest
import shapely
from shapely.geometry import box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("rasterio")

from utils.address_points import (  # noqa: E402
    HAZARD_SOURCES, build_points, centroid_points, clean_nsul, oa_point_counts, oa_shares, sample_hazard,
)
from utils.hazard_raster import RasterAccumulator, save_raster, tile_bounds, tile_code_at  # noqa: E402


def test_tile_code_at_is_the_inverse_of_tile_bounds():
    rng = np.random.default_rng(3)
    e, n = rng.uniform(100_000, 650_000, 400), rng.uniform(0, 700_000, 400)
    for x, y, code in zip(e, n, tile_code_at(e, n)):
        xmin, ymin, xmax, ymax = tile_bounds(code)
        assert xmin <= x < xmax and ymin <= y < ymax
    assert tile_code_at([392000.0], [649322.0])[0] == "NT50"
    assert tile_code_at([-2_000_000.0], [5.0])[0] == ""        # off the lettered grid


def raw_nsul():
    return pd.DataFrame({
        "UPRN": ["1", "2", "3", "4", "5"], "GRIDGB1E": ["528240", "528256", "0", "", "300000"], "GRIDGB1N": ["185465", "186364", "0", "", "200000"],
        "OA21CD": ["E1", "E1", "E2", "E2", "W1"], "LSOA21CD": ["L1", "L1", "L2", "L2", "W"], "LAD25CD": ["D1", "D1", "D2", "D2", "WD"],
        "CTY25CD": ["E99999999", "C1", "C1", "E99999999", "W99999999"], "WD25CD": ["w"] * 5, "PCON24CD": ["p"] * 5, "PFA23CD": ["f"] * 5,
        "RGN25CD": ["r"] * 5, "CTRY25CD": ["E92000001", "E92000001", "E92000001", "E92000001", "W92000004"],
    })


def test_clean_nsul_drops_unplaceable_rows_keeps_england_and_derives_the_county_or_unitary():
    d, rep = clean_nsul(raw_nsul())
    assert list(d["uprn"]) == ["1", "2"] and rep["unplaced"] == 2 and rep["rows_in"] == 5
    assert list(d["ctyua"]) == ["D1", "C1"]                       # no county: the district is the unitary authority
    assert d["cell_key"].iloc[0] == (185465 // 100) * 10_000 + 528240 // 100
    assert rep["countries"]["W92000004"] == 1
    both, _ = clean_nsul(raw_nsul(), england_only=False)
    assert len(both) == 3


def test_shares_conserve_the_census_totals_per_output_area():
    census = pd.DataFrame({"oa": ["E1", "E2"], "population": [10, 6], "households": [4, 3], "age65plus": [2, 0]})
    counts = oa_point_counts(["E1", "E1", "E2", "E2", "E2"])
    sh = oa_shares(census, counts).set_index("oa")
    assert sh.loc["E1", "people"] == 5.0 and sh.loc["E2", "people"] == 2.0
    assert sh.loc["E1", "people"] * 2 == 10 and sh.loc["E2", "households"] * 3 == 3


def make_raster(tmp_path, source, tile, bounds_box, rank):
    b = tile_bounds(tile)
    acc = RasterAccumulator(b, 10)
    acc.add([shapely.to_wkb(bounds_box)], [rank])
    os.makedirs(tmp_path / source, exist_ok=True)
    save_raster(str(tmp_path / source / f"{tile}.npz"), acc.finalize(), b, 10)


def test_points_pick_up_shares_deprivation_and_the_band_at_their_position(tmp_path):
    tile = tile_code_at([528240.0], [185465.0])[0]
    make_raster(tmp_path, "rofrs", tile, box(528200, 185400, 528300, 185500), 4)             # High around the first point only
    make_raster(tmp_path, "flood_zones_sea", tile, box(528200, 185400, 528300, 185500), 2)   # FZ3, sea
    d, _ = clean_nsul(raw_nsul())
    census = pd.DataFrame({"oa": ["E1"], "population": [10], "households": [4], "age65plus": [2]})
    sh = oa_shares(census, oa_point_counts(d["oa"]))
    imd = pd.DataFrame({"lsoa": ["L1"], "imd_score": [30.0], "imd_decile": [2]})
    out = build_points(d, sh, imd, str(tmp_path))
    assert list(out["rofrs_rank"]) == [4, 0]                      # the second point is 900 m away
    assert list(out["fz_sea_rank"]) == [2, 0] and list(out["fz_river_rank"]) == [0, 0]
    assert out["people"].sum() == 10.0 and (out["imd_decile"] == 2).all()
    assert set(out["point_source"]) == {"nsul"}
    assert set(HAZARD_SOURCES.values()) <= set(out.columns)


def test_a_point_in_a_tile_with_no_raster_has_no_hazard(tmp_path):
    d, _ = clean_nsul(raw_nsul())
    out = sample_hazard(d, str(tmp_path))
    assert (out["rofrs_rank"] == 0).all()


def test_output_areas_with_people_but_no_points_get_a_centre_point_and_nothing_is_lost(tmp_path):
    census = pd.DataFrame({"oa": ["E1", "E3", "E4"], "lsoa": ["L1", "L3", "L4"], "population": [10, 7, 5], "households": [4, 3, 2], "age65plus": [2, 1, 0]})
    counts = oa_point_counts(["E1", "E1"])
    centres = pd.DataFrame({"oa": ["E3"], "lsoa": ["L3"], "easting": [400000.0], "northing": [300000.0]})
    imd = pd.DataFrame({"lsoa": ["L3"], "imd_score": [10.0], "imd_decile": [7]})
    pts, rep = centroid_points(census, counts, centres, imd, str(tmp_path))
    assert rep["areas_without_points"] == 2 and rep["placed_at_centre"] == 1 and rep["areas_with_no_centre"] == 1
    assert rep["people_placed"] == 7.0 and rep["people_lost"] == 5.0
    assert pts["people"].sum() == 7.0 and list(pts["point_source"]) == ["oa_centroid"] and pts["imd_decile"].iloc[0] == 7


def test_nothing_to_place_returns_an_empty_frame_even_when_the_centres_table_has_float_columns(tmp_path):
    census = pd.DataFrame({"oa": ["E1"], "lsoa": ["L1"], "population": [10], "households": [4], "age65plus": [2]})
    counts = oa_point_counts(["E1", "E1"])
    centres = pd.DataFrame({"oa": [], "lsoa": [], "easting": [], "northing": []})        # all float64, as an empty frame is by default
    imd = pd.DataFrame({"lsoa": ["L1"], "imd_score": [1.0], "imd_decile": [1]})
    pts, rep = centroid_points(census, counts, centres, imd, str(tmp_path))
    assert len(pts) == 0 and rep["areas_without_points"] == 0 and "rofrs_rank" in pts.columns
