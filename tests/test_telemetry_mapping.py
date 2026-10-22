# Tests for the telemetry mapping match. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.telemetry_mapping import (  # noqa: E402
    clean, family_of_code, family_of_text, flag_inactive, match_mapping, prepare_mapping, reconcile_family, settle_by_readings,
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


def test_a_groundwater_gauge_called_water_level_is_groundwater_not_a_conflict():
    # TelemetryParameter says Water Level, ParameterID says GW.obs: the code is the more specific.
    rows, _ = run([("a", "X", "Gauge", "GW.obs", "SU74_73", "Water Level", "Groundwater", "9")])
    assert rows.loc[0, "match_status"] == "matched" and rows.loc[0, "measure_uri"] == "m/gw-1"
    rows, _ = run([("a", "X", "Gauge", "GW.obs", "SU74_73", "Groundwater", "Groundwater", "9")])
    assert rows.loc[0, "match_status"] == "matched" and rows.loc[0, "measure_uri"] == "m/gw-1"
    # It must not match the station's ordinary level measure.
    reg = register([("m/lvl", "S5", "level", "Stage", "900"), ("m/gw", "S5", "level", "Groundwater", "900")])
    rows, _ = match_mapping(mapping([("a", "X", "G", "GW.obs", "S5", "Water Level", None, "1")]), reg)
    assert list(rows["measure_uri"]) == ["m/gw"]


def test_other_disagreements_are_still_conflicts():
    assert reconcile_family("level", "groundwater") == ("groundwater", False)
    assert reconcile_family("groundwater", "level") == (None, True)           # the other way round is not the same thing
    assert reconcile_family("flow", "level") == (None, True)
    assert reconcile_family("level", None) == ("level", False) and reconcile_family(None, "flow") == ("flow", False)


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
    assert report["wiski_id_in_scientific_notation"] == 1
    # Nothing in the file is lost: an unrecognised column is kept, as text, and the WISKI id as the file held it.
    assert report["extra_columns"] == ["src_extra"] and list(m["src_extra"]) == ["1", "2"]
    assert list(m["wiski_id_in_file"]) == ["4.53E+08", "713207"]


def test_extra_columns_survive_the_match():
    raw = pd.DataFrame({"ID": ["a"], "FFFS_ID": ["F1"], "FFFSLocName": ["Cwm Rheidol RG"], "ParameterID": ["P.obs"],
                        "TelemetryID": ["520455"], "TelemetryParameter": ["Rainfall"], "Station Name": ["Cwm Rheidol"],
                        "Notes (free text)": ["check datum"]})
    m, report = prepare_mapping(raw)
    assert report["extra_columns"] == ["src_station_name", "src_notes_free_text"]
    rows, _ = match_mapping(m, REG)
    assert rows.loc[0, "src_station_name"] == "Cwm Rheidol" and rows.loc[0, "src_notes_free_text"] == "check datum"
    assert rows.loc[0, "match_status"] == "matched"


NOW = pd.Timestamp("2026-10-22 12:00")


def twin_stations():
    """Telemetry 023041 lists two level measures; only one ever delivers readings."""
    reg = register([("m/live", "023041", "level", "Stage", "900"), ("m/dead", "023041", "level", "Stage", "900")])
    rows, _ = match_mapping(mapping([("a", "F1", "Gauge", "H.obs", "023041", "Water Level", "Stage", "9")]), reg)
    return rows


def activity(items):
    return pd.DataFrame(items, columns=["measure_uri", "n_readings", "first_reading", "last_reading"])


def test_leading_zero_telemetry_ids_are_kept_and_a_dead_twin_is_dropped_by_readings():
    rows = twin_stations()
    assert list(rows["match_status"]) == ["ambiguous", "ambiguous"] and rows.loc[0, "telemetry_id"] == "023041"
    out, settled = settle_by_readings(rows, activity([("m/live", 900, "2020-01-01", "2026-10-22 11:45")]), NOW)
    assert settled == 1 and list(out["measure_uri"]) == ["m/live"] and list(out["match_status"]) == ["matched"]
    assert "settled by readings" in out.loc[0, "match_note"] and out.loc[0, "n_readings"] == 900


def test_two_measures_with_data_are_settled_only_if_one_is_recent():
    rows = twin_stations()
    both = activity([("m/live", 900, "2020-01-01", "2026-10-22 11:45"), ("m/dead", 50, "2015-01-01", "2016-03-01")])
    out, settled = settle_by_readings(rows, both, NOW)
    assert settled == 1 and list(out["measure_uri"]) == ["m/live"]
    still = activity([("m/live", 900, "2020-01-01", "2026-10-22 11:45"), ("m/dead", 900, "2020-01-01", "2026-10-21 11:45")])
    out, settled = settle_by_readings(rows, still, NOW)
    assert settled == 0 and list(out["match_status"]) == ["ambiguous", "ambiguous"]      # both live: not guessed
    assert list(out["n_readings"]) == [900, 900]
    out, settled = settle_by_readings(rows, activity([]), NOW)                            # neither has data
    assert settled == 0 and list(out["n_readings"]) == [0, 0]


def test_matched_rows_get_their_readings_and_unmatched_rows_none():
    reg = register([("m/1", "S1", "level", "Stage", "900")])
    rows, _ = match_mapping(mapping([("a", "F", "G", "H.obs", "S1", "Water Level", None, "1"), ("b", "F", "G", "H.obs", "NOPE", "Water Level", None, "1")]), reg)
    out, _ = settle_by_readings(rows, activity([("m/1", 5, "2026-01-01", "2026-10-21")]), NOW)
    by = out.set_index("id")
    assert by.loc["a", "n_readings"] == 5 and pd.isna(by.loc["b", "n_readings"])


def test_only_the_space_after_tg_becomes_an_underscore():
    reg = register([("m/tg", "067033_TG_148", "rainfall", "Tipping Bucket Raingauge", "900"),
                    ("m/lv", "VCHURCH-T_TG_341", "level", "Stage", "900"),
                    ("m/sp", "Some_Name_12", "level", "Stage", "900")])
    rows, _ = match_mapping(mapping([("a", "F1", "Chester Sus Bridge", "P.obs", "067033_TG 148", "Rainfall", None, "1"),
                                     ("b", "F2", "Vowchurch", "H.obs", "VCHURCH-T_TG 341", "Water Level", None, "2"),
                                     ("e", "F5", "Other", "H.obs", "Some Name 12", "Water Level", None, "5")]), reg)
    by = rows.set_index("id")
    assert by.loc["a", "match_status"] == "matched" and by.loc["a", "measure_uri"] == "m/tg"
    assert "after _TG" in by.loc["a", "match_note"] and by.loc["b", "measure_uri"] == "m/lv"
    # Any other ID with a space is NOT rewritten, even though an underscore version exists.
    assert by.loc["e", "match_status"] == "no_station" and pd.isna(by.loc["e", "measure_uri"])
    # An ID that already matches as written is left alone, and one that matches neither way is still no_station.
    rows, _ = match_mapping(mapping([("c", "F", "G", "P.obs", "067033_TG_148", "Rainfall", None, "1"),
                                     ("d", "F", "G", "P.obs", "067033 TG 148 X", "Rainfall", None, "1")]), reg)
    by = rows.set_index("id")
    assert by.loc["c", "match_status"] == "matched" and by.loc["c", "match_note"] == ""
    assert by.loc["d", "match_status"] == "no_station"


def test_inactive_sites_that_matched_nothing_are_set_aside_but_a_real_match_is_kept():
    reg = register([("m/1", "S1", "level", "Stage", "900"), ("m/2", "S2", "level", "Stage", "900"), ("m/3", "S2", "level", "Stage", "900")])
    rows, report = match_mapping(mapping([
        ("a", "F1", "Pevensey INACTIVE", "H.obs", "NOPE", "Water Level", None, "1"),      # nothing matched: set aside
        ("b", "F2", "Herne Bay inactive", "H.obs", "S1", "Water Level", None, "2"),       # matched a live measure: kept
        ("c", "F3", "Twin INACTIVE", "H.obs", "S2", "Water Level", None, "3"),            # ambiguous (two candidates): set aside once
        ("d", "F4", "Live Place", "H.obs", "NOPE", "Water Level", None, "4")]), reg)
    by = rows.drop_duplicates("id").set_index("id")
    assert by.loc["a", "match_status"] == "inactive" and by.loc["c", "match_status"] == "inactive"
    assert by.loc["b", "match_status"] == "matched" and "INACTIVE" in by.loc["b", "match_note"]
    assert by.loc["d", "match_status"] == "no_station"                                      # an ordinary miss is still a miss
    assert (rows["id"] == "c").sum() == 1 and pd.isna(rows.loc[rows["id"] == "c", "measure_uri"].iloc[0])
    assert report["statuses"]["inactive"] == 2
    assert flag_inactive(rows.iloc[0:0]).empty
