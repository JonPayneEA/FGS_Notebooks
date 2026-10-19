# Tests for the pure parsing helpers. Run locally with:  pytest tests
# Needs pandas and pyspark installed (Row/types only; no Spark session).
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.helpers import (  # noqa: E402
    IMPACT_LABELS, LIKELIHOOD_LABELS, build_geojson_geometry,
    parse_risk_rows, parse_aoc_rows, parse_block_rows, parse_statement_row, RISK_SCHEMA,
    AOC_SCHEMA, BLOCK_SCHEMA, STATEMENT_SCHEMA, to_native,
    risk_rank_sql, risk_level_sql,
)

POLYGON = [[[-1.5, 53.2], [-1.3, 53.2], [-1.3, 53.4], [-1.5, 53.2]]]
LINE    = [[-1.5, 53.2], [-1.3, 53.2], [-1.1, 53.3]]


def statement(polys):
    return {
        "id": 4068,
        "issued_at": "2026-05-29T10:30:00.752Z",
        "last_modified_at": "2026-05-29T10:31:00.000Z",
        "sources": [{"coastal": "c"}, {"river": "r"}],
        "flood_risk_trend": {"day1": "stable"},
        "public_forecast": {"england_forecast": "e", "wales_forecast_english": "w"},
        "risk_areas": [{
            "id": 1, "beyond_five_days": False,
            "risk_area_blocks": [{
                "id": 7, "days": [1, 3],
                "risk_levels": {"river": [2, 3], "coastal": [4, 1]},
                "polys": polys,
            }],
        }],
        "aoc_maps": [{"id": 9, "polys": [dict(polys[0], risk_area_block_id=7)]}],
    }


def test_labels_match_api_documentation():
    assert IMPACT_LABELS == {1: "Minor", 2: "Moderate", 3: "Significant", 4: "Extreme"}
    assert LIKELIHOOD_LABELS == {1: "Low", 2: "Medium", 3: "High", 4: "Very High"}


def test_documented_example_is_moderate_impact_high_likelihood():
    rows = parse_risk_rows(statement([{"id": 1, "type": "Polygon", "coordinates": POLYGON,
                                       "poly_type": "inland"}]), "now")
    river = [r for r in rows if r.source == "river"][0]
    assert (river.risk_x, river.risk_y) == (2, 3)
    assert (river.impact_label, river.likelihood_label) == ("Moderate", "High")


def test_polygon_and_line_types_are_preserved():
    geojson, gtype = build_geojson_geometry({"type": "Polygon", "coordinates": POLYGON})
    assert gtype == "Polygon" and '"Polygon"' in geojson
    geojson, gtype = build_geojson_geometry({"type": "LineString", "coordinates": LINE})
    assert gtype == "LineString" and '"LineString"' in geojson


def test_declared_type_wrong_for_coordinates_falls_back_to_nesting():
    # A polyline declared as a polygon (or no type at all) must not be wrapped as a Polygon
    assert build_geojson_geometry({"type": "Polygon", "coordinates": LINE})[1] == "LineString"
    assert build_geojson_geometry({"coordinates": LINE})[1] == "LineString"
    assert build_geojson_geometry({"coordinates": POLYGON})[1] == "Polygon"
    assert build_geojson_geometry({"type": "polyline", "coordinates": LINE})[1] == "LineString"


def test_unclassifiable_geometry_raises():
    with pytest.raises(ValueError):
        build_geojson_geometry({"coordinates": []})


def test_rows_expand_per_day_per_source_per_poly_and_carry_geometry_type():
    polys = [{"id": 1, "type": "Polygon", "coordinates": POLYGON, "poly_type": "inland"},
             {"id": 2, "type": "LineString", "coordinates": LINE, "poly_type": "coastal"}]
    rows = parse_risk_rows(statement(polys), "now")
    assert len(rows) == 2 * 2 * 2     # 2 days x 2 polys x 2 sources
    assert {r.geometry_type for r in rows} == {"Polygon", "LineString"}
    assert {r.forecast_date for r in rows} == {"2026-05-29", "2026-05-31"}


def test_rows_match_explicit_schemas():
    polys = [{"id": 1, "type": "Polygon", "coordinates": POLYGON, "poly_type": "inland"}]
    s = statement(polys)
    for row in parse_risk_rows(s, "now"):
        assert [f.name for f in RISK_SCHEMA.fields] == list(row.__fields__)
    for row in parse_aoc_rows(s, "now"):
        assert [f.name for f in AOC_SCHEMA.fields] == list(row.__fields__)
    for row in parse_block_rows(s, "now"):
        assert [f.name for f in BLOCK_SCHEMA.fields] == list(row.__fields__)
    assert [f.name for f in STATEMENT_SCHEMA.fields] == list(parse_statement_row(s, "now").__fields__)


def test_malformed_risk_levels_are_skipped_not_fatal():
    s = statement([{"id": 1, "type": "Polygon", "coordinates": POLYGON}])
    s["risk_areas"][0]["risk_area_blocks"][0]["risk_levels"] = {"river": None, "coastal": [4, 1]}
    rows = parse_risk_rows(s, "now")
    assert {r.source for r in rows} == {"coastal"}


def test_empty_or_null_sections_parse():
    s = {"id": 1, "issued_at": "2026-05-29T10:30:00Z", "sources": None,
         "flood_risk_trend": None, "public_forecast": None}
    assert parse_risk_rows(s, "now") == []
    assert parse_aoc_rows(s, "now") == []
    assert parse_statement_row(s, "now").statement_id == 1


def test_to_native_handles_pandas_values():
    import numpy as np
    import pandas as pd
    assert to_native(float("nan")) is None
    assert to_native(pd.NaT) is None
    assert to_native(np.int64(3)) == 3 and isinstance(to_native(np.int64(3)), int)
    assert to_native(np.bool_(True)) is True
    assert to_native("x") == "x"


def test_block_rows_hold_the_risk_for_each_block_day_and_source():
    polys = [{"id": 1, "type": "Polygon", "coordinates": POLYGON, "poly_type": "inland"}]
    blocks = parse_block_rows(statement(polys), "now")
    assert len(blocks) == 2 * 2                      # 2 days x 2 sources, no geometry, no polys
    river = [b for b in blocks if b.source == "river" and b.day_index == 1][0]
    assert (river.risk_area_block_id, river.risk_x, river.risk_y) == (7, 2, 3)
    assert (river.impact_label, river.likelihood_label, river.forecast_date) == ("Moderate", "High", "2026-05-29")


def test_block_risk_matches_risk_polygon_risk_so_the_two_can_be_linked():
    # The same block/day/source must give the same risk in both tables
    polys = [{"id": 1, "type": "Polygon", "coordinates": POLYGON, "poly_type": "inland"}]
    s = statement(polys)
    by_block = {(b.risk_area_block_id, b.day_index, b.source): (b.risk_x, b.risk_y, b.risk_colour)
                for b in parse_block_rows(s, "now")}
    for r in parse_risk_rows(s, "now"):
        assert by_block[(r.risk_area_block_id, r.day_index, r.source)] == (r.risk_x, r.risk_y, r.risk_colour)


def test_aoc_rows_keep_the_block_id_that_links_them_to_the_risk_blocks():
    polys = [{"id": 1, "type": "Polygon", "coordinates": POLYGON, "poly_type": "inland"}]
    aoc = parse_aoc_rows(statement(polys), "now")
    assert [a.risk_area_block_id for a in aoc] == [7]
    # and a block id that exists in the blocks table
    assert 7 in {b.risk_area_block_id for b in parse_block_rows(statement(polys), "now")}


def test_aoc_poly_without_a_block_id_is_kept_but_unlinked():
    s = statement([{"id": 1, "type": "Polygon", "coordinates": POLYGON}])
    s["aoc_maps"] = [{"id": 9, "polys": [{"id": 5, "type": "Polygon", "coordinates": POLYGON}]}]
    assert parse_aoc_rows(s, "now")[0].risk_area_block_id is None


# ---- retries -------------------------------------------------------------------------
def _fake_response(code, body=None):
    import types
    return types.SimpleNamespace(status_code=code, text="x", json=lambda: body or {})


def test_get_with_retry_retries_timeouts_then_succeeds(monkeypatch):
    import requests
    from utils import helpers
    calls = {"n": 0}

    def flaky(url, headers=None, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ReadTimeout("slow")
        return _fake_response(200, {"ok": True})

    monkeypatch.setattr(requests, "get", flaky)
    monkeypatch.setattr(helpers.time, "sleep", lambda s: None)
    assert helpers.get_with_retry("http://x", timeout=7).json() == {"ok": True}
    assert calls["n"] == 3


def test_get_with_retry_gives_up_with_the_timeout_after_the_last_attempt(monkeypatch):
    import requests
    from utils import helpers
    seen = []

    def always_slow(url, headers=None, params=None, timeout=None):
        seen.append(timeout)
        raise requests.ReadTimeout("slow")

    monkeypatch.setattr(requests, "get", always_slow)
    monkeypatch.setattr(helpers.time, "sleep", lambda s: None)
    with pytest.raises(requests.ReadTimeout):
        helpers.get_with_retry("http://x", timeout=120, attempts=3)
    assert seen == [120, 120, 120]


def test_ffc_get_passes_its_timeout_and_attempts_through(monkeypatch):
    import requests
    from utils import helpers
    seen = {}

    def capture(url, headers=None, params=None, timeout=None):
        seen.update(timeout=timeout, key=headers["X-Api-Key"])
        return _fake_response(200, {"statements": []})

    monkeypatch.setattr(requests, "get", capture)
    assert helpers.ffc_get("http://x", "KEY", timeout=150) == {"statements": []}
    assert seen == {"timeout": 150, "key": "KEY"}


def test_risk_rank_sql_covers_every_level_and_defaults_to_zero():
    sql = risk_rank_sql("i.risk_level")
    for level, rank in (("Very Low", 1), ("Low", 2), ("Medium", 3), ("High", 4)):
        assert f"WHEN '{level}' THEN {rank}" in sql
    assert sql.startswith("CASE i.risk_level") and sql.endswith("ELSE 0 END")


def test_risk_level_sql_is_the_inverse_and_has_no_zero_branch():
    sql = risk_level_sql("MAX(c.rk)")
    for level, rank in (("Very Low", 1), ("Low", 2), ("Medium", 3), ("High", 4)):
        assert f"WHEN {rank} THEN '{level}'" in sql
    assert "ELSE" not in sql        # rank 0 gives NULL, so callers can COALESCE to 'None'


# ---- areas under the forecast (MP briefing sentence) -------------------------

from utils.helpers import areas_under_forecast_sentence  # noqa: E402


def test_areas_sentence_names_counts_risk_and_source():
    s = areas_under_forecast_sentence(10, 3, "Medium", ["river"])
    assert s == ("10 flood warning areas and 3 flood alert areas lie under the forecast Medium river "
                 "flood risk. These are places where the Environment Agency could issue a flood warning "
                 "or alert if the risk is realised.")


def test_areas_sentence_is_silent_when_there_are_none():
    assert areas_under_forecast_sentence(0, 0, "Medium", ["river"]) is None
    assert areas_under_forecast_sentence(None, None, "Medium", []) is None


def test_areas_sentence_singular_and_single_type():
    s = areas_under_forecast_sentence(1, 0, "Low", ["coastal"])
    assert s.startswith("1 flood warning area lies under the forecast Low coastal flood risk.")
    assert "This is a place where the Environment Agency could issue a flood warning if" in s
    assert "alert" not in s


def test_areas_sentence_joins_several_sources_in_a_stable_order():
    s = areas_under_forecast_sentence(0, 2, "High", ["surface", "river", "ground"])
    assert "forecast High groundwater, river and surface water flood risk" in s
