# Tests for the pure parsing helpers. Run locally with:  pytest tests
# Needs pandas and pyspark installed (Row/types only; no Spark session).
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.helpers import (  # noqa: E402
    IMPACT_LABELS, LIKELIHOOD_LABELS, build_geojson_geometry,
    parse_risk_rows, parse_aoc_rows, parse_statement_row, RISK_SCHEMA,
    AOC_SCHEMA, STATEMENT_SCHEMA, to_native,
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
        "aoc_maps": [{"id": 9, "polys": [polys[0]]}],
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
