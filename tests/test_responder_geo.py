# Tests for the responder geography helpers. Run with:  pytest tests
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.responder_geo import GEOGRAPHIES, find_reference_file, parse_code_name, parse_district_lookup, read_csv_text, rollup_through_lookup  # noqa: E402
from utils.responder_geo import assign_points_to_polygons, read_geojson_features  # noqa: E402

FILES = ["BUA names and codes EW as at 04_24.csv", "CED County Electoral Division names and codes EN as at 05_25.csv", "CTRY Country names and codes UK as at 05_25.csv",
         "CTY County names and codes EW as at 05_25.csv", "E121_Mismatch_Stats_By_LAD.csv", "PFA Police Force Area names and codes EW as at 04_23.csv",
         "PFA Police Force Area names and codes EW as at 04_24.csv", "WD Ward names and codes UK as at 05_25.csv", "User Guide.pdf"]


def test_the_file_whose_name_starts_with_the_keyword_wins_and_the_latest_date_breaks_a_tie():
    assert find_reference_file(FILES, GEOGRAPHIES["police_force_area"]["keywords"]) == "PFA Police Force Area names and codes EW as at 04_24.csv"
    assert find_reference_file(FILES, GEOGRAPHIES["ward"]["keywords"]) == "WD Ward names and codes UK as at 05_25.csv"
    assert find_reference_file(FILES, GEOGRAPHIES["county_or_unitary"]["keywords"]) == "CTY County names and codes EW as at 05_25.csv"


def test_a_geography_with_no_file_returns_none_and_non_csv_files_are_ignored():
    assert find_reference_file(FILES, ["Fire"]) is None
    assert find_reference_file(["User Guide.pdf"], ["User"]) is None
    assert find_reference_file(FILES, GEOGRAPHIES["constituency"]["keywords"]) is None


def test_the_code_and_name_columns_are_found_and_the_welsh_name_is_not_taken():
    df = pd.DataFrame({"PFA23CD": ["E23000001", "E23000001", "E23000002"], "PFA23NM": ["Avon", "Avon", "Bedfordshire"], "PFA23NMW": ["", "", ""]})
    out, code, name = parse_code_name(df)
    assert (code, name) == ("PFA23CD", "PFA23NM") and list(out["code"]) == ["E23000001", "E23000002"] and list(out["name"]) == ["Avon", "Bedfordshire"]
    assert parse_code_name(pd.DataFrame({"a": [1]})) == (None, None, None)


def test_districts_roll_up_into_the_geography_a_lookup_gives_them():
    by_d = pd.DataFrame({"code": ["D1", "D2", "D3", "D4"], "people": [10.0, 20.0, 30.0, 40.0], "people_rs_high_medium": [1.0, 2.0, 3.0, 4.0]})
    lk = pd.DataFrame({"district_code": ["D1", "D2", "D3"], "geography": "fire_and_rescue_authority", "geography_code": ["F1", "F1", "F2"],
                       "geography_name": ["North FRS", "North FRS", "South FRS"]})
    out = rollup_through_lookup(by_d, lk, ["people", "people_rs_high_medium"]).set_index("geography_code")
    assert out.loc["F1", "people"] == 30 and out.loc["F1", "districts"] == 2 and out.loc["F2", "people_rs_high_medium"] == 3
    assert out["people"].sum() == 60                            # D4 is in no row: it is not in any geography


def test_a_csv_with_a_byte_order_mark_and_accents_is_read_cleanly(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes("\ufeffLAD25CD,LAD25NM\nW06000001,Ynys M\u00f4n\n".encode("utf-8"))
    d = read_csv_text(str(p))
    assert list(d.columns) == ["LAD25CD", "LAD25NM"] and d.loc[0, "LAD25NM"] == "Ynys M\u00f4n"
    q = tmp_path / "y.csv"
    q.write_bytes("LAD25CD,LAD25NM\nE1,Caf\xe9\n".encode("latin-1"))                    # not UTF-8: falls back
    assert read_csv_text(str(q)).loc[0, "LAD25NM"] == "Caf\u00e9"


def test_a_raw_ons_district_lookup_is_reshaped_for_the_rollup():
    ca = pd.DataFrame({"LAD25CD": ["E08000001", "E08000002", "E06000001"], "LAD25NM": ["Bolton", "Bury", "Hartlepool"], "CAUTH25CD": ["E47000001", "E47000001", ""],
                       "CAUTH25NM": ["Greater Manchester", "Greater Manchester", ""], "ObjectId": ["1", "2", "3"]})
    out = parse_district_lookup(ca, "combined_authority")
    assert list(out.columns) == ["district_code", "geography", "geography_code", "geography_name"]
    assert list(out["district_code"]) == ["E08000001", "E08000002"] and set(out["geography_name"]) == {"Greater Manchester"}     # Hartlepool is in none
    fra = pd.DataFrame({"\ufeffLAD23CD": ["E1"], "LAD23NM": ["x"], "FRA23CD": ["E31000001"], "FRA23NM": ["Avon FRS"], "FRA23NMW": [""]})
    f = parse_district_lookup(fra, "fire_and_rescue_authority")
    assert f.loc[0, "geography_code"] == "E31000001" and f.loc[0, "geography_name"] == "Avon FRS"
    import pytest
    with pytest.raises(ValueError):
        parse_district_lookup(pd.DataFrame({"a": ["1"]}), "x")


def test_a_geojson_feature_collection_becomes_a_frame_with_its_crs():
    import json
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"FRA24CD": "E31000001", "FRA24NM": "Avon"}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
                                                      {"type": "Feature", "properties": {"FRA24CD": "E31000002", "FRA24NM": "None here"}, "geometry": None}]}
    d, epsg = read_geojson_features(json.dumps(fc))
    assert epsg == 4326 and list(d["FRA24NM"]) == ["Avon", "None here"] and d.loc[0, "geometry"].startswith('{"type": "Polygon"') and pd.isna(d.loc[1, "geometry"])
    fc["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::27700"}}
    assert read_geojson_features(json.dumps(fc))[1] == 27700


def test_points_take_the_polygon_they_are_in_then_the_nearest_within_a_limit():
    from shapely.geometry import box
    polys = [box(0, 0, 1000, 1000), box(2000, 0, 3000, 1000), box(500, 0, 2500, 1000)]               # the third overlaps the first two
    idx, nearest = assign_points_to_polygons([100, 2900, 1500, 3400, 9000], [100, 100, 100, 100, 100], polys, max_nearest_m=600)
    assert list(idx) == [0, 1, 2, 1, -1]            # (100,100) is in 0 only; (2900) in 1; (1500) in 2 only; (3400) is 400 m from 1; (9000) is too far
    assert nearest == 1
    idx2, _ = assign_points_to_polygons([700], [100], polys)
    assert idx2[0] == 0                              # in polygons 0 and 2: the lower index wins
