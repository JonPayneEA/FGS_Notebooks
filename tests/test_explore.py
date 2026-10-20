# Tests for utils/explore.py. Pure pandas, no Spark or Databricks.  Run:  pytest tests
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.explore import (  # noqa: E402
    ALL, archive_live_boundary, filter_register, options, prepare_register, summarise, window_start,
)


def register():
    return pd.DataFrame({
        "measure_uri":   ["m1", "m2", "m3", "m4"],
        "station_label": ["Bewdley", None, "Hereford", ""],
        "label":         ["BEWDLEY", "CHESTER WEIR", "HEREFORD", "TRUNC"],
        "station_reference": ["r1", "r2", "r3", "r4"],
        "river_name":    ["River Severn", "River Dee", "River Wye", "River Wye"],
        "ea_area_name":  ["West Midlands", "Cheshire", "West Midlands", None],
        "parameter":     ["level", "level", "flow", "rainfall"],
        "qualifier":     ["Stage", "Stage", None, "Tipping Bucket"],
        "unit_name":     ["mASD", "mAOD", "m3/s", "mm"],
        "period":        [900, 900, 900, 900],
    })


def test_station_name_prefers_the_api_label_then_the_archive_label():
    r = prepare_register(register())
    assert list(r["station"]) == ["Bewdley", "CHESTER WEIR", "Hereford", "TRUNC"]   # None and "" fall back


def test_measure_name_says_what_the_measure_is():
    r = prepare_register(register())
    assert list(r["measure_name"]) == ["level, Stage (mASD)", "level, Stage (mAOD)", "flow (m3/s)", "rainfall, Tipping Bucket (mm)"]


def test_filter_register_applies_every_choice_but_all():
    r = prepare_register(register())
    assert len(filter_register(r)) == 4
    assert list(filter_register(r, area="West Midlands")["measure_uri"]) == ["m1", "m3"]
    assert list(filter_register(r, area="West Midlands", parameter="flow")["measure_uri"]) == ["m3"]
    assert list(filter_register(r, station="CHESTER WEIR")["measure_uri"]) == ["m2"]
    assert filter_register(r, area="Cheshire", parameter="flow").empty


def test_options_are_narrowed_by_the_other_choices_so_nothing_dead_ends():
    r = prepare_register(register())
    o = options(r, area="West Midlands")
    assert o["areas"][0] == ALL and "Cheshire" in o["areas"]            # an area list ignores the area choice itself
    assert o["rivers"] == [ALL, "River Severn", "River Wye"]            # only rivers in that area
    assert o["parameters"] == [ALL, "flow", "level"]
    assert o["stations"] == [ALL, "Bewdley", "Hereford"]
    o2 = options(r, river="River Wye", parameter="flow")
    assert o2["areas"] == [ALL, "West Midlands"]                        # the missing area is simply not offered
    assert o2["stations"] == [ALL, "Hereford"]


def test_boundary_is_midnight_at_the_end_of_the_last_archived_day():
    b = archive_live_boundary("2026-10-18")
    assert b == pd.Timestamp("2026-10-19 00:00", tz="UTC")
    assert archive_live_boundary(pd.Timestamp("2026-10-18 13:45")) == pd.Timestamp("2026-10-19", tz="UTC")
    assert archive_live_boundary(None) is None
    assert archive_live_boundary(pd.NaT) is None


def test_window_start_counts_back_whole_days_in_utc():
    assert window_start(pd.Timestamp("2026-10-19 12:00", tz="UTC"), 3) == pd.Timestamp("2026-10-16 12:00", tz="UTC")
    assert window_start(pd.Timestamp("2026-10-19 12:00"), 1) == pd.Timestamp("2026-10-18 12:00", tz="UTC")


def test_summary_splits_archive_from_live_at_the_boundary():
    now = pd.Timestamp("2026-10-19 10:00", tz="UTC")
    boundary = archive_live_boundary("2026-10-18")
    df = pd.DataFrame({"date_time": ["2026-10-18 23:45", "2026-10-19 00:00", "2026-10-19 09:45"]})
    s = summarise(df, boundary, now)
    assert (s["readings"], s["archive"], s["live"]) == (3, 1, 2)       # the boundary instant itself is live
    assert s["newest"] == pd.Timestamp("2026-10-19 09:45", tz="UTC") and s["age_minutes"] == 15.0


def test_summary_copes_with_nothing_and_with_no_boundary():
    now = pd.Timestamp("2026-10-19 10:00", tz="UTC")
    assert summarise(pd.DataFrame({"date_time": []}), None, now)["readings"] == 0
    s = summarise(pd.DataFrame({"date_time": ["2026-10-19 09:00"]}), None, now)
    assert s["live"] == 0 and s["archive"] == 1


def test_generic_station_names_are_made_unique():
    reg = pd.DataFrame({
        "measure_uri": [f"http://x/measures/{i}-rainfall" for i in range(3)],
        "station_uri": [f"http://x/stations/{i}" for i in range(3)],
        "station_reference": ["A1", "B2", "C3"],
        "station_label": ["Rainfall station"] * 3,
        "parameter": ["rainfall"] * 3, "qualifier": ["Tipping Bucket Raingauge"] * 3,
        "unit_name": ["mm"] * 3,
    })
    out = prepare_register(reg)
    assert out["station"].is_unique
    assert out["measure_label"].is_unique
    assert all("Rainfall station" in s for s in out["station"])


def test_unique_names_are_left_alone():
    reg = pd.DataFrame({
        "measure_uri": ["m1", "m2"], "station_uri": ["s1", "s2"],
        "station_label": ["Bewdley", "Hereford"], "parameter": ["level", "level"],
    })
    assert list(prepare_register(reg)["station"]) == ["Bewdley", "Hereford"]
