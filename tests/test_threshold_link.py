import pandas as pd

from utils.threshold_link import (link_thresholds, link_summary, prepare_aliases, prepare_thresholds, resolve_columns, unit_key)


def raw_file():
    return pd.DataFrame({
        "ID": ["a1", "a2", "a3", "a4", "a5", "a6", "a7"],
        "Location": ["0130TH", "0130TH", "0144TH", "0155TH", "0184TH", "9999XX", "0130TH"],
        "ThresholdType": ["Observed", "Forecast", "Observed", "Observed", "Observed", "Observed", "Observed"],
        "ThresholdName": ["Water Level"] * 7,
        "Value": [0.99, 0.99, 1.6, 1.58, 1.7, 2.0, 1.94],
        "Unit": ["mALD", "mALD", "mAOD", "mALD", "m3/s", "mALD", "mALD"],
        "Category": ["FW", "FW", "FW", "FW", "FW", "FW", "FC"],
        "CatDesc": ["Flood Warning", "Flood Warning", "Flood Warning", "Flood Warning", "Flood Warning", "Flood Warning", "Forecaster Activity"],
        "Type": ["RES", "RES", "RES", "ACTCON", "RES", "RES", "ACT"],
        "Purpose": ["FAL", "FAL", "FW", "FW", "FW", "FW", "EDF"],
        "Parameter": ["H.obs", "H.rated.fc", "H.obs", "H.obs.us", "Q.obs", "H.obs", "H.obs"],
        "TargetLeadTimeMinutes": [180, 180, 180, 30, 90, 60, 180],
    })


def mapping():
    return pd.DataFrame({
        "fffs_id": ["0130TH", "0144TH", "0144TH", "155TH", "155TH", "0184TH"],
        "telemetry_family": ["level", "level", "level", "level", "level", "level"],
        "code_family": ["level"] * 6,
        "measure_uri": ["u1", "u2", "u3", "u4", None, "u6"],
        "unit_name": ["mALD", "mASD", "mAOD", "mALD", "mALD", "mASD"],
        "register_value_type": ["instantaneous", "instantaneous", "mean", "instantaneous", "instantaneous", "instantaneous"],
        "n_readings": [100, 50, 400, 10, 0, 5],
    })


def test_only_observed_flood_warning_rows_are_kept():
    thr, rep = prepare_thresholds(raw_file())
    assert set(thr["threshold_id"]) == {"a1", "a3", "a4", "a5", "a6"}    # not the forecast row, not the forecaster-activity row
    assert rep["columns"]["basis"] == "ThresholdType"                    # found by its values, not its name
    assert rep["rows"] == 7 and rep["rows_kept"] == 5
    assert rep["units"]["mALD"] == 3 and rep["parameters"]["H.obs"] == 3
    assert set(thr["type"]) == {"RES", "ACTCON"}                          # every type inside Flood Warning is kept
    assert thr.set_index("threshold_id").loc["a1", "family"] == "level"


def test_column_names_can_be_given():
    raw = raw_file().rename(columns={"ThresholdType": "Basis"})
    thr, rep = prepare_thresholds(raw, {"basis": "Basis"})
    assert len(thr) == 5
    try:
        resolve_columns(raw_file().drop(columns=["Unit"]))
        assert False
    except ValueError as e:
        assert "unit" in str(e)


def test_link_needs_gauge_family_and_unit():
    thr, _ = prepare_thresholds(raw_file())
    out = link_thresholds(thr, mapping()).set_index("threshold_id")
    assert out.loc["a1", "link_status"] == "linked" and out.loc["a1", "measure_uri"] == "u1"
    assert out.loc["a3", "link_status"] == "linked" and out.loc["a3", "measure_uri"] == "u3"   # mAOD picks the mAOD measure, not mASD
    assert out.loc["a4", "link_status"] == "linked" and out.loc["a4", "measure_uri"] == "u4"   # 0155TH and 155TH are one gauge; the no-uri line is ignored
    assert out.loc["a5", "link_status"] == "no_measure"                                        # flow threshold, gauge only has level
    assert out.loc["a6", "link_status"] == "no_gauge"


def test_a_unit_that_disagrees_is_not_linked_and_says_why():
    thr, _ = prepare_thresholds(raw_file())
    mp = mapping()
    mp.loc[mp["fffs_id"] == "0130TH", "unit_name"] = "mBDAT"
    out = link_thresholds(thr, mp).set_index("threshold_id")
    assert out.loc["a1", "link_status"] == "unit_mismatch" and pd.isna(out.loc["a1", "measure_uri"])
    assert "mBDAT" in out.loc["a1", "link_note"] and "mALD" in out.loc["a1", "link_note"]
    # an alias fills the gap
    al = prepare_aliases(pd.DataFrame({"threshold_unit": ["mALD"], "measure_unit": ["mBDAT"]}))
    out2 = link_thresholds(thr, mp, al).set_index("threshold_id")
    assert out2.loc["a1", "link_status"] == "linked" and out2.loc["a1", "measure_uri"] == "u1"
    assert link_summary(out.reset_index())["by_status"]["unit_mismatch"] == 1


def test_several_agreeing_measures_choose_instantaneous_then_most_readings():
    thr, _ = prepare_thresholds(raw_file())
    mp = pd.concat([mapping(), pd.DataFrame({"fffs_id": ["0130TH", "0130TH"], "telemetry_family": ["level", "level"], "code_family": ["level", "level"],
                                             "measure_uri": ["m_mean", "m_inst_big"], "unit_name": ["mALD", "mALD"],
                                             "register_value_type": ["mean", "instantaneous"], "n_readings": [9999, 500]})], ignore_index=True)
    out = link_thresholds(thr, mp).set_index("threshold_id")
    assert out.loc["a1", "link_status"] == "linked_chosen" and out.loc["a1", "measure_uri"] == "m_inst_big" and out.loc["a1", "n_candidates"] == 3


def test_unit_key_ignores_case_and_spacing():
    assert unit_key(" m ALD ") == "mald" and unit_key(None) is None and unit_key("nan") is None


def test_a_bare_m_is_taken_as_mald_only_when_no_measure_is_exactly_mald():
    thr, _ = prepare_thresholds(raw_file())
    mp = mapping()
    mp.loc[mp["fffs_id"] == "0130TH", "unit_name"] = "m"
    out = link_thresholds(thr, mp).set_index("threshold_id")
    assert out.loc["a1", "link_status"] == "linked" and out.loc["a1", "measure_uri"] == "u1" and out.loc["a1", "unit_match"] == "alias"
    assert "taken as" in out.loc["a1", "link_note"]
    both = pd.concat([mp, pd.DataFrame({"fffs_id": ["0130TH"], "telemetry_family": ["level"], "code_family": ["level"], "measure_uri": ["u_exact"],
                                        "unit_name": ["mALD"], "register_value_type": ["instantaneous"], "n_readings": [1]})], ignore_index=True)
    out2 = link_thresholds(thr, both).set_index("threshold_id")
    assert out2.loc["a1", "measure_uri"] == "u_exact" and out2.loc["a1", "unit_match"] == "exact"     # exact beats the alias
    assert link_thresholds(thr, mapping()).set_index("threshold_id").loc["a1", "unit_match"] == "exact"
    mp2 = mapping(); mp2.loc[mp2["fffs_id"] == "0130TH", "unit_name"] = "mBDAT"
    assert link_thresholds(thr, mp2).set_index("threshold_id").loc["a1", "link_status"] == "unit_mismatch"   # other datums are still not guessed


def test_a_bare_m_also_stands_for_masd_and_maod_but_only_as_a_fallback():
    raw = raw_file(); raw.loc[raw["ID"] == "a1", "Unit"] = "mASD"
    thr, _ = prepare_thresholds(raw)
    mp = mapping(); mp.loc[mp["fffs_id"] == "0130TH", "unit_name"] = "m"
    out = link_thresholds(thr, mp).set_index("threshold_id")
    assert out.loc["a1", "link_status"] == "linked" and out.loc["a1", "unit_match"] == "alias"
    mp2 = mapping(); mp2.loc[mp2["fffs_id"] == "0144TH", "unit_name"] = "m"          # a3 is mAOD: if mAOD does not match, try m
    o2 = link_thresholds(thr, mp2).set_index("threshold_id")
    assert o2.loc["a3", "link_status"] in ("linked", "linked_chosen") and o2.loc["a3", "unit_match"] == "alias" and o2.loc["a3", "measure_uri"] in ("u2", "u3")
    mp3 = mapping(); mp3.loc[mp3["fffs_id"] == "0144TH", "unit_name"] = ["m", "mAOD"]   # an exact mAOD measure beats the bare m
    o3 = link_thresholds(thr, mp3).set_index("threshold_id")
    assert o3.loc["a3", "measure_uri"] == "u3" and o3.loc["a3", "unit_match"] == "exact"


def test_mald_and_masd_are_one_datum_but_ranked_below_exact_and_above_bare_m():
    thr, _ = prepare_thresholds(raw_file())
    mp = mapping(); mp.loc[mp["fffs_id"] == "0130TH", "unit_name"] = "mASD"
    out = link_thresholds(thr, mp).set_index("threshold_id")
    assert out.loc["a1", "link_status"] == "linked" and out.loc["a1", "unit_match"] == "datum_assumed" and "taken as" in out.loc["a1", "link_note"]
    extra = lambda uri, unit: pd.DataFrame({"fffs_id": ["0130TH"], "telemetry_family": ["level"], "code_family": ["level"], "measure_uri": [uri],   # noqa: E731
                                            "unit_name": [unit], "register_value_type": ["instantaneous"], "n_readings": [1]})
    with_m = link_thresholds(thr, pd.concat([mp, extra("u_m", "m")], ignore_index=True)).set_index("threshold_id")
    assert with_m.loc["a1", "measure_uri"] == "u1" and with_m.loc["a1", "unit_match"] == "datum_assumed"          # beats the bare m
    with_exact = link_thresholds(thr, pd.concat([mp, extra("u_ex", "mALD")], ignore_index=True)).set_index("threshold_id")
    assert with_exact.loc["a1", "measure_uri"] == "u_ex" and with_exact.loc["a1", "unit_match"] == "exact"        # exact beats both
    maod = mapping(); maod.loc[maod["fffs_id"] == "0130TH", "unit_name"] = "mAOD"
    assert link_thresholds(thr, maod).set_index("threshold_id").loc["a1", "link_status"] == "unit_mismatch"       # mAOD is never taken for ALD
