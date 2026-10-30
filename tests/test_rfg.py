# Tests for the Rapid Flood Guidance helpers. Run with:  pytest tests
import os
import sys

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rfg import is_present, is_stored, merge_exposure, parse_update, polygon_key, rfg_exposure, update_key  # noqa: E402

UPDATE = {
    "id": 7, "issued_at": "2023-01-01T10:30:00.752Z", "last_updated": "2023-01-01T11:00:00.000Z", "pdf_url": "http://x/rfg.pdf",
    "status": "Rapid Flooding Guidance Present", "date_issued": "2023-01-01", "time_issued": "2023-01-01T10:30:00.752Z", "national_summary": "Showers.",
    "polygons": [
        {"polygon_number": 1, "coordinates": [[[-1.5, 53.8], [-1.4, 53.8], [-1.4, 53.9], [-1.5, 53.8]]], "updated_at": "2023-01-01T10:45:00Z",
         "latest_update": "Easing.", "counties": ["North Yorkshire", "West Yorkshire"], "is_valid": True},
        {"polygon_number": 2, "coordinates": [], "updated_at": None, "latest_update": None, "counties": [], "is_valid": False},
    ],
}
NONE = {"last_updated": "2023-01-02T09:00:00Z", "status": "No Rapid Flood Guidance at this time"}


def test_status_text_decides_whether_guidance_is_present():
    assert is_present("Rapid Flooding Guidance Present")
    assert not is_present("No Rapid Flood Guidance at this time") and not is_present(None) and not is_present("")


def test_an_update_gives_its_row_and_its_polygons_and_counts_the_valid_ones():
    u, p = parse_update(UPDATE, "now")
    assert u["update_key"] == "7" and u["rfg_id"] == 7 and u["is_present"] and u["n_polygons"] == 2 and u["n_valid"] == 1
    assert p[0]["counties"] == "North Yorkshire, West Yorkshire" and p[0]["geometry"].startswith('{"type": "Polygon"') and p[0]["is_valid"]
    assert p[1]["geometry"] is None and not p[1]["is_valid"]                       # kept, not hidden
    assert polygon_key("7", 2) == "7|2"


def test_the_no_guidance_reply_is_a_row_of_its_own_keyed_by_its_time():
    u, p = parse_update(NONE, "now")
    assert u["update_key"] == "none_2023-01-02T09:00:00Z" and not u["is_present"] and u["rfg_id"] is None and p == []
    assert update_key(NONE) != update_key({"last_updated": "2023-01-02T10:00:00Z"})


def test_a_modified_update_is_stored_again_but_an_unchanged_one_is_not():
    stored = pd.DataFrame({"update_key": ["7"], "last_updated": [pd.Timestamp("2023-01-01T11:00:00Z")]})
    assert is_stored(stored, UPDATE)
    assert not is_stored(stored, {**UPDATE, "last_updated": "2023-01-01T12:00:00Z"})
    assert not is_stored(None, UPDATE) and not is_stored(stored.iloc[0:0], UPDATE)


def make_points():
    return pd.DataFrame({
        "easting": [100.0, 300.0, 500.0, 5000.0], "northing": [100.0] * 4, "people": [2.0, 4.0, 6.0, 8.0], "households": [1.0, 2.0, 3.0, 4.0],
        "over65": [0.0, 1.0, 1.0, 2.0], "imd_decile": [1, 2, 3, 4], "point_source": ["nsul"] * 4, "rofrs_rank": [4, 3, 0, 4],
        "fz_rank": [0, 0, 0, 0], "pcon": ["P1", "P1", "P2", "P3"],
    })


def test_people_under_each_polygon_by_constituency_area_and_site():
    polys = [box(0, 0, 400, 200), box(400, 0, 600, 200), box(9000, 0, 9100, 100)]
    keys = ["7|1", "7|2", "7|3"]
    area_polys = [box(0, 0, 200, 200), box(0, 0, 1000, 200)]
    sites = pd.DataFrame({"site_id": ["s1", "s2"], "name": ["School", "Care home"], "site_group": ["education", "health"], "site_type": ["Primary", "Care"],
                          "easting": [150.0, 450.0], "northing": [100.0, 100.0], "rofrs_rank": [4, 0], "fz_rank": [0, 0]})
    r = rfg_exposure(make_points(), polys, keys, area_polys, ["A", "B"], ["flood_warning", "flood_alert"], ["Area A", "Area B"], sites)
    p = r["polygon"].set_index("polygon_key")
    assert list(p.index) == keys                                                    # every polygon is there, even one with nothing inside
    assert p.loc["7|1", "people"] == 6 and p.loc["7|1", "people_rs_high_medium"] == 6 and p.loc["7|2", "people"] == 6 and p.loc["7|3", "people"] == 0
    assert p.loc["7|1", "addresses"] == 2 and p.loc["7|3", "addresses"] == 0
    c = r["constituency"]
    assert c[(c["polygon_key"] == "7|1") & (c["pcon"] == "P1")]["people"].iloc[0] == 6 and not (c["polygon_key"] == "7|3").any()
    a = r["areas"].set_index(["polygon_key", "area_id"])
    assert a.loc[("7|1", "A"), "people"] == 2 and a.loc[("7|1", "B"), "people"] == 6 and a.loc[("7|1", "B"), "area_name"] == "Area B"
    s = r["sites"].set_index("polygon_key")
    assert s.loc["7|1", "name"] == "School" and s.loc["7|1", "band"] == "High" and s.loc["7|2", "name"] == "Care home"


def test_no_points_or_no_polygons_gives_zero_rows_not_an_error():
    r = rfg_exposure(make_points().iloc[0:0], [box(0, 0, 10, 10)], ["9|1"])
    assert list(r["polygon"]["polygon_key"]) == ["9|1"] and r["polygon"]["people"].iloc[0] == 0 and r["constituency"].empty and r["areas"].empty and r["sites"].empty
    assert rfg_exposure(make_points(), [], [])["polygon"].empty


def test_strips_of_points_add_up_to_the_same_answer_as_one_read():
    pts = make_points()
    polys = [box(0, 0, 600, 200)]
    area_polys = [box(0, 0, 350, 200), box(0, 0, 1000, 200)]
    sites = pd.DataFrame({"site_id": ["s1"], "name": ["School"], "site_group": ["education"], "site_type": ["Primary"], "easting": [150.0], "northing": [100.0], "rofrs_rank": [4], "fz_rank": [0]})
    args = (polys, ["7|1"], area_polys, ["A", "B"], ["flood_warning", "flood_alert"], ["Area A", "Area B"])
    whole = rfg_exposure(pts, *args, sites)
    # the same points in three strips by easting, the sites given once (to the first strip) as the job does
    strips = [pts[(pts["easting"] >= lo) & (pts["easting"] < hi)] for lo, hi in ((0, 250), (250, 450), (450, 10000))]
    parts = [rfg_exposure(strips[0], *args, sites)] + [rfg_exposure(x, *args) for x in strips[1:]]
    m = merge_exposure(parts)
    assert np.allclose(m["polygon"].set_index("polygon_key").loc["7|1"].to_numpy(dtype=float), whole["polygon"].set_index("polygon_key").loc["7|1"].to_numpy(dtype=float))
    c = m["constituency"].set_index(["polygon_key", "pcon"])["people"]
    assert c.loc[("7|1", "P1")] == whole["constituency"].set_index(["polygon_key", "pcon"])["people"].loc[("7|1", "P1")]
    a = m["areas"].set_index(["polygon_key", "area_id"])["people"]
    assert a.loc[("7|1", "A")] == 6 and a.loc[("7|1", "B")] == 12
    assert list(m["sites"]["name"]) == ["School"]
    empty = merge_exposure([rfg_exposure(pts.iloc[0:0], *args)])
    assert empty["polygon"]["people"].iloc[0] == 0 and empty["constituency"].empty


def test_job_36_can_import_the_parsing_helpers_without_shapely():
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = f"""
import sys
sys.modules['shapely'] = None          # as if shapely were not installed
sys.path.insert(0, {root!r})
import utils.rfg as r
u, p = r.parse_update({{'id': 1, 'status': 'Rapid Flooding Guidance Present', 'polygons': []}}, 'now')
assert u['update_key'] == '1' and r._HAVE_SHAPELY is False
try:
    r.rfg_exposure(None, [], [])
except ImportError as e:
    assert 'shapely' in str(e)
else:
    raise SystemExit('rfg_exposure should need shapely')
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
