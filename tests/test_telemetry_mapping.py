# Tests for the telemetry mapping match. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.telemetry_mapping import (  # noqa: E402
    add_import_only_rows, clean, id_key, fallback_external_id, family_of_code, family_of_text, flag_inactive, flag_out_of_scope, match_mapping, prepare_mapping, reconcile_family, settle_by_readings,
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


def test_a_supplied_list_sets_unmatched_sites_aside_and_keeps_real_matches():
    reg = register([("m/1", "S1", "level", "Stage", "900")])
    rows, _ = match_mapping(mapping([("a", "W1", "Pont Rhyd", "H.obs", "NOPE", "Water Level", None, "1"),
                                     ("b", "W2", "Matched anyway", "H.obs", "S1", "Water Level", None, "2"),
                                     ("c", "E1", "English Place", "H.obs", "NOPE2", "Water Level", None, "3"),
                                     ("d", "W3", "By telemetry id", "H.obs", "NOPE3", "Water Level", None, "4")]), reg)
    scope = pd.DataFrame({"FFFS ID": ["W1", "W2", None], "Telemetry_ID": [None, None, "NOPE3"],
                          "Reason": ["Wales (NRW)", "Wales (NRW)", "Wales (NRW)"]})
    out = flag_out_of_scope(rows, scope).set_index("id")
    assert out.loc["a", "match_status"] == "out_of_scope" and out.loc["a", "match_note"] == "Wales (NRW)"
    assert out.loc["d", "match_status"] == "out_of_scope"                      # listed by telemetry id
    assert out.loc["b", "match_status"] == "matched" and "listed as out of scope" in out.loc["b", "match_note"]
    assert out.loc["c", "match_status"] == "no_station"                         # not listed: still a miss
    assert flag_out_of_scope(rows, pd.DataFrame()).equals(rows)
    try:
        flag_out_of_scope(rows, pd.DataFrame({"Name": ["x"]}))
        assert False
    except ValueError as e:
        assert "FFFS_ID or TelemetryID" in str(e)


def test_a_keep_column_removes_the_rows_marked_false_even_if_they_matched():
    reg = register([("m/1", "S1", "level", "Stage", "900")])
    rows, _ = match_mapping(mapping([("a", "N1", "Welsh astro", "H.obs", "S1", "Water Level", None, "1"),     # matched, Keep FALSE
                                     ("b", "N2", "Welsh unmatched", "H.obs", "NOPE", "Water Level", None, "2"),
                                     ("c", "E1", "English", "H.obs", "S1", "Water Level", None, "3"),          # Keep TRUE
                                     ("d", "E2", "Not on the list", "H.obs", "NOPE2", "Water Level", None, "4")]), reg)
    scope = pd.DataFrame({"FFFS_ID": ["N1", "N2", "E1", None], "ParameterQualifier": ["x"] * 4,
                          "ImportSource": ["Astro", "Astro", "Telemetry", "Astro"], "Keep": ["FALSE", "false", "TRUE", ""]})
    out = flag_out_of_scope(rows, scope).set_index("id")
    assert out.loc["a", "match_status"] == "out_of_scope" and pd.isna(out.loc["a", "measure_uri"])    # the link is removed
    assert out.loc["a", "match_note"] == "Keep = FALSE (Astro)"
    assert out.loc["b", "match_status"] == "out_of_scope"
    assert out.loc["c", "match_status"] == "matched" and out.loc["c", "match_note"] == ""             # Keep TRUE: left alone
    assert out.loc["d", "match_status"] == "no_station"
    # Without a Keep column the old rule holds: a real match stays.
    plain = flag_out_of_scope(rows, pd.DataFrame({"FFFS_ID": ["N1"], "Reason": ["Wales"]})).set_index("id")
    assert plain.loc["a", "match_status"] == "matched" and "out of scope" in plain.loc["a", "match_note"]
    # Every Keep is TRUE: nothing is set aside.
    assert flag_out_of_scope(rows, pd.DataFrame({"FFFS_ID": ["N1"], "Keep": ["TRUE"]})).equals(rows)


def test_a_station_with_logged_and_two_stage_forms_settles_on_the_stage_in_a_datum():
    reg = pd.DataFrame({"measure_uri": ["m/4048-nodatum", "m/4048-logged", "m/4048-stage"], "station_reference": ["4048"] * 3,
                        "station_label": ["Wingfield Park"] * 3, "parameter": ["level"] * 3,
                        "qualifier": ["Stage", "Logged", "Stage"], "period": ["900"] * 3, "unit_name": ["---", "mASD", "mASD"]})
    rows, _ = match_mapping(mapping([("a", "WP1", "Wingfield Park", "H.obs", "4048", "Water Level", None, "4048")]), reg)
    assert list(rows["match_status"]) == ["matched"] and rows.iloc[0]["measure_uri"] == "m/4048-stage"
    assert "Logged level set aside" in rows.iloc[0]["match_note"] and "no datum" in rows.iloc[0]["match_note"]
    # A file that names Logged still gets the Logged measure.
    rows, _ = match_mapping(mapping([("a", "WP1", "Wingfield Park", "H.obs", "4048", "Water Level", "Logged", "4048")]), reg)
    assert rows.iloc[0]["measure_uri"] == "m/4048-logged"
    # Only no-datum and Logged measures with nothing better: nothing is emptied, so the row stays ambiguous or matched, never lost.
    reg2 = reg[reg["measure_uri"] != "m/4048-stage"]
    rows, _ = match_mapping(mapping([("a", "WP1", "Wingfield Park", "H.obs", "4048", "Water Level", None, "4048")]), reg2)
    assert len(rows) >= 1 and rows["match_status"].isin(["matched", "ambiguous"]).all()


def test_a_row_with_no_station_is_retried_on_the_import_lists_external_id():
    reg = register([("m/2230TH-stage", "2230TH", "level", "Stage", "900"), ("m/2230TH-down", "2230TH", "level", "Downstream Stage", "900")])
    m = mapping([("1", "2230_w1TH", "Knighton Main", "H.obs", "2230_w1TH", "Water Level", "Stage", "2230_w1TH"),
                 ("2", "NOPE", "Nowhere", "H.obs", "E9999", "Water Level", None, "9"),
                 ("3", "GONE", "Dropped", "H.obs", "2230_w2TH", "Water Level", "Stage", "9")])
    rows, _ = match_mapping(m, reg)
    assert set(rows["match_status"]) == {"no_station"}
    scope = pd.DataFrame({"FFFS_ID": ["2230_w1TH", "NOPE", "GONE"], "ExternalID": ["2230TH", "E9999", "2230TH"],
                          "Keep": ["TRUE", "TRUE", "FALSE"]})
    out, n = fallback_external_id(rows, m, scope, reg)
    by = out.set_index("id")
    assert n == 1
    assert by.loc["1", "match_status"] == "matched" and by.loc["1", "measure_uri"] == "m/2230TH-stage"
    assert by.loc["1", "telemetry_id"] == "2230_w1TH"                       # the file's own TelemetryID is kept
    assert "ExternalID 2230TH" in by.loc["1", "match_note"]
    assert by.loc["2", "match_status"] == "no_station"                      # the ExternalID found nothing either
    assert by.loc["3", "match_status"] == "no_station"                      # Keep = FALSE is never retried
    assert len(out) == 3
    same, n0 = fallback_external_id(rows, m, pd.DataFrame({"FFFS_ID": ["x"], "ExternalID": ["y"]}), reg)
    assert n0 == 0 and len(same) == 3


def test_an_fffs_id_that_is_true_and_false_loses_only_the_row_the_false_line_describes():
    reg = register([("m/2001-level", "2001", "level", "Stage", "900")])
    m = mapping([("1", "F1", "Real", "H.obs", "2001", "Water Level", None, "11"),
                 ("2", "F1", "Welsh", "H.obs", "NRW77", "Water Level", None, "12"),
                 ("3", "F2", "Plain", "H.obs", "NRW88", "Water Level", None, "13")])
    rows, _ = match_mapping(m, reg)
    scope = pd.DataFrame({"FFFS_ID": ["F1", "F1", "F2"], "ExternalID": ["2001", "NRW77", "NRW88"], "Keep": ["TRUE", "FALSE", "FALSE"]})
    out = flag_out_of_scope(rows, scope).drop_duplicates("id").set_index("id")
    assert out.loc["1", "match_status"] == "matched"          # F1 is TRUE for 2001: the real-time row survives
    assert out.loc["2", "match_status"] == "out_of_scope"     # its NRW line is FALSE
    assert out.loc["3", "match_status"] == "out_of_scope"     # F2 is FALSE only: unchanged

def test_level_measures_that_differ_only_by_datum_are_each_matched_with_the_unit_kept():
    reg = pd.DataFrame({"measure_uri": ["m/a-maod", "m/a-masd"], "station_reference": ["4098"] * 2, "station_label": ["Keadby"] * 2,
                        "parameter": ["level"] * 2, "qualifier": ["Tidal Level"] * 2, "period": ["300"] * 2, "unit_name": ["mAOD", "mASD"]})
    rows, report = match_mapping(mapping([("a", "KEA1", "Keadby", "H.obs", "4098", "Water Level", None, "4098")]), reg)
    assert list(rows["match_status"]) == ["matched", "matched"]
    assert set(rows["measure_uri"]) == {"m/a-maod", "m/a-masd"} and set(rows["unit_name"]) == {"mAOD", "mASD"}
    assert "differ only by datum" in rows.iloc[0]["match_note"]
    # A different period or qualifier is a real ambiguity, not a datum variant.
    reg2 = reg.copy()
    reg2.loc[1, "period"] = "900"
    rows, _ = match_mapping(mapping([("a", "KEA1", "Keadby", "H.obs", "4098", "Water Level", None, "4098")]), reg2)
    assert set(rows["match_status"]) == {"ambiguous"}


def test_a_numeric_id_that_lost_its_leading_zero_is_matched_with_it_restored_only_when_as_written_fails():
    reg = register([("m/022006", "022006", "level", "Stage", "900"), ("m/2001", "2001", "level", "Stage", "900")])
    m = mapping([("1", "A", "Lost zero", "H.obs", "22006", "Water Level", None, "1"),
                 ("2", "B", "Four digits as written", "H.obs", "2001", "Water Level", None, "2"),
                 ("3", "C", "Nothing", "H.obs", "31999", "Water Level", None, "3")])
    rows, _ = match_mapping(m, reg)
    by = rows.set_index("id")
    assert by.loc["1", "match_status"] == "matched" and by.loc["1", "measure_uri"] == "m/022006"
    assert "leading zeros restored" in by.loc["1", "match_note"] and by.loc["1", "telemetry_id"] == "22006"
    assert by.loc["2", "measure_uri"] == "m/2001" and not by.loc["2", "match_note"]
    assert by.loc["3", "match_status"] == "no_station"


def test_an_instantaneous_level_is_preferred_to_a_mean_when_the_file_names_no_qualifier():
    reg = pd.DataFrame({"measure_uri": ["m/nh-inst", "m/nh-mean"], "station_reference": ["SE71739"] * 2, "station_label": ["Newhaven"] * 2,
                        "parameter": ["level"] * 2, "qualifier": ["Tidal Level"] * 2, "period": ["900"] * 2,
                        "unit_name": ["m", "mAOD"], "value_type": ["instantaneous", "mean"]})
    rows, _ = match_mapping(mapping([("a", "NH1", "Newhaven", "H.obs", "SE71739", "Water Level", None, "1")]), reg)
    assert list(rows["match_status"]) == ["matched"] and rows.iloc[0]["measure_uri"] == "m/nh-inst"
    assert "non-instantaneous" in rows.iloc[0]["match_note"]
    # Unknown value types are not guessed: both measures differ only by unit, so each is matched as a datum variant.
    rows, _ = match_mapping(mapping([("a", "NH1", "Newhaven", "H.obs", "SE71739", "Water Level", None, "1")]), reg.drop(columns="value_type"))
    assert list(rows["match_status"]) == ["matched", "matched"]


def test_a_groundwater_row_falls_back_on_a_level_measure_and_a_level_row_on_a_groundwater_one():
    reg = pd.DataFrame({"measure_uri": ["m/E46643", "m/E16181"], "station_reference": ["E46643", "E16181"], "station_label": ["Borehole", "Sweeps Lane"],
                        "parameter": ["level", "level"], "qualifier": ["Stage", "Groundwater"], "period": ["3600", "900"], "unit_name": ["mAOD", "mAOD"],
                        "value_type": ["instantaneous", "instantaneous"]})
    rows, _ = match_mapping(mapping([("1", "G1", "Borehole", "GW.obs", "E46643", "Water Level", None, "1"),
                                     ("2", "G2", "Sweeps", "H.obs", "E16181", "Water Level", None, "2"),
                                     ("3", "R1", "Rain", "P.obs", "E46643", "Rainfall", None, "3")]), reg)
    by = rows.set_index("id")
    assert by.loc["1", "match_status"] == "matched" and by.loc["1", "measure_uri"] == "m/E46643"
    assert "no groundwater measure" in by.loc["1", "match_note"]
    assert by.loc["2", "match_status"] == "matched" and by.loc["2", "measure_uri"] == "m/E16181"
    assert by.loc["3", "match_status"] == "no_parameter"            # only level and groundwater are interchangeable


def test_import_rows_the_mapping_file_lacks_are_added_when_their_external_id_finds_a_measure():
    reg = pd.DataFrame({"measure_uri": ["m/074108", "m/2001"], "station_reference": ["074108", "2001"], "station_label": ["Rainfall station", "Bewdley"],
                        "parameter": ["rainfall", "level"], "qualifier": ["Tipping Bucket Raingauge", "Stage"], "period": ["900", "900"],
                        "unit_name": ["mm", "m"], "value_type": ["instantaneous", "instantaneous"]})
    m = mapping([("1", "BEW1", "Bewdley", "H.obs", "2001", "Water Level", None, "1")])
    rows, _ = match_mapping(m, reg)
    scope = pd.DataFrame({"FFFS_ID": ["BEW1", "P74108", "P99999", "GONE", "BLANK"], "Parameter": ["H.obs", "P.obs", "P.obs", "P.obs", "P.obs"],
                          "ExternalID": ["2001", "74108", "99999", "074108", "074108"], "ExternalParameter": ["Water Level", "Rainfall", "Rainfall", "Rainfall", "Rainfall"],
                          "Keep": ["TRUE", "TRUE", "TRUE", "FALSE", ""]})
    out, n = add_import_only_rows(rows, m, scope, reg)
    assert n == 1 and len(out) == 2                                  # P74108 only: BEW1 is in the file, 99999 finds nothing, GONE/BLANK are not kept
    new = out[out["fffs_id"] == "P74108"].iloc[0]
    assert new["match_status"] == "matched" and new["measure_uri"] == "m/074108" and new["telemetry_id"] == "74108"
    assert "import list only" in new["match_note"] and "leading zeros restored" in new["match_note"]
    same, n0 = add_import_only_rows(rows, m, pd.DataFrame({"FFFS_ID": ["x"], "ExternalID": ["y"]}), reg)
    assert n0 == 0 and len(same) == len(rows)


def test_ids_are_compared_without_regard_to_leading_zeros_between_the_import_list_and_the_mapping_file():
    assert id_key("083280") == id_key("83280") == "83280"
    assert id_key("E05720") == id_key("e5720") == "E5720"
    assert id_key("0155TH") == id_key("155th") == "155TH"
    assert id_key("2230_w1TH") == "2230_W1TH" and id_key(None) is None and id_key("000") == "0"
    reg = register([("m/083280", "083280", "rainfall", "Tipping Bucket Raingauge", "900")])
    m = mapping([("1", "083280", "Rain", "P.obs", "083280", "Rainfall", None, "1")])
    rows, _ = match_mapping(m, reg)
    scope = pd.DataFrame({"FFFS_ID": ["83280", "83280"], "Parameter": ["P.obs", "P.obs"], "ExternalID": ["83280", "N9"],
                          "ExternalParameter": ["Rainfall", "Rainfall"], "Keep": ["TRUE", "FALSE"]})
    out, n = add_import_only_rows(rows, m, scope, reg)
    assert n == 0 and len(out) == len(rows)                       # the file has 083280: nothing is added as import-only
    gone = flag_out_of_scope(rows, pd.DataFrame({"FFFS_ID": ["83280"], "Keep": ["FALSE"]}))
    assert set(gone["match_status"]) == {"out_of_scope"}           # a FALSE line without the zero still finds its row
