# Tests for the telemetry mapping match. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.telemetry_mapping import (  # noqa: E402
    clean, family_of_code, family_of_text, match_mapping, prepare_mapping,
)


def register(rows):
    """rows: (measure_uri, station_reference, parameter, qualifier, period)."""
    return pd.DataFrame({"measure_uri": [r[0] for r in rows], "station_reference": [r[1] for r in rows],
                         "station_label": ["Gauge " + r[1] for r in rows], "parameter": [r[2] for r in rows],
                         "qualifier": [r[3] for r in rows], "period": [r[4] for r in rows], "unit_name": ["x"] * len(rows)})


def mapping(rows):
    """rows: (id, fffs_id, name, parameter_id, telemetry_id, telemetry_parameter, qualifier, wiski_id)."""
    return prepare_mapping(pd.DataFrame(rows, columns=[
        "ID", "FFFS_ID", "FFFSLocName", "ParameterID", "TelemetryID", "TelemetryParameter", "ParameterQualifier", "WISKI_ID"]))[0]


REG = register([("m/2001-level", "2001", "level", "Stage", "900"), ("m/2001-flow", "2001", "flow", "Flow", "900"),
                ("m/rg-1", "520455", "rainfall", "Tipping Bucket Raingauge", "900"),
                ("m/gw-1", "SU74_73", "level", "Groundwater", "900")])


def run(rows):
    return match_mapping(mapping(rows), REG)


def test_the_same_telemetry_id_matches_flow_and_stage_each_to_its_own_measure():
    rows, report = run([("a", "BEW1", "Bewdley", "H.obs", "2001", "Water Level", "Stage", "11"),
                        ("b", "BEW2", "Bewdley", "Q.obs", "2001", "Flow", "Flow", "12")])
    by = rows.set_index("id")
    assert by.loc["a", "measure_uri"] == "m/2001-level" and by.loc["b", "measure_uri"] == "m/2001-flow"
    assert set(rows["match_status"]) == {"matched"} and report["measures_matched"] == 2


def test_the_station_alone_never_matches():
    rows, _ = run([("a", "X", "Gauge", "P.obs", "2001", "Rainfall", "Tipping Bucket Raingauge", "9")])
    assert rows.loc[0, "match_status"] == "no_parameter" and pd.isna(rows.loc[0, "measure_uri"])
    assert "level, flow" in rows.loc[0, "match_note"] or "flow, level" in rows.loc[0, "match_note"]


def test_disagreeing_columns_are_a_conflict_and_not_guessed():
    rows, report = run([("a", "X", "Gauge", "Q.obs", "2001", "Water Level", "Stage", "9")])
    assert rows.loc[0, "match_status"] == "conflict" and pd.isna(rows.loc[0, "measure_uri"])
    assert report["statuses"] == {"conflict": 1}


def test_either_column_alone_can_name_the_family():
    rows, _ = run([("a", "X", "Gauge", None, "520455", "Rainfall", None, "9"),
                   ("b", "Y", "Gauge", "P.obs", "520455", None, None, "9")])
    assert set(rows["match_status"]) == {"matched"} and set(rows["measure_uri"]) == {"m/rg-1"}


def test_groundwater_is_not_confused_with_level():
    rows, _ = run([("a", "X", "Gauge", "GW.obs", "SU74_73", "Water Level", "Groundwater", "9")])
    # TelemetryParameter says level, ParameterID says groundwater: a conflict, written as such.
    assert rows.loc[0, "match_status"] == "conflict"
    rows, _ = run([("a", "X", "Gauge", "GW.obs", "SU74_73", "Groundwater", "Groundwater", "9")])
    assert rows.loc[0, "match_status"] == "matched" and rows.loc[0, "measure_uri"] == "m/gw-1"


def test_several_fitting_measures_are_ambiguous_unless_the_qualifier_settles_it():
    reg = register([("m/a", "7", "level", "Stage", "900"), ("m/b", "7", "level", "Downstream Stage", "900")])
    rows, report = match_mapping(mapping([("a", "X", "G", "H.obs", "7", "Water Level", None, "1")]), reg)
    assert list(rows["match_status"]) == ["ambiguous", "ambiguous"] and set(rows["measure_uri"]) == {"m/a", "m/b"}
    rows, _ = match_mapping(mapping([("a", "X", "G", "H.obs", "7", "Water Level", "Downstream Stage", "1")]), reg)
    assert list(rows["match_status"]) == ["matched"] and rows.loc[0, "measure_uri"] == "m/b"


def test_unmatched_rows_say_why():
    rows, report = run([("a", "X", "G", "H.obs", "NOPE", "Water Level", None, "1"),
                        ("b", "X", "G", "H.obs", None, "Water Level", None, "1"),
                        ("c", "X", "G", "Odd.obs", "2001", "Mystery", None, "1")])
    assert dict(zip(rows["id"], rows["match_status"])) == {"a": "no_station", "b": "no_telemetry_id", "c": "unknown_parameter"}
    assert report["measures_matched"] == 0


def test_a_measure_wanted_by_two_rows_is_reported_and_both_rows_keep_their_ids():
    rows, report = run([("a", "F1", "G", "P.obs", "520455", "Rainfall", None, "1"),
                        ("b", "F2", "G", "P.obs", "520455", "Rainfall", None, "2")])
    assert report["measures_matched_by_several_rows"] == 1 and sorted(rows["fffs_id"]) == ["F1", "F2"]


def test_cleaning_the_file():
    assert clean(" 4687.0 ") == "4687" and clean("nan") is None and clean(None) is None and clean("E3474") == "E3474"
    assert family_of_text("Significant wave height (Hm0)") == "wave" and family_of_text("Dominant (peak) wave period") == "wave"
    assert family_of_code("Wave.obs.peak.period") == "wave" and family_of_code("H.obs") == "level" and family_of_code("Abst.profile") is None
    m, report = prepare_mapping(pd.DataFrame({"TelemetryID": [4687.0, "E1"], "WISKI_ID": ["4.53E+08", "713207"],
                                              "FFFS_ID": ["a", "b"], "Extra": [1, 2]}))
    assert list(m["telemetry_id"]) == ["4687", "E1"] and pd.isna(m["wiski_id"].iloc[0]) and m["wiski_id"].iloc[1] == "713207"
    assert report["wiski_id_in_scientific_notation"] == 1 and "Extra" not in m
