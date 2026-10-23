# Tests for matching tide gauges by the position their TelemetryID carries. Run with:  pytest tests
# Needs pandas and numpy only.
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.tidal_position import (  # noqa: E402
    apply_position_matches, decode_gauge_code, haversine_km, position_candidates,
)


def near(readings, lat, lon, km=3.0):
    return any(float(haversine_km(a, b, lat, lon)) <= km for a, b in readings)


def test_the_codes_in_the_photo_decode_to_the_places_they_name():
    assert near(decode_gauge_code("5338N011W"), 53.63, -0.18)       # Immingham
    assert near(decode_gauge_code("5559N310W"), 55.98, -3.17)       # Leith
    assert near(decode_gauge_code("5341N042W"), 53.68, -0.70)       # Blacktoft
    assert near(decode_gauge_code("551N126W"), 55.02, -1.43)        # North Shields: minutes not padded (55 deg 1 min)
    assert near(decode_gauge_code("5438N19W"), 54.63, -1.15)        # Tees Dock: 1 deg 9 min, so "19" is not 0 deg 19 min
    assert decode_gauge_code("5338N011W")[0][0] > 53.6              # north is positive, west negative
    assert all(lon < 0 for _, lon in decode_gauge_code("5338N011W"))


def test_anything_that_is_not_a_position_code_is_left_alone():
    for v in ("067033_TG 148", "SU74_73", "E71139", "ZZ", "", None, "9999N999W", "5338N011X"):
        assert decode_gauge_code(v) == [], v


STATIONS = pd.DataFrame({
    "stationReference": ["E71139", "E71124", "L3360", "E70824", "FAR"],
    "label": ["Immingham", "Immingham Dock", "Immingham", "Leith", "Immingham"],
    "lat": ["53.630", "53.631", "53.640", "55.980", "52.000"], "long": ["-0.185", "-0.190", "-0.180", "-3.170", "-1.000"]})
ROWS = pd.DataFrame({"id": ["a", "b", "c"], "telemetry_id": ["5338N011W", "5559N310W", "5559N310W"],
                     "fffs_location_name": ["Immingham", "Leith", "Somewhere Else"]})


def test_candidates_are_stations_near_the_position_with_the_evidence_for_each():
    c = position_candidates(ROWS, STATIONS)
    a = c[c["id"] == "a"]
    assert set(a["candidate_station_reference"]) == {"E71139", "E71124", "L3360"}        # the far station is out
    assert (a["distance_km"] <= 3.0).all() and list(a["distance_km"]) == sorted(a["distance_km"])
    assert set(a["evidence"]) == {"name and position"}
    b = c[c["id"] == "b"].iloc[0]
    assert b["candidate_station_reference"] == "E70824" and b["evidence"] == "name and position"
    assert c[c["id"] == "c"].iloc[0]["evidence"] == "position only"                       # a position with the wrong name
    assert position_candidates(ROWS.iloc[0:0], STATIONS).empty


def register(rows):
    return pd.DataFrame(rows, columns=["measure_uri", "station_reference", "station_label", "parameter", "qualifier", "value_type", "period", "unit_name"])


def mapping_rows(ids_names):
    return pd.DataFrame([{"id": i, "telemetry_id": t, "fffs_location_name": n, "match_status": "no_station", "match_note": "no measure in the register has this station reference",
                          "measure_uri": None, "station_reference": None, "station_label": None, "register_parameter": None,
                          "register_qualifier": None, "period": None, "unit_name": None, "register_value_type": None} for i, t, n in ids_names])


def test_a_match_needs_name_and_position_and_takes_only_tidal_level_measures():
    reg = register([("m1", "E71139", "Immingham", "level", "Tidal Level", "instantaneous", "900", "mAOD"),
                    ("m2", "E71139", "Immingham", "level", "Tidal Level", "mean", "900", "mAOD"),
                    ("m3", "L3360", "Immingham", "level", "Stage", "instantaneous", "900", "m"),
                    ("m4", "E70824", "Leith", "level", "Tidal Level", "instantaneous", "900", "mAOD")])
    rows = mapping_rows([("a", "5338N011W", "Immingham"), ("b", "5559N310W", "Leith"), ("c", "5559N310W", "Somewhere Else")])
    out = apply_position_matches(rows, position_candidates(rows, STATIONS), reg).set_index("id")
    assert out.loc["a", "match_status"] == "matched" and out.loc["a", "measure_uri"] == "m1"     # instantaneous preferred, tidal only
    assert "name and position" in out.loc["a", "match_note"] and "km" in out.loc["a", "match_note"]
    assert out.loc["b", "match_status"] == "matched" and out.loc["b", "measure_uri"] == "m4"
    assert out.loc["c", "match_status"] == "no_station"                                          # position alone is not enough


def test_several_tidal_measures_with_no_value_type_stay_ambiguous_for_the_readings_to_settle():
    reg = register([("m1", "E71139", "Immingham", "level", "Tidal Level", None, "900", "mAOD"),
                    ("m2", "E71124", "Immingham", "level", "Tidal Level", None, "900", "mAOD")])
    rows = mapping_rows([("a", "5338N011W", "Immingham")])
    out = apply_position_matches(rows, position_candidates(rows, STATIONS), reg)
    assert list(out["match_status"]) == ["ambiguous", "ambiguous"] and set(out["measure_uri"]) == {"m1", "m2"}
    assert apply_position_matches(rows, position_candidates(rows, STATIONS), reg.iloc[0:0]).equals(rows)   # no tidal measure: untouched


def test_only_instantaneous_measures_are_taken_when_the_register_gives_the_type():
    rows = mapping_rows([("a", "5338N011W", "Immingham")])
    only_mean = register([("m2", "E71139", "Immingham", "level", "Tidal Level", "mean", "900", "mAOD"),
                          ("m3", "E71124", "Immingham", "level", "Tidal Level", "max", "900", "mAOD")])
    out = apply_position_matches(rows, position_candidates(rows, STATIONS), only_mean)
    assert list(out["match_status"]) == ["no_station"]                      # no instantaneous tidal measure: not matched to a mean
    mixed = register([("m1", "E71139", "Immingham", "level", "Tidal Level", "instantaneous", "900", "mAOD"),
                      ("m2", "E71139", "Immingham", "level", "Tidal Level", "mean", "900", "mAOD")])
    out = apply_position_matches(rows, position_candidates(rows, STATIONS), mixed)
    assert list(out["measure_uri"]) == ["m1"] and list(out["match_status"]) == ["matched"]


def test_one_gauge_listed_as_two_stations_in_two_datums_is_matched_in_each():
    rows = mapping_rows([("a", "5338N011W", "Immingham")])
    reg = register([("m1", "E71139", "Immingham", "level", "Tidal Level", "instantaneous", "900", "mAOD"),
                    ("m2", "E71124", "Immingham", "level", "Tidal Level", "instantaneous", "900", "m")])
    out = apply_position_matches(rows, position_candidates(rows, STATIONS), reg)
    assert list(out["match_status"]) == ["matched", "matched"] and set(out["unit_name"]) == {"mAOD", "m"}
    assert "differ only by datum" in out.iloc[0]["match_note"] and set(out["register_value_type"]) == {"instantaneous"}
    # A different period is a real ambiguity.
    reg2 = register([("m1", "E71139", "Immingham", "level", "Tidal Level", "instantaneous", "900", "mAOD"),
                     ("m2", "E71124", "Immingham", "level", "Tidal Level", "instantaneous", "300", "m")])
    assert set(apply_position_matches(rows, position_candidates(rows, STATIONS), reg2)["match_status"]) == {"ambiguous"}
