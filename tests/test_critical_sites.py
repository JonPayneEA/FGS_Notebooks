# Tests for the critical site helpers. Run with:  pytest tests
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.critical_sites import (  # noqa: E402
    COLUMNS, classify_osm, dedupe_nearby, fetch_ods_role, gias_sites, normalise_postcode, ods_sites, osm_sites, standardise,
)


def test_postcodes_join_whatever_the_spacing_or_case():
    assert list(normalise_postcode(["LS1 4HR", "ls14hr", " LS1  4HR "])) == ["LS14HR"] * 3


def test_gias_keeps_open_schools_with_coordinates_and_uses_the_phase_as_the_type():
    df = pd.DataFrame({
        "URN": [1, 2, 3, 4], "EstablishmentName": ["A", "B", "C", "D"], "EstablishmentStatus (name)": ["Open", "Closed", "Open", "Open, but proposed to close"],
        "TypeOfEstablishment (name)": ["Community school", "x", "Academy", "Other"], "PhaseOfEducation (name)": ["Primary", "Primary", "Not applicable", "Secondary"],
        "Postcode": ["P1", "P2", "P3", "P4"], "Easting": [400000, 410000, 0, 420000], "Northing": [300000, 310000, 0, 320000],
    })
    s = gias_sites(df)
    assert list(s["source_id"]) == ["1", "4"] and list(s["site_type"]) == ["Primary", "Secondary"] and (s["site_group"] == "education").all()
    assert list(s.columns) == COLUMNS


def test_ods_sites_are_placed_by_postcode_and_unplaceable_ones_dropped():
    orgs = [{"OrgId": "A1", "Name": "Alpha Surgery", "PostCode": "LS1 4HR"}, {"OrgId": "B2", "Name": "Beta Hospital", "PostCode": "ZZ9 9ZZ"}]
    pcs = pd.DataFrame({"pcds": ["LS1 4HR", "AB1 0AA"], "east1m": ["430000", "385000"], "north1m": ["433000", "801000"]})
    s = ods_sites(orgs, "GP practice", pcs)
    assert len(s) == 1 and s.iloc[0]["source_id"] == "A1" and s.iloc[0]["easting"] == 430000 and s.iloc[0]["site_group"] == "health"


@pytest.mark.parametrize("tags,expected", [
    ({"amenity": "hospital"}, ("health", "hospital")),
    ({"amenity": "doctors"}, ("health", "gp surgery")),
    ({"amenity": "social_facility", "social_facility": "nursing_home"}, ("health", "care home")),
    ({"amenity": "social_facility", "social_facility": "food_bank"}, None),
    ({"amenity": "university"}, ("education", "university")),
    ({"amenity": "police"}, ("emergency", "police station")),
    ({"amenity": "fire_station"}, ("emergency", "fire station")),
    ({"emergency": "ambulance_station"}, ("emergency", "ambulance station")),
    ({"amenity": "prison"}, ("emergency", "prison")),
    ({"power": "substation", "voltage": "132000;33000"}, ("utilities", "substation")),
    ({"power": "substation", "voltage": "11000"}, None),                    # a pole transformer is not critical
    ({"power": "substation", "substation": "transmission"}, ("utilities", "substation")),
    ({"man_made": "wastewater_plant"}, ("utilities", "sewage works")),
    ({"man_made": "water_works"}, ("utilities", "water treatment works")),
    ({"amenity": "cafe"}, None),
])
def test_osm_tags_become_a_group_and_type(tags, expected):
    assert classify_osm(tags) == expected


def test_osm_schools_are_left_out_unless_asked_for_because_gias_has_them():
    assert classify_osm({"amenity": "school"}) is None
    assert classify_osm({"amenity": "school"}, include_schools=True) == ("education", "school")


def test_osm_features_become_british_national_grid_sites():
    feats = pd.DataFrame({"osm_id": [10, 11], "kind": ["node", "way"], "lon": [-1.5491, -1.5], "lat": [53.7997, 53.8],
                          "tags": [{"amenity": "hospital", "name": "Leeds General"}, {"amenity": "cafe"}]})
    s = osm_sites(feats)
    assert len(s) == 1 and s.iloc[0]["name"] == "Leeds General" and s.iloc[0]["source_id"] == "node/10"
    assert 425000 < s.iloc[0]["easting"] < 435000 and 430000 < s.iloc[0]["northing"] < 436000           # Leeds city centre


def test_the_same_place_from_two_sources_is_kept_once_but_a_different_group_nearby_is_not_dropped():
    keep = standardise([pd.DataFrame({"site_id": "", "site_group": ["health"], "site_type": ["hospital"], "name": ["A"], "easting": [400000.0],
                                      "northing": [300000.0], "postcode": [""], "source": ["nhs_ods"], "source_id": ["1"]})])
    cand = pd.DataFrame({"site_id": "", "site_group": ["health", "health", "emergency"], "site_type": ["hospital"] * 3, "name": ["A2", "far", "police"],
                         "easting": [400050.0, 405000.0, 400050.0], "northing": [300000.0, 300000.0, 300000.0], "postcode": [""] * 3,
                         "source": ["osm"] * 3, "source_id": ["a", "b", "c"]})
    out = dedupe_nearby(keep, cand, 150)
    assert list(out["name"]) == ["far", "police"]


def test_standardise_gives_unique_ids_even_for_repeated_source_ids():
    f = pd.DataFrame({"site_id": "", "site_group": ["health"] * 2, "site_type": ["x"] * 2, "name": ["a", None], "easting": [1.0, 2.0], "northing": [1.0, 2.0],
                      "postcode": [None, "P"], "source": ["osm"] * 2, "source_id": ["node/1", "node/1"]})
    s = standardise([f, None, pd.DataFrame(columns=COLUMNS)])
    assert s["site_id"].is_unique and list(s.columns) == COLUMNS and s["name"].tolist() == ["a", ""]


def test_reading_an_osm_file_finds_nodes_and_way_centres(tmp_path):
    pytest.importorskip("osmium")
    from utils.osm_read import read_osm_features
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="53.80" lon="-1.55" version="1"><tag k="amenity" v="hospital"/><tag k="name" v="H"/></node>
  <node id="2" lat="53.00" lon="-1.00" version="1"/><node id="3" lat="53.00" lon="-1.02" version="1"/>
  <node id="4" lat="53.02" lon="-1.02" version="1"/><node id="5" lat="53.02" lon="-1.00" version="1"/>
  <node id="6" lat="52.00" lon="-1.00" version="1"><tag k="shop" v="bakery"/></node>
  <way id="20" version="1"><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="5"/><nd ref="2"/><tag k="amenity" v="fire_station"/></way>
</osm>"""
    p = tmp_path / "t.osm"
    p.write_text(xml)
    d = read_osm_features(str(p))
    assert sorted(d["osm_id"]) == [1, 20]
    way = d[d["osm_id"] == 20].iloc[0]
    assert way["kind"] == "way" and abs(way["lat"] - 53.012) < 0.01 and abs(way["lon"] - (-1.0125)) < 0.01     # mean of the five node positions
    s = osm_sites(d)
    assert sorted(s["site_type"]) == ["fire station", "hospital"]


def test_the_reader_can_filter_while_reading(tmp_path):
    pytest.importorskip("osmium")
    from utils.osm_read import read_osm_features
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="test">
  <node id="1" lat="53.80" lon="-1.55" version="1"><tag k="amenity" v="hospital"/></node>
  <node id="2" lat="53.81" lon="-1.56" version="1"><tag k="amenity" v="bench"/></node>
</osm>"""
    p = tmp_path / "k.osm"
    p.write_text(xml)
    from utils.critical_sites import classify_osm
    d = read_osm_features(str(p), keep=lambda tags: classify_osm(tags) is not None)
    assert list(d["osm_id"]) == [1]
    assert sorted(read_osm_features(str(p))["osm_id"]) == [1, 2]


class FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body or {}, text

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def make_get(accept, total=2500):
    """A fake API that answers 406 unless `accept(headers, params)` is true, and, like the real one, 406 for an Offset below 1 (it counts from 1)."""
    calls = []

    def get(url, params, headers):
        calls.append((dict(params), dict(headers)))
        if params["Offset"] < 1:
            return FakeResponse(406, text='{"errorCode":406,"errorText":"Supplied Offset must be greater than 1"}')
        if not accept(headers, params):
            return FakeResponse(406, text="Not Acceptable")
        lo = params["Offset"] - 1
        return FakeResponse(200, {"Organisations": [{"OrgId": str(i)} for i in range(lo, min(lo + params["Limit"], total))]})
    get.calls = calls
    return get


def test_the_ods_fetch_falls_back_to_a_variant_the_api_accepts_and_pages_through_everything():
    get = make_get(lambda h, p: "Mozilla" in h.get("User-Agent", ""))          # only a browser-like client is accepted
    orgs, label = fetch_ods_role(get, "RO198")
    assert len(orgs) == 2500 and label == "browser client"
    assert len({o["OrgId"] for o in orgs}) == 2500                              # no page repeated or missed
    assert [p["Offset"] for p, h in get.calls if "Mozilla" in h.get("User-Agent", "")] == [1, 1001, 2001]    # three pages, counted from 1, all with the chosen variant


def test_the_first_variant_is_used_when_it_works_and_a_total_of_exactly_one_page_stops_at_the_next_empty_one():
    get = make_get(lambda h, p: p.get("_format") == "json", total=1000)
    orgs, label = fetch_ods_role(get, "RO76")
    assert label == "_format=json" and len(orgs) == 1000


def test_when_every_variant_is_refused_the_error_shows_each_status():
    get = make_get(lambda h, p: False)
    with pytest.raises(RuntimeError) as e:
        fetch_ods_role(get, "RO198")
    msg = str(e.value)
    assert "406" in msg and "_format=json" in msg and "browser client" in msg and "Not Acceptable" in msg


def test_the_first_attempt_succeeds_now_that_paging_starts_at_one():
    get = make_get(lambda h, p: True, total=1500)
    orgs, label = fetch_ods_role(get, "RO198")
    assert label == "_format=json" and len(orgs) == 1500 and get.calls[0][0]["Offset"] == 1
