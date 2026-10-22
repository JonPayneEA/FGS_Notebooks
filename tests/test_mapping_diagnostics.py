# Tests for the telemetry mapping diagnostics. Run with:  pytest tests
# Needs pandas only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.mapping_diagnostics import (  # noqa: E402
    candidate_summary, classify_ambiguity, matched_name_check, name_similarity, station_candidates, tidy_id,
    unmapped_measures,
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
