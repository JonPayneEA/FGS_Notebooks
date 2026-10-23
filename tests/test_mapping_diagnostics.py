# Tests for the telemetry mapping diagnostics. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.mapping_diagnostics import (  # noqa: E402
    candidate_measures, candidate_summary, classify_ambiguity, matched_name_check, name_similarity, stage_pairs,
    gap_list, id_shape, keep_true_status, listed_station_check, numeric_id_variants, pair_by_external_id, profile_unpaired_kept, profile_unplaced, station_candidates, tidal_summary, tidy_id, unmapped_measures, welsh_hint,
)

STATIONS = pd.DataFrame({
    "stationReference": ["S100", "S200", "0412", "S400", "S500"],
    "notation": ["S100", "N-200", "0412", "S400", "S500"],
    "label": ["Aldwyn Bridge", "Brenn Weir", "Cardell Mill", "Dunmere Ford", "Hexton Rainfall station"],
    "RLOIid": ["7001", "7002", None, None, None],
    "wiskiID": ["W1", "W2", "W3", "W4", None]})


def rows(items):
    """items: (id, telemetry_id, wiski_id, name)."""
    return pd.DataFrame(items, columns=["id", "telemetry_id", "wiski_id", "fffs_location_name"])


def best(cands, rid):
    c = cands[cands["id"] == rid]
    return None if c.empty else (c.iloc[0]["how"], c.iloc[0]["candidate_station_reference"])


def test_each_route_finds_its_station():
    r = rows([("a", "7001", None, "x"),                  # RLOIid
              ("b", "N-200", None, "x"),                 # notation
              ("c", "ZZZ", "W4", "x"),                   # the file's WISKI_ID is the station's wiskiID
              ("d", "W3", None, "x"),                    # TelemetryID is a wiskiID
              ("e", "412", None, "x"),                   # leading zero lost
              ("f", "s-400", None, "x")])                # case and dash
    c = station_candidates(r, STATIONS)
    assert best(c, "a") == ("RLOIid", "S100") and best(c, "b") == ("notation", "S200")
    assert best(c, "c") == ("wiskiID", "S400") and best(c, "d") == ("wiskiID = TelemetryID", "0412")
    assert best(c, "e") == ("same after tidy-up", "0412") and best(c, "f") == ("same after tidy-up", "S400")


def test_a_similar_name_is_used_only_when_no_id_route_finds_anything():
    r = rows([("a", "NOPE", None, "Hexton RG"), ("b", "NOPE", None, "Quite Different Place"), ("c", "7001", None, "Hexton RG")])
    c = station_candidates(r, STATIONS)
    assert best(c, "a") == ("similar name", "S500") and best(c, "b") is None
    assert best(c, "c")[0] == "RLOIid"                                   # the ID route wins over the name
    s = candidate_summary(r, c).set_index("how")["rows"].to_dict()
    assert s == {"RLOIid": 1, "similar name": 1, "no candidate": 1}


def test_tidy_id_and_name_similarity():
    assert tidy_id(" 00-4_12 ") == "412" and tidy_id("e7300") == "E7300" and tidy_id(None) is None and tidy_id("000") == "0"
    assert name_similarity("Cwm Rheidol RG", "Cwm Rheidol Rainfall station") == 1.0     # filler words ignored
    assert name_similarity("Aldwyn Bridge", "Dunmere Ford") < 0.4


def amb(items):
    """items: (id, qualifier, period)."""
    return pd.DataFrame([{"id": i, "match_status": "ambiguous", "telemetry_id": "T", "telemetry_parameter": "Water Level",
                          "parameter_qualifier": None, "register_qualifier": q, "period": p} for i, q, p in items])


def test_ambiguity_is_classified_and_the_shortest_period_rule_is_tested():
    a = classify_ambiguity(amb([("a", "Stage", "900"), ("a", "Stage", "86400"),
                                ("b", "Stage", "900"), ("b", "Downstream Stage", "900"),
                                ("c", "Stage", "900"), ("c", "Stage", "900")])).set_index("id")
    assert a.loc["a", "why"] == "same qualifier, different period" and bool(a.loc["a", "settled_by_shortest_period"])
    assert a.loc["b", "why"] == "different qualifiers, same period" and not a.loc["b", "settled_by_shortest_period"]
    assert a.loc["c", "why"].startswith("identical") and not a.loc["c", "settled_by_shortest_period"]
    assert a.loc["a", "candidates"] == 2


def test_unmapped_measures_say_whether_the_station_is_in_the_file():
    reg = pd.DataFrame({"measure_uri": ["m1", "m2", "m3"], "station_reference": ["A", "A", "B"], "station_label": ["a", "a", "b"],
                        "parameter": ["level", "flow", "rainfall"], "qualifier": ["Stage", "Flow", "Gauge"], "period": ["900"] * 3})
    mr = pd.DataFrame({"telemetry_id": ["A"], "measure_uri": ["m1"], "match_status": ["matched"]})
    u = unmapped_measures(reg, mr).set_index("measure_uri")
    assert list(u.index) == ["m2", "m3"]
    assert u.loc["m2", "why"].startswith("station is in the file") and u.loc["m3", "why"] == "station is not in the file"
    assert u.loc["m3", "family"] == "rainfall"


def test_matched_rows_with_disagreeing_names_are_listed():
    mr = pd.DataFrame({"match_status": ["matched", "matched", "no_station"], "telemetry_id": ["1", "2", "3"],
                       "telemetry_parameter": ["x"] * 3, "fffs_location_name": ["Aldwyn Bridge", "Aldwyn Bridge", "Q"],
                       "station_label": ["Aldwyn Bridge", "Totally Elsewhere", None], "measure_uri": ["m1", "m2", None]})
    assert list(matched_name_check(mr)["telemetry_id"]) == ["2"]


def stage_rows(file_rows):
    """file_rows: (id, telemetry_id, qualifier, wiski_id) -> the output of a match where Stage and Downstream Stage both fit."""
    recs = []
    for rid, tid, q, w in file_rows:
        for rq in ("Stage", "Downstream Stage"):
            recs.append({"id": rid, "telemetry_id": tid, "match_status": "ambiguous", "telemetry_family": "level",
                         "telemetry_parameter": "Water Level", "parameter_qualifier": q, "register_qualifier": rq,
                         "period": "900", "wiski_id": w, "fffs_id": "F" + tid})
    return pd.DataFrame(recs)


def test_stage_and_downstream_stage_are_found_and_described():
    one = stage_pairs(stage_rows([("a", "T1", None, "W1")]))
    assert list(one["file_describes"]) == ["one_row_no_qualifier"] and one.loc[0, "wiski_ids"] == "W1"
    two = stage_pairs(stage_rows([("a", "T2", "Stage", "W2"), ("b", "T2", "Downstream Stage", "W2")]))
    assert list(two["file_describes"]) == ["distinct_qualifiers"] and two.loc[0, "wiski_ids"] == "W2"   # shared WISKI id
    assert two.loc[0, "file_qualifiers"] == "Downstream Stage | Stage"
    same = stage_pairs(stage_rows([("a", "T3", "Stage", "W3"), ("b", "T3", "Stage", "W4")]))
    assert list(same["file_describes"]) == ["same_qualifier"] and same.loc[0, "wiski_ids"] == "W3 | W4"


def test_other_ambiguities_are_not_stage_pairs():
    amb2 = pd.DataFrame([{"id": "a", "telemetry_id": "T", "match_status": "ambiguous", "telemetry_family": "level",
                          "telemetry_parameter": "x", "parameter_qualifier": None, "register_qualifier": q, "period": "900",
                          "wiski_id": "W", "fffs_id": "F"} for q in ("Stage", "Tidal Level")])
    assert stage_pairs(amb2).empty


def test_a_route_that_finds_only_stations_without_readings_is_separated_from_one_that_can_gain_matches():
    # S100 and S200 are in the register (they have readings); 0412 is listed by the stations API but is not (a Welsh site).
    r = rows([("a", "7001", None, "x"), ("b", "7002", None, "x"), ("c", "N-200", None, "x")])
    c = station_candidates(r, STATIONS, register_refs=["S100", "S200"])
    assert dict(zip(c["id"], c["candidate_in_register"])) == {"a": True, "b": True, "c": True}
    stations = STATIONS.copy()
    stations.loc[stations["stationReference"] == "S200", "RLOIid"] = "7009"
    c = station_candidates(rows([("a", "7001", None, "x"), ("w", "7009", None, "x")]), stations, register_refs=["S100"])
    s = candidate_summary(rows([("a", "7001", None, "x"), ("w", "7009", None, "x")]), c)
    got = {(h, rch): n for h, rch, n in zip(s["how"], s["reachable"], s["rows"])}
    assert got == {("RLOIid", "reachable"): 1, ("RLOIid", "listed, no readings in the feed"): 1}
    # Without the register the split is simply unknown, not wrong.
    c = station_candidates(rows([("a", "7001", None, "x")]), STATIONS)
    assert c["candidate_in_register"].isna().all()


def test_a_name_match_reaches_stations_whose_measures_differ_only_in_value_type():
    stations = pd.DataFrame({"stationReference": ["E71139", "E71124", "L3360"], "notation": ["E71139", "E71124", "L3360"],
                             "label": ["Immingham", "Immingham", "Immingham"], "RLOIid": [None] * 3, "wiskiID": [None] * 3})
    reg = pd.DataFrame({"measure_uri": ["m1", "m2", "m3", "m4"], "station_reference": ["E71139", "E71139", "E71124", "L3360"],
                        "parameter": ["level"] * 4, "qualifier": ["Tidal Level"] * 3 + ["Stage"],
                        "value_type": ["instantaneous", "mean", "instantaneous", "instantaneous"], "period": ["900"] * 4,
                        "unit_name": ["mAOD"] * 4})
    r = rows([("a", "5338N011W", None, "Immingham")])
    cands = station_candidates(r, stations, max_per_row=5, register_refs=reg["station_reference"])
    assert set(cands["how"]) == {"similar name"} and len(cands) == 3
    cm = candidate_measures(cands, reg)
    assert len(cm) == 4 and cm["tidal"].tolist().count(True) == 3
    ts = tidal_summary(cm).iloc[0]
    assert ts["stations"] == 3 and ts["measures"] == 4 and bool(ts["any_tidal"]) and ts["value_types"] == "instantaneous | mean"


def test_the_profile_of_unplaced_rows_shows_what_they_share():
    r = pd.DataFrame({"id": list("abcd"), "telemetry_id": ["067033_TG 148", "067624_TG 126", "5338N011W", None],
                      "parameter_id": ["P.obs", "P.obs", "H.obs", None], "telemetry_parameter": ["Rainfall", "Rainfall", "Water Level", None],
                      "fffs_location_name": ["Pont y Rhyd", "Aberdeen Road", "Leith", "Llanwrtyd"], "wiski_id": ["1", None, "3", None]})
    assert id_shape("067033_TG 148") == "999999_AA 999" and id_shape("5338N011W") == "9999A999A"
    p = profile_unplaced(r)
    assert p["shapes"].set_index("shape")["rows"].to_dict()["999999_AA 999"] == 2
    assert p["prefixes"].set_index("first_two_characters")["rows"]["06"] == 2
    h = p["hints"].set_index("measure")["count"]
    # Pont y Rhyd, Aberdeen Road and Llanwrtyd: Aberdeen is a false hint, which is why it is only a hint.
    assert h["rows"] == 4 and h["has a WISKI id"] == 2 and h["name has a Welsh place-name element (hint only)"] == 3
    assert welsh_hint("Pont y Rhyd") and welsh_hint("Llanwrtyd") and welsh_hint("Ty Mawr") and not welsh_hint("Leith") and not welsh_hint("")


def test_kept_rows_are_listed_with_whether_they_paired_to_a_real_time_measure():
    mapping = pd.DataFrame([
        {"id": "1", "fffs_id": "F1", "telemetry_id": "T1", "match_status": "matched", "measure_uri": "m/1", "station_reference": "S1",
         "station_label": "One", "n_readings": 900, "match_note": ""},
        {"id": "2", "fffs_id": "F2", "telemetry_id": "T2", "match_status": "no_station", "measure_uri": None, "station_reference": None,
         "station_label": None, "n_readings": None, "match_note": "no measure in the register has this station reference"},
        {"id": "3", "fffs_id": "F3", "telemetry_id": "T3", "match_status": "ambiguous", "measure_uri": "m/3a", "station_reference": "S3",
         "station_label": "Three", "n_readings": 5, "match_note": "2 level measures fit"},
        {"id": "3", "fffs_id": "F3", "telemetry_id": "T3", "match_status": "ambiguous", "measure_uri": "m/3b", "station_reference": "S3",
         "station_label": "Three", "n_readings": 6, "match_note": "2 level measures fit"},
        {"id": "4", "fffs_id": "F4", "telemetry_id": "5547N20W", "match_status": "matched", "measure_uri": "m/4", "station_reference": "S4",
         "station_label": "Four", "n_readings": 7, "match_note": ""}])
    scope = pd.DataFrame({"FFFS_ID": ["F1", "F2", "F3", "F9", "FX", "F5"], "Parameter": ["H.obs"] * 6,
                          "ExternalID": ["T1", "T2", "T3", "T9", "5547N20W", "T5"], "ExternalParameter": ["Water Level"] * 6,
                          "ImportSource": ["Telemetry"] * 6, "Keep": ["TRUE", "true", "TRUE", "TRUE", "TRUE", "FALSE"]})
    out = keep_true_status(scope, mapping).set_index("FFFS_ID")
    assert list(out.index) == ["F1", "F2", "F3", "F9", "FX"]                      # F5 is Keep = FALSE: not listed
    assert out.loc["F1", "paired"] and out.loc["F1", "measure_uri"] == "m/1" and out.loc["F1", "n_readings"] == 900
    assert not out.loc["F2", "paired"] and out.loc["F2", "in_mapping"] and out.loc["F2", "match_status"] == "no_station"
    assert not out.loc["F3", "paired"] and out.loc["F3", "match_status"] == "ambiguous"
    assert not out.loc["F9", "in_mapping"] and out.loc["F9", "match_status"] == "not in the mapping file"
    assert out.loc["FX", "paired"] and out.loc["FX", "measure_uri"] == "m/4"        # found through the ExternalID as a TelemetryID
    try:
        keep_true_status(pd.DataFrame({"FFFS_ID": ["a"]}), mapping)
        assert False
    except ValueError as e:
        assert "FFFS_ID and Keep" in str(e)


def test_a_kept_row_the_file_cannot_place_is_tried_on_its_external_id():
    reg = pd.DataFrame({"measure_uri": ["m/2230TH-stage", "m/2453TH-stage"], "station_reference": ["2230TH", "2453TH"],
                        "station_label": ["Knighton Main", "Other"], "parameter": ["level", "level"], "qualifier": ["Stage", "Stage"],
                        "period": ["900", "900"], "unit_name": ["mASD", "mASD"]})
    scope = pd.DataFrame({"FFFS_ID": ["2230_w1TH", "2453TH", "ZZ1", "OK1"], "Parameter": ["H.obs"] * 4,
                          "ExternalID": ["2230TH", "2453TH", "E9999", "T1"], "ExternalParameter": ["Water Level"] * 4,
                          "ImportSource": ["Telemetry"] * 4, "Keep": ["TRUE"] * 4})
    mapping = pd.DataFrame([
        {"id": "1", "fffs_id": "2230_w1TH", "telemetry_id": "2230_w1TH", "match_status": "no_station", "measure_uri": None,
         "station_reference": None, "station_label": None, "n_readings": None, "match_note": "x"},
        {"id": "2", "fffs_id": "OK1", "telemetry_id": "T1", "match_status": "matched", "measure_uri": "m/t1",
         "station_reference": "T1", "station_label": "T", "n_readings": 50, "match_note": ""}])
    kept = keep_true_status(scope, mapping)          # 2453TH and ZZ1 are not in the mapping file at all
    activity = pd.DataFrame({"measure_uri": ["m/2453TH-stage"], "n_readings": [10], "first_reading": ["2026-01-01"],
                             "last_reading": ["2026-10-22"]})
    out = pair_by_external_id(kept, reg, activity, now=pd.Timestamp("2026-10-23")).set_index("FFFS_ID")
    assert out.loc["2230_w1TH", "paired"] and out.loc["2230_w1TH", "paired_via"] == "ExternalID"
    assert out.loc["2230_w1TH", "external_measure_uri"] == "m/2230TH-stage" and out.loc["2230_w1TH", "data_state"] == "matched, no readings"
    assert out.loc["2453TH", "paired_via"] == "ExternalID" and out.loc["2453TH", "data_state"] == "delivers data"
    assert not out.loc["ZZ1", "paired"] and out.loc["ZZ1", "external_status"] == "no_station" and out.loc["ZZ1", "data_state"] == "not paired"
    assert out.loc["OK1", "paired_via"] == "mapping file" and out.loc["OK1", "data_state"] == "delivers data"


def test_unpaired_kept_rows_are_profiled_by_id_shape_source_and_unknown_parameter():
    kept = pd.DataFrame({
        "FFFS_ID": ["a", "b", "c", "d", "e"], "Parameter": ["H.obs", "H.obs", "H.obs", "X.obs", "X.obs"],
        "ExternalID": ["E14521", "E8980", "2230TH", "77", "78"], "ExternalParameter": ["Water Level"] * 3 + ["Mystery", "Mystery"],
        "ImportSource": ["Telemetry"] * 5, "in_mapping": [True, False, True, True, True], "paired": [False] * 5,
        "match_status": ["no_station", "not in the mapping file", "no_station", "unknown_parameter", "unknown_parameter"],
        "location_name": ["Llyn Brenig", None, "Knighton Main", None, None]})
    prof = profile_unpaired_kept(kept)
    shapes = prof["shapes"].set_index("shape")
    assert shapes.loc["A99999", "rows"] == 1 and shapes.loc["A9999", "in_mapping_file"] == 0
    assert shapes.loc["9999AA", "rows"] == 1 and shapes.loc["9999AA", "in_mapping_file"] == 1
    assert prof["prefixes"].set_index("first_two_characters").loc["E1", "rows"] == 1
    assert prof["shapes"]["rows"].sum() == 3                                   # unknown_parameter rows are not here
    assert prof["shapes"]["welsh_name_hint"].sum() == 1
    assert list(prof["unknown_parameter"]["rows"]) == [2]
    assert prof["sources"].iloc[0]["rows"] == 3


def test_numeric_ids_are_checked_against_the_register_in_each_form():
    reg = pd.DataFrame({"station_reference": ["022006", "2001", "E31999", "0044500", "56000A"]})
    kept = pd.DataFrame({"ExternalID": ["22006", "2001", "31999", "44500", "56000", "99999", "E1"], "paired": [False] * 7,
                         "match_status": ["no_station"] * 7})
    out = numeric_id_variants(kept, reg).set_index("external_id")
    assert "leading-zero rule" in out.loc["22006", "verdict"]
    assert "as written" in out.loc["2001", "verdict"]
    assert out.loc["31999", "ends_with"] == "E31999" and "different rule" in out.loc["31999", "verdict"]
    assert out.loc["44500", "found_zero_padded_7_8"] == "0044500"
    assert out.loc["56000", "starts_with"] == "56000A"
    assert out.loc["99999", "verdict"] == "nothing like it in the register"
    assert "E1" not in out.index                                   # not digits only


def test_unpaired_ids_are_checked_against_the_register_and_the_station_list():
    reg = pd.DataFrame({"station_reference": ["A1"]})
    stations = pd.DataFrame({"stationReference": ["S9", "A1", "S77"], "notation": ["S9", "A1", "S77"], "RLOIid": ["1234", None, "5678"],
                             "wiskiID": [None, None, "W-1"], "label": ["Nine", "A one", "Seventy"]})
    kept = pd.DataFrame({"ExternalID": ["A1", "S9", "1234", "W-1", "ZZ9", "074"], "paired": [False] * 6, "match_status": ["no_station"] * 6})
    out = listed_station_check(kept, reg, stations).set_index("external_id")
    assert "something else stopped" in out.loc["A1", "verdict"]
    assert out.loc["S9", "verdict"] == "station listed, no measures in the register" and out.loc["S9", "station_label"] == "Nine"
    assert out.loc["1234", "listed_by"] == "RLOIid" and out.loc["1234", "station_reference"] == "S9"
    assert out.loc["W-1", "listed_by"] == "wiskiID" and out.loc["W-1", "station_reference"] == "S77"
    assert out.loc["ZZ9", "verdict"] == "not in the station list either"
    assert out.loc["074", "verdict"] == "not in the station list either"


def test_a_kept_row_is_found_in_the_mapping_when_only_leading_zeros_differ():
    mapping = pd.DataFrame([{"id": "1", "fffs_id": "083280", "telemetry_id": "083280", "match_status": "matched", "measure_uri": "m/1",
                             "station_reference": "083280", "station_label": "Rain", "n_readings": 5, "match_note": ""}])
    scope = pd.DataFrame({"FFFS_ID": ["83280"], "Parameter": ["P.obs"], "ExternalID": ["83280"], "ExternalParameter": ["Rainfall"],
                          "ImportSource": ["Telemetry"], "Keep": ["TRUE"]})
    out = keep_true_status(scope, mapping)
    assert bool(out.iloc[0]["in_mapping"]) and bool(out.iloc[0]["paired"]) and out.iloc[0]["measure_uri"] == "m/1"


def test_ambiguity_says_when_candidates_differ_by_unit_or_value_type():
    base = {"match_status": "ambiguous", "telemetry_id": "075497", "telemetry_parameter": "Rainfall", "parameter_qualifier": None,
            "register_qualifier": "Tipping Bucket Raingauge", "period": "900"}
    rows = pd.DataFrame([{**base, "id": "r", "unit_name": "mm", "register_value_type": "instantaneous"},
                         {**base, "id": "r", "unit_name": "mm", "register_value_type": "total"},
                         {**base, "id": "t", "unit_name": "m", "register_value_type": "mean"},
                         {**base, "id": "t", "unit_name": "mAOD", "register_value_type": "mean"}])
    a = classify_ambiguity(rows).set_index("id")
    assert a.loc["r", "why"] == "same qualifier and period; differ by value type" and a.loc["r", "value_types"] == "instantaneous | total"
    assert a.loc["t", "why"] == "same qualifier and period; differ by unit (datum)" and a.loc["t", "units"] == "m | maod"


def test_an_ambiguous_kept_row_lists_its_candidates_and_what_differs_instead_of_showing_one_as_paired():
    base = {"id": "1", "fffs_id": "UKCFF_WORK", "telemetry_id": "E73639", "match_status": "ambiguous", "station_reference": "E73639",
            "station_label": "Workington", "n_readings": 5, "match_note": "2 tidal level measures fit", "register_qualifier": "Tidal Level",
            "period": "900", "register_value_type": "mean"}
    mapping = pd.DataFrame([{**base, "measure_uri": "http://x/measures/E73639-anglian-level-tidal_level-Mean-15_min-mAOD", "unit_name": "mAOD"},
                            {**base, "measure_uri": "http://x/measures/E73639-level-tidal_level-Mean-15_min-m", "unit_name": "m"}])
    scope = pd.DataFrame({"FFFS_ID": ["UKCFF_WORK"], "Parameter": ["H.obs"], "ExternalID": ["E73639"], "ExternalParameter": ["Water Level"],
                          "ImportSource": ["UKCFF Tide Gauge"], "Keep": ["TRUE"]})
    out = keep_true_status(scope, mapping).iloc[0]
    assert not out["paired"] and out["match_status"] == "ambiguous" and out["measure_uri"] is None
    assert out["n_candidates"] == 2 and "Mean-15_min-mAOD" in out["candidate_measures"] and "Mean-15_min-m" in out["candidate_measures"]
    assert out["ambiguity_why"] == "differ by unit (datum)"


def test_the_gap_list_gives_each_unpaired_row_a_verdict_and_what_to_do():
    kept = pd.DataFrame({"FFFS_ID": ["a", "b", "c", "d", "e"], "Parameter": ["H.obs"] * 5, "ExternalID": ["S9", "ZZ9", "S5", "Q1", "Q2"],
                         "ExternalParameter": ["Water Level"] * 5, "ImportSource": ["Telemetry"] * 5, "paired": [False] * 5,
                         "match_status": ["no_station", "no_station", "ambiguous", "no_parameter", "matched"],
                         "station_reference": [None, None, "S5", "Q1", "Q2"], "station_label": [None, None, "Five", "Q", "Q"],
                         "ambiguity_why": [None, None, "differ by unit (datum)", None, None], "match_note": ["x"] * 5})
    lc = pd.DataFrame({"external_id": ["S9", "ZZ9"],
                       "verdict": ["station listed, no measures in the register", "not in the station list either"],
                       "listed_by": ["stationReference", None], "station_reference": ["S9", None], "station_label": ["Nine", None]})
    out = gap_list(kept, lc).set_index("FFFS_ID")
    assert out.loc["a", "verdict"] == "station listed, no measures in the register" and "Ask the feed owner" in out.loc["a", "what_to_do"]
    assert "outside the feed" in out.loc["b", "what_to_do"]
    assert out.loc["c", "verdict"] == "ambiguous: differ by unit (datum)"
    assert out.loc["d", "verdict"].startswith("no_parameter")
