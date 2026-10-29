# Tests for the Census and deprivation readers. Run with:  pytest tests
import io
import os
import sys
import zipfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.census_load import (  # noqa: E402
    age65plus_columns, census_oa_table, find_iod_links, iod_lsoa_table, read_nomis_zip,
)


def nomis_zip(name, frames):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for level, df in frames.items():
            z.writestr(f"census2021-{name}-{level}.csv", df.to_csv(index=False))
    return buf.getvalue()


TS001 = pd.DataFrame({"date": 2021, "geography": ["a", "b", "c"], "geography code": ["E00000001", "E00000002", "W00000009"],
                      "Residence type: Total; measures: Value": [300, 150, 90],
                      "Residence type: Lives in a household; measures: Value": [290, 150, 90]})
TS007A = pd.DataFrame({"date": 2021, "geography": ["a", "b", "c"], "geography code": ["E00000001", "E00000002", "W00000009"],
                       "Age: Total": [300, 150, 90], "Age: Aged 4 years and under": [20, 5, 4], "Age: Aged 60 to 64 years": [30, 10, 6],
                       "Age: Aged 65 to 69 years": [25, 10, 8], "Age: Aged 70 to 74 years": [20, 5, 3], "Age: Aged 85 years and over": [5, 10, 4],
                       "Age: Aged 5 to 9 years": [200, 110, 65]})
TS041 = pd.DataFrame({"date": 2021, "geography": ["a", "b", "c"], "geography code": ["E00000001", "E00000002", "W00000009"],
                      "Number of households: Total; measures: Value": [120, 60, 40]})


def test_a_nomis_zip_gives_the_csv_for_the_level_asked_for():
    z = nomis_zip("ts001", {"oa": TS001, "lsoa": TS001.iloc[:1]})
    assert len(read_nomis_zip(z, "oa")) == 3 and len(read_nomis_zip(z, "lsoa")) == 1
    try:
        read_nomis_zip(z, "msoa")
        assert False
    except ValueError as e:
        assert "-msoa.csv" in str(e)


def test_age_bands_from_65_are_found_by_their_text_and_summed():
    assert age65plus_columns(TS007A) == ["Age: Aged 65 to 69 years", "Age: Aged 70 to 74 years", "Age: Aged 85 years and over"]
    t, rep = census_oa_table(TS001, TS007A, TS041)
    t = t.set_index("oa")
    assert t.loc["E00000001", "population"] == 300 and t.loc["E00000001", "age65plus"] == 50 and t.loc["E00000001", "households"] == 120
    assert round(t.loc["E00000002", "share_65plus"], 4) == round(25 / 150, 4)
    assert rep["england_rows"] == 2 and rep["rows"] == 3 and rep["age_total_differs_from_population_in"] == 0


def test_an_age_table_that_does_not_add_up_is_flagged_and_missing_households_are_blank():
    bad = TS007A.copy(); bad.loc[0, "Age: Total"] = 310
    t, rep = census_oa_table(TS001, bad)
    assert rep["age_total_differs_from_population_in"] == 1 and t["households"].isna().all()
    try:
        census_oa_table(TS001, TS007A.drop(columns=["Age: Aged 65 to 69 years", "Age: Aged 70 to 74 years", "Age: Aged 85 years and over"]))
        assert False
    except ValueError as e:
        assert "65" in str(e)


def test_the_iod_file_link_is_picked_from_the_statistics_page():
    html = ('<a href="https://assets.publishing.service.gov.uk/media/1/File_1_IoD2025_Domains.xlsx">x</a>'
            '<a href="https://assets.publishing.service.gov.uk/media/2/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv">y</a>'
            '<a href="https://assets.publishing.service.gov.uk/media/3/Report.pdf">z</a>')
    assert find_iod_links(html)[0].endswith("File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv")


def test_the_iod_columns_are_found_by_text_and_domain_deciles_are_kept():
    raw = pd.DataFrame({"LSOA code (2021)": ["E01000001", "E01000002", "W01000001"], "LSOA name (2021)": ["a", "b", "c"],
                        "Index of Multiple Deprivation (IMD) Score": [10.5, 30.1, 5.0],
                        "Index of Multiple Deprivation (IMD) Rank (where 1 is most deprived)": [20000, 5000, 30000],
                        "Index of Multiple Deprivation (IMD) Decile (where 1 is most deprived 10% of LSOAs)": [6, 2, 9],
                        "Income Decile (where 1 is most deprived 10% of LSOAs)": [7, 1, 9]})
    t, rep = iod_lsoa_table(raw)
    assert list(t["imd_decile"]) == [6, 2, 9] and list(t["imd_rank"]) == [20000, 5000, 30000] and rep["england_lsoas"] == 2
    assert "domain_income_decile" in t.columns
    try:
        iod_lsoa_table(raw.drop(columns=["LSOA code (2021)"]))
        assert False
    except ValueError as e:
        assert "Columns present" in str(e)


def test_the_households_table_with_no_total_in_its_column_name_is_read():
    # the real ts041 header, as shown by the first run of job 29
    ts001 = pd.DataFrame({"geography code": ["E00000001"], "Residence type: Total; measures: Value": [300]})
    ts007a = pd.DataFrame({"geography code": ["E00000001"], "Age: Total": [300], "Age: Aged 65 to 69 years": [50]})
    ts041 = pd.DataFrame({"date": [2021], "geography": ["x"], "geography code": ["E00000001"],
                          "Number of households: Number of households; measures: Value": [120]})
    t, rep = census_oa_table(ts001, ts007a, ts041)
    assert t.set_index("oa").loc["E00000001", "households"] == 120
    assert "Number of households" in rep["households_column"]


def test_the_iod_link_finder_skips_csv_preview_pages_and_makes_links_absolute():
    html = ('<a href="/csv-preview/691ded/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv">preview</a>'
            '<a href="https://assets.publishing.service.gov.uk/media/691ded/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv">file</a>'
            '<a href="https://assets.publishing.service.gov.uk/media/691ded/File_5_IoD2025_Scores_for_the_Indices_of_Deprivation.xlsx">five</a>'
            '<a href="/government/uploads/File_7_something.csv">relative</a>')
    links = find_iod_links(html)
    assert all("csv-preview" not in u for u in links)
    assert links[0] == "https://assets.publishing.service.gov.uk/media/691ded/File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv"
    assert all(u.startswith("http") for u in links)


def test_the_report_gives_the_size_of_any_age_table_difference():
    ts001 = pd.DataFrame({"geography code": ["E1", "E2", "E3"], "Residence type: Total; measures: Value": [100, 100, 100]})
    ts007a = pd.DataFrame({"geography code": ["E1", "E2", "E3"], "Age: Total": [100, 101, 120], "Age: Aged 65 to 69 years": [5, 5, 5]})
    _, rep = census_oa_table(ts001, ts007a, None)
    assert rep["age_total_differs_from_population_in"] == 2 and rep["age_total_max_difference"] == 20
    assert rep["age_total_differs_by_more_than_5"] == 1 and rep["age_total_difference_in_people"] == 21


def test_vulnerability_table_sums_the_columns_found_by_their_text():
    import pytest
    from utils.census_load import census_vulnerability_table
    ts045 = pd.DataFrame({"date": 2021, "geography": ["a", "b"], "geography code": ["E00000001", "E00000002"],
                          "Car or van availability: Total: All households": [10, 20], "Car or van availability: No cars or vans in household": [3, 5],
                          "Car or van availability: 1 car or van in household": [7, 15]})
    ts037 = pd.DataFrame({"date": 2021, "geography": ["a", "b"], "geography code": ["E00000001", "E00000002"],
                          "General health: Total: All usual residents": [30, 50], "General health: Very good health": [10, 20],
                          "General health: Bad health": [2, 3], "General health: Very bad health": [1, 1]})
    ts038 = pd.DataFrame({"date": 2021, "geography": ["a", "b"], "geography code": ["E00000001", "E00000002"],
                          "Disability: Total: All usual residents": [30, 50],
                          "Disability: Disabled under the Equality Act: Day-to-day activities limited a lot": [4, 6],
                          "Disability: Disabled under the Equality Act: Day-to-day activities limited a little": [5, 7]})
    t, rep = census_vulnerability_table(ts045, ts037, ts038)
    assert list(t["oa"]) == ["E00000001", "E00000002"]
    assert list(t["no_car_households"]) == [3, 5] and list(t["bad_health"]) == [3, 4] and list(t["limited_lot"]) == [4, 6]
    assert len(rep["bad_health_columns"]) == 2
    with pytest.raises(ValueError):
        census_vulnerability_table(ts045.drop(columns=["Car or van availability: No cars or vans in household"]), None, None)
    t2, _ = census_vulnerability_table(ts045, None, None)
    assert t2["bad_health"].isna().all()
