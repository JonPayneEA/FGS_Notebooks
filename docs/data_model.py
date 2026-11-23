"""
docs/data_model.py
Builds the data model diagram of the FGS pipeline (docs/data_model.svg and docs/data_model.png) with Graphviz.

    python docs/data_model.py        needs the Graphviz `dot` program on the path

Each box is a table (or view) in the catalogue; each arrow is a job that reads one and writes another, labelled with the job number.
Edit the TABLES and EDGES lists below when a job or a table changes, then run it again and commit both images.
"""

import html
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# domain -> (cluster title, fill colour)
DOMAINS = {
    "fgs":   ("Flood Guidance Statement", "#DCE9F7"),
    "ref":   ("Reference geography and contacts", "#E4F2E1"),
    "prod":  ("Constituency products", "#FBEFD3"),
    "warn":  ("Warnings and their history", "#F7DDE5"),
    "rt":    ("Real-time gauges and thresholds", "#DDF0F2"),
    "expo":  ("Exposure and risk grid (people, sites, flood bands)", "#EAE3F4"),
    "rfg":   ("Rapid flood guidance", "#FBE3D3"),
    "econ":  ("Economics", "#EFEFEF"),
}

# id, label, key columns, domain, kind ("table", "view", "restricted", "source", "manual")
TABLES = [
    # sources
    ("s_fgs", "FFC Flood Guidance Statement API", "", "fgs", "source"),
    ("s_rfg", "FFC Rapid Flood Guidance API", "", "rfg", "source"),
    ("s_wfs", "EA geoservices: flood warning and alert areas", "", "ref", "source"),
    ("s_warn", "EA flood warnings API", "", "warn", "source"),
    ("s_msgs", "EA warning message history (file)", "", "warn", "source"),
    ("s_rt", "EA real-time API and archive", "", "rt", "source"),
    ("s_thr", "Thresholds and WISKI mapping files", "", "rt", "source"),
    ("s_parl", "Parliament Members API", "", "ref", "source"),
    ("s_ons", "ONS: boundaries, Census 2021, UPRN Lookup, deprivation", "", "expo", "source"),
    ("s_haz", "Defra and EA flood hazard maps; GIAS, NHS ODS, OpenStreetMap", "", "expo", "source"),
    ("s_admin", "EA administrative area boundaries", "", "ref", "source"),
    # fgs
    ("fgs_statements", "fgs_statements", "statement_id", "fgs", "table"),
    ("fgs_risk_polygons", "fgs_risk_polygons", "statement_id, polygon", "fgs", "table"),
    ("fgs_aoc_polygons", "fgs_aoc_polygons", "statement_id", "fgs", "table"),
    ("fgs_risk_blocks", "fgs_risk_blocks", "statement_id", "fgs", "table"),
    # ref
    ("ea_fwa", "ea_flood_warning_areas", "ea_area_code", "ref", "table"),
    ("ea_faa", "ea_flood_alert_areas", "ea_area_code, ea_area", "ref", "table"),
    ("constituencies", "parliamentary_constituencies", "constituency_id", "ref", "table"),
    ("mp", "mp_contact_details", "constituency_id", "ref", "table"),
    ("lookup", "ea_area_constituency_lookup", "ea_area_code, constituency_id", "ref", "table"),
    ("admin", "ea_administrative_areas", "", "ref", "table"),
    # prod
    ("fgs_ea_int", "fgs_ea_area_intersections", "statement_id, ea_area_code", "prod", "table"),
    ("fgs_con_int", "fgs_constituency_intersections", "statement_id, constituency_id", "prod", "table"),
    ("int_log", "fgs_intersections_log", "statement_id", "prod", "table"),
    ("fgs_contacts", "fgs_constituency_mp_contacts", "statement_id, constituency_id", "prod", "table"),
    ("fgs_changes", "fgs_constituency_changes", "statement_id, constituency_id", "prod", "table"),
    ("changes_log", "fgs_changes_log", "statement_id", "prod", "table"),
    ("situation", "constituency_flood_situation", "constituency_id", "prod", "table"),
    ("drafts", "mp_briefing_drafts", "statement_id, constituency_id", "prod", "table"),
    ("fgs_expo", "fgs_constituency_exposure", "constituency_id, month", "prod", "table"),
    ("health", "pipeline_health", "check_name", "prod", "table"),
    # warn
    ("warn_cur", "flood_warnings_current", "ea_area_code", "warn", "table"),
    ("warn_log", "flood_warnings_log", "ea_area_code, time", "warn", "table"),
    ("warn_con", "flood_warning_constituency_contacts", "ea_area_code, constituency_id", "warn", "table"),
    ("msg_hist", "ea_warning_messages_history", "ea_area_code, time", "warn", "table"),
    ("episodes", "ea_warning_episodes", "episode_id, ea_area_code", "warn", "table"),
    ("periods", "ea_warning_periods", "ea_area_code", "warn", "table"),
    ("cov", "ea_warning_fgs_coverage", "episode_id, statement_id", "warn", "table"),
    # rt
    ("rt_bronze", "ea_rt_readings_bronze", "measure, time", "rt", "table"),
    ("rt_live", "ea_rt_readings_live", "measure, time", "rt", "table"),
    ("rt_all", "ea_rt_readings_all", "bronze and live, one reading once", "rt", "view"),
    ("rt_latest", "ea_rt_latest", "newest reading per measure", "rt", "view"),
    ("rt_register", "ea_rt_measure_register", "measure", "rt", "table"),
    ("rt_stations", "ea_rt_stations", "station", "rt", "table"),
    ("rt_station_measures", "ea_rt_station_measures", "station, measure", "rt", "table"),
    ("wiski", "ea_rt_wiski_lookup", "", "rt", "table"),
    ("tele", "ea_rt_telemetry_mapping", "", "rt", "table"),
    ("mapped", "ea_rt_measures_mapped", "measure", "rt", "table"),
    ("thresholds", "ea_thresholds_all", "threshold", "rt", "restricted"),
    ("thr_links", "ea_fw_threshold_gauge_links", "threshold, measure, ea_area_code", "rt", "restricted"),
    ("timing", "ea_fw_timing_validation", "", "rt", "restricted"),
    # expo
    ("census", "census2021_oa", "oa", "expo", "table"),
    ("imd", "imd2025_lsoa", "lsoa", "expo", "table"),
    ("hazard", "hazard_cells_100m", "cell_key, source", "expo", "table"),
    ("sites", "critical_sites", "site_id", "expo", "restricted"),
    ("points", "exposure_address_points", "uprn, cell_key", "expo", "restricted"),
    ("grid", "exposure_grid_100m", "cell_key", "expo", "restricted"),
    ("area_expo", "ea_area_exposure", "kind, area_id", "expo", "restricted"),
    ("area_over", "ea_area_overlaps", "area_a, area_b", "expo", "restricted"),
    ("area_con", "ea_area_constituency_exposure", "area_id, pcon", "expo", "restricted"),
    ("area_geo", "ea_area_geographies", "area_id, geography", "expo", "restricted"),
    ("area_sites", "ea_area_sites", "area_id, site_id", "expo", "restricted"),
    ("area_cov", "ea_area_coverage", "", "expo", "restricted"),
    ("con_expo", "constituency_flood_exposure", "pcon", "expo", "restricted"),
    ("con_sites", "constituency_sites", "pcon, site_id", "expo", "restricted"),
    ("geo_names", "responder_geography_names", "geography, code", "expo", "table"),
    ("oa_fire", "oa_fire_authority", "oa", "expo", "table"),
    ("district", "district_responder_lookup", "", "expo", "table"),
    ("geo_expo", "geography_flood_exposure", "geography, code", "expo", "restricted"),
    # rfg
    ("rfg_updates", "rfg_updates", "update_key", "rfg", "table"),
    ("rfg_polygons", "rfg_polygons", "update_key, polygon_number", "rfg", "table"),
    ("rfg_checks", "rfg_checks", "one row, last check", "rfg", "table"),
    ("rfg_expo", "rfg_polygon_exposure", "polygon_key", "rfg", "restricted"),
    ("rfg_con", "rfg_polygon_constituencies", "polygon_key, pcon", "rfg", "restricted"),
    ("rfg_areas", "rfg_polygon_areas", "polygon_key, area_id", "rfg", "restricted"),
    ("rfg_sites", "rfg_polygon_sites", "polygon_key, site_id", "rfg", "restricted"),
    # econ
    ("econ", "economics_assumptions", "item, scenario", "econ", "manual"),
]

# (from, to, job number label). Reads are drawn into the job's output, so an arrow means "feeds".
EDGES = [
    ("s_fgs", "fgs_statements", "01, 02"), ("s_fgs", "fgs_risk_polygons", "01, 02"), ("s_fgs", "fgs_aoc_polygons", "01, 02"), ("s_fgs", "fgs_risk_blocks", "01, 02"),
    ("s_wfs", "ea_fwa", "03"), ("s_wfs", "ea_faa", "03"),
    ("s_parl", "mp", "04"), ("s_ons", "constituencies", "05"), ("s_admin", "admin", "12"),
    ("ea_fwa", "lookup", "06"), ("ea_faa", "lookup", "06"), ("constituencies", "lookup", "06"),
    ("fgs_risk_polygons", "fgs_ea_int", "07"), ("ea_fwa", "fgs_ea_int", "07"), ("ea_faa", "fgs_ea_int", "07"),
    ("fgs_risk_polygons", "fgs_con_int", "07"), ("constituencies", "fgs_con_int", "07"),
    ("fgs_con_int", "int_log", "07"),
    ("fgs_con_int", "fgs_contacts", "14"), ("mp", "fgs_contacts", "14"),
    ("fgs_con_int", "fgs_changes", "18"), ("fgs_changes", "changes_log", "18"),
    ("fgs_contacts", "situation", "19"), ("fgs_changes", "situation", "19"), ("warn_con", "situation", "19"),
    ("situation", "drafts", "20, 24"),
    ("fgs_con_int", "fgs_expo", "21"),
    ("s_warn", "warn_cur", "11"), ("s_warn", "warn_log", "11"),
    ("warn_cur", "warn_con", "13"), ("lookup", "warn_con", "13"), ("mp", "warn_con", "13"),
    ("s_msgs", "msg_hist", "25"), ("msg_hist", "episodes", "25"), ("warn_log", "episodes", "25"), ("episodes", "periods", "25"),
    ("episodes", "cov", "26"), ("fgs_ea_int", "cov", "26"),
    ("s_rt", "rt_bronze", "08, 10"), ("s_rt", "rt_live", "23"), ("rt_bronze", "rt_all", ""), ("rt_live", "rt_all", ""), ("rt_all", "rt_latest", ""),
    ("s_rt", "rt_register", "09"), ("s_rt", "rt_stations", "15"), ("s_rt", "rt_station_measures", "15"),
    ("s_thr", "wiski", "16"), ("s_thr", "tele", "16"), ("rt_register", "mapped", "16"), ("wiski", "mapped", "16"),
    ("s_thr", "thresholds", "17"),
    ("thresholds", "thr_links", "28"), ("mapped", "thr_links", "28"), ("tele", "thr_links", "28"),
    ("episodes", "timing", "27"), ("rt_all", "timing", "27"), ("thr_links", "timing", "27"),
    ("s_ons", "census", "29"), ("s_ons", "imd", "29"),
    ("s_haz", "hazard", "30"), ("s_haz", "sites", "31"),
    ("s_ons", "points", "33"), ("census", "points", "33"), ("imd", "points", "33"), ("hazard", "points", "33"),
    ("points", "grid", "34"), ("hazard", "grid", "34"), ("sites", "grid", "34"),
    ("points", "area_expo", "35"), ("ea_fwa", "area_expo", "35"), ("ea_faa", "area_expo", "35"), ("sites", "area_expo", "35"),
    ("area_expo", "area_over", "35"), ("area_expo", "area_con", "35"), ("area_expo", "area_geo", "35"), ("area_expo", "area_sites", "35"),
    ("area_expo", "area_cov", "35"), ("area_expo", "con_expo", "35"), ("area_expo", "con_sites", "35"),
    ("points", "geo_expo", "32"), ("sites", "geo_expo", "32"), ("s_ons", "geo_names", "32"), ("s_ons", "oa_fire", "32"), ("s_ons", "district", "32"),
    ("periods", "timing", "27"), ("rt_stations", "mapped", "16"),
    ("grid", "area_expo", "35"), ("geo_names", "area_geo", "35"), ("oa_fire", "area_geo", "35"), ("district", "area_geo", "35"),
    ("s_rfg", "rfg_updates", "36"), ("s_rfg", "rfg_polygons", "36"), ("s_rfg", "rfg_checks", "36"),
    ("rfg_polygons", "rfg_expo", "37"), ("points", "rfg_expo", "37"), ("rfg_expo", "rfg_con", "37"), ("rfg_expo", "rfg_areas", "37"), ("rfg_expo", "rfg_sites", "37"),
]

KIND_STYLE = {
    "table":      dict(style="filled", penwidth="1"),
    "view":       dict(style="filled,dashed", penwidth="1.2"),
    "restricted": dict(style="filled", penwidth="3"),
    "manual":     dict(style="filled,bold", penwidth="2"),
    "source":     dict(style="filled,rounded", penwidth="1"),
}


def node_label(label, key):
    name = f'<FONT FACE="Helvetica-Bold" POINT-SIZE="11">{html.escape(label)}</FONT>'
    k = f'<BR/><FONT POINT-SIZE="8" COLOR="#555555">{html.escape(key)}</FONT>' if key else ""
    return f"<{name}{k}>"


def build_dot():
    out = ['digraph fgs {',
           '  graph [rankdir=LR, compound=true, fontname="Helvetica", nodesep=0.18, ranksep=0.9, splines=true, pad=0.3, bgcolor="white", labelloc=t, fontsize=26,',
           '         label="FGS pipeline: data model\\l"];',
           '  node [shape=box, fontname="Helvetica", margin="0.10,0.05", color="#333333"];',
           '  edge [color="#8A8A8A", arrowsize=0.6, fontname="Helvetica", fontsize=8, fontcolor="#444444"];']
    for dom, (title, fill) in DOMAINS.items():
        out.append(f'  subgraph cluster_{dom} {{ label=<<B>{html.escape(title)}</B>>; fontsize=16; style="rounded,filled"; fillcolor="{fill}"; color="#999999";')
        for tid, label, key, d, kind in TABLES:
            if d != dom:
                continue
            st = KIND_STYLE[kind]
            fill_node = {"source": "#F6F6F6", "manual": "#FFF4C2"}.get(kind, "#FFFFFF")
            border = "#1F2A44" if kind == "restricted" else "#333333"
            out.append(f'    {tid} [label={node_label(label, key)}, fillcolor="{fill_node}", color="{border}", style="{st["style"]}", penwidth={st["penwidth"]}];')
        out.append("  }")
    out.append('  app [label=<<FONT POINT-SIZE="16"><B>Flood Planning App</B></FONT><BR/>R Shiny, reads these tables<BR/>through a SQL warehouse>, shape=box, style="filled,bold", fillcolor="#FFF2B3", penwidth=2.5];')
    for tid, to_cluster in [("situation", "prod"), ("episodes", "warn"), ("rt_latest", "rt"), ("area_expo", "expo"), ("rfg_expo", "rfg"), ("econ", "econ")]:
        out.append(f'  {tid} -> app [color="#C9A400", penwidth=2, style=dashed, arrowsize=0.8];')
    out.append('  key [shape=plaintext, label=<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="6" CELLPADDING="5">'
               '<TR><TD COLSPAN="2" BORDER="0"><B>Key</B></TD></TR>'
               '<TR><TD>table</TD><TD BORDER="0" ALIGN="LEFT">a Delta table in the catalogue</TD></TR>'
               '<TR><TD STYLE="dotted">view</TD><TD BORDER="0" ALIGN="LEFT">a view over other tables</TD></TR>'
               '<TR><TD BORDER="3"><B>restricted</B></TD><TD BORDER="0" ALIGN="LEFT">in the restricted schema (built from address-level data)</TD></TR>'
               '<TR><TD BORDER="2" BGCOLOR="#FFF4C2"><B>edited by hand</B></TD><TD BORDER="0" ALIGN="LEFT">changed with an UPDATE, never overwritten by a job</TD></TR>'
               '<TR><TD STYLE="rounded" BGCOLOR="#F6F6F6">source</TD><TD BORDER="0" ALIGN="LEFT">an external data source</TD></TR>'
               '<TR><TD BORDER="0">NN</TD><TD BORDER="0" ALIGN="LEFT">arrows are labelled with the job (notebook) number that builds the table</TD></TR>'
               '<TR><TD BORDER="0"><FONT COLOR="#C9A400">- - &gt;</FONT></TD><TD BORDER="0" ALIGN="LEFT">read by the Flood Planning App</TD></TR>'
               '</TABLE>>];')
    out.append('  health_note [label=<<I>pipeline_health (job 22) checks the age and size of most tables above</I>>, shape=plaintext, fontsize=10];')
    out.append('  health_note -> health [style=dotted, arrowhead=none];')
    for a, b, lab in EDGES:
        attrs = []
        if lab:
            attrs.append(f'label="{lab}"')
        out.append(f'  {a} -> {b}' + (f' [{", ".join(attrs)}]' if attrs else '') + ';')
    out.append("}")
    return "\n".join(out)


def main():
    dot = build_dot()
    (HERE / "data_model.dot").write_text(dot, encoding="utf-8")
    for fmt, extra in (("svg", []), ("png", ["-Gdpi=110"])):
        subprocess.run(["dot", f"-T{fmt}", *extra, str(HERE / "data_model.dot"), "-o", str(HERE / f"data_model.{fmt}")], check=True)
    print("wrote docs/data_model.svg and docs/data_model.png")


if __name__ == "__main__":
    sys.exit(main())
