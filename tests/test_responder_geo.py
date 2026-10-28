# Tests for the responder geography helpers. Run with:  pytest tests
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.responder_geo import GEOGRAPHIES, find_reference_file, parse_code_name, rollup_through_lookup  # noqa: E402

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
