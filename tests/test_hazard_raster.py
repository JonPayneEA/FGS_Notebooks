# Tests for the flood polygon rasteriser. Run with:  pytest tests
import os
import sys

import numpy as np
import pytest
import shapely
from shapely.geometry import box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("rasterio")

from utils.grid100 import cell_key  # noqa: E402
from utils.hazard_raster import (  # noqa: E402
    RasterAccumulator, cell_shares, is_present_day, load_raster, sample_ranks, save_raster, tile_bounds, tile_code,
)

LABELS = {1: "Very Low", 2: "Low", 3: "Medium", 4: "High"}


def wkb(*geoms):
    return [shapely.to_wkb(g) for g in geoms]


def test_tile_names_and_present_day_folders():
    assert tile_code("RoFRS_SE50_v202606") == "SE50"
    assert tile_code("RoFRS_Climate_Change_01_NT55_v202606") == "NT55"
    assert tile_code("nothing") is None
    assert is_present_day("RoFRS_SE50_v202606") and not is_present_day("RoFRS_Climate_Change_01_SE50_v202606")


def test_tile_bounds_match_the_observed_data():
    assert tile_bounds("NT50") == (350000.0, 600000.0, 400000.0, 650000.0)
    assert tile_bounds("NT55") == (350000.0, 650000.0, 400000.0, 700000.0)
    assert tile_bounds("SE50") == (450000.0, 400000.0, 500000.0, 450000.0)
    assert tile_bounds("TQ05") == (500000.0, 150000.0, 550000.0, 200000.0)
    with pytest.raises(ValueError):
        tile_bounds("IA00")


def test_the_worst_band_wins_where_polygons_overlap_whatever_the_order():
    b = (350000, 600000, 350500, 600500)
    acc = RasterAccumulator(b, res=10)
    acc.add(wkb(box(350000, 600000, 350300, 600300)), [4])      # High first
    acc.add(wkb(box(350100, 600100, 350500, 600500)), [2])      # Low second, overlapping
    arr = acc.finalize()
    # inside both: High; Low only: Low
    assert sample_ranks(arr, b, 10, [350150], [600150])[0] == 4
    assert sample_ranks(arr, b, 10, [350450], [600450])[0] == 2
    assert sample_ranks(arr, b, 10, [350450], [600050])[0] == 0
    assert acc.n_polygons == 2


def test_points_outside_the_tile_or_any_band_are_zero():
    b = (350000, 600000, 350500, 600500)
    arr = RasterAccumulator(b, res=10).finalize()
    assert list(sample_ranks(arr, b, 10, [0, 350250, 999999], [0, 600250, 999999])) == [0, 0, 0]


def test_cell_shares_are_shares_of_the_cell_not_worst_band_wins():
    b = (350000, 600000, 350500, 600500)
    acc = RasterAccumulator(b, res=10)
    acc.add(wkb(box(350000, 600000, 350100, 600100)), [1])      # fills cell (3500, 6000) with Very Low
    acc.add(wkb(box(350000, 600000, 350050, 600050)), [4])      # a quarter of that cell is High
    s = cell_shares(acc.finalize(), b, 10, LABELS)
    k = int(cell_key(350050, 600050))
    cell = s[s["cell_key"] == k].set_index("band")["share"]
    assert cell["High"] == pytest.approx(0.25) and cell["Very Low"] == pytest.approx(0.75)
    assert cell.sum() == pytest.approx(1.0)
    assert set(s["cell_key"]) == {k}                            # the sparse result holds only the touched cell


def test_cell_keys_use_the_northern_edge_correctly():
    b = (350000, 600000, 350500, 600500)
    acc = RasterAccumulator(b, res=10)
    acc.add(wkb(box(350400, 600400, 350500, 600500)), [3])      # the north-east cell of the tile
    s = cell_shares(acc.finalize(), b, 10, LABELS)
    assert list(s["cell_key"]) == [int(cell_key(350450, 600450))] and s["share"].iloc[0] == 1.0


def test_batches_give_the_same_raster_as_one_pass():
    b = (350000, 600000, 350500, 600500)
    polys = [box(350000, 600000, 350200, 600200), box(350100, 600100, 350400, 600400), box(350300, 600300, 350500, 600500)]
    ranks = [2, 4, 3]
    one = RasterAccumulator(b, 10)
    one.add(wkb(*polys), ranks)
    two = RasterAccumulator(b, 10)
    for p, r in zip(polys, ranks):
        two.add(wkb(p), [r])
    assert (one.finalize() == two.finalize()).all()


def test_a_raster_round_trips_through_a_file(tmp_path):
    b = (350000, 600000, 350500, 600500)
    acc = RasterAccumulator(b, 10)
    acc.add(wkb(box(350000, 600000, 350100, 600100)), [3])
    arr = acc.finalize()
    p = str(tmp_path / "t.npz")
    save_raster(p, arr, b, 10)
    arr2, b2, res2 = load_raster(p)
    assert (arr == arr2).all() and b2 == b and res2 == 10.0
