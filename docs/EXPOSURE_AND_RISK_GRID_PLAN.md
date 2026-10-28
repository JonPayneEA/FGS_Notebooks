# Exposure and risk grid: plan

Status: plan and foundation only. Nothing here has run against real data. England only (Scotland and Wales are out of scope for now).

## What it gives you

For every flood warning area and flood alert area, and for every constituency:

- **People.** Population, households, people aged 65 and over, and deprivation (IoD2025), from Census 2021.
- **Risk zones.** How much of the area, and how many people, properties and critical sites, sit in each NaFRA2 band (High, Medium, Low, Very Low) for **rivers and the sea**, with Flood Zones 2 and 3 as a second view split into coastal and fluvial. Surface water is not part of the first release (see "Coastal, fluvial and surface water").
- **Critical sites.** Counts by group (health and care, education, emergency services and prisons, utilities and hazardous sites), and by risk band.
- **A gridded risk product.** A 100 m British National Grid layer holding all of the above per cell, with an adjustable composite score on top. The layers are always stored. The score is never the only thing kept.

## What it will look like

A mock-up with invented numbers, in the app's design, is in `docs/EXPOSURE_MOCKUP.html` (open it in a browser). It shows a new Areas section with a sortable warning areas table, one page per area (key figures, the 100 m risk grid, a breakdown by band for rivers and sea and for surface water, and the critical sites in High or Medium bands), the extra lines in a forecast map popup, and the new exports. The flood bands use a blue ramp and the score a magenta one, so neither is mistaken for a forecast level.

## Decisions already made

| Decision | Choice |
|---|---|
| Method | **Points first, grid second.** Where address or receptor points are held, the sums are made from the points (exact point in area, band looked up at the point). The 100 m British National Grid is the aggregation and product layer, and carries the layers that have no points. |
| Grid | 100 m British National Grid. Cell key = (northing // 100) * 10,000 + (easting // 100). |
| Risk zones | NaFRA2 rivers and sea bands, and Flood Zones 2 and 3 split by mapping source into coastal (sea), fluvial (river) and mapped together. Surface water is out of the first release. See "Coastal, fluvial and surface water". |
| Critical sites | All four groups: health and care, education, emergency services and prisons, utilities and hazardous sites. |
| Score | Layers first, plus a simple adjustable score. Weights in `config.RISK_SCORE_WEIGHTS`. |
| Big Environment Agency datasets | May already be governed tables, so notebooks read a table name or a file path from a widget, whichever you have. |
| Out of scope | Scotland and Wales observed data. |

## Coastal, fluvial and surface water

These are three different hazards. They are kept apart in every table and in the app, and are never added or merged without saying so.

- **Warning and alert areas are for coastal and fluvial risk.** Each area already carries its type from the EA feed (`river_or_sea`, `is_tidal`, and the code: fluvial, coastal or tidal). Exposure for a warning area is reported against **rivers and sea** first, labelled with that type.
- **Surface water is not part of the warning area work.** The FGS issues surface water risk, but acting on it is not yet the Agency's responsibility, and warning and alert areas are not issued for it. So the first release has no surface water columns, no surface water in the score, and job 30 does not run it by default. It is a separate, later piece, below.
- **The NaFRA2 Risk of Flooding from Rivers and Sea layer is one layer.** It does not say which cell is coastal and which fluvial. The area's type gives the reading: a coastal area's band is mainly sea risk, a fluvial area's mainly river risk, and a tidal river area both.
- **Flood Zones 2 and 3 do say.** They carry a mapping source, so job 30 writes them split into sea (coastal), river (fluvial) and river and sea mapped together, as well as all together. That is the only true coastal against fluvial split in the data we hold.
- **Where an area's type is mixed** (a warning area with both tidal and river sources), the figures are reported once against rivers and sea, never summed across types.

### Later: what sits under the FGS surface water risk polygons

The useful question is about the FGS's own surface water risk polygons, not the warning areas: which places and people lie under a polygon, and where within it the risk is highest (the Risk of Flooding from Surface Water High and Medium bands). The machinery is the same (job 30 with `source = rofsw`, then points looked up in the rasters), so the exposure counts are not the hard part: people, households, over-65s, deprivation and critical sites under each polygon, split by surface water band.

The large part is richer context: what land use and buildings sit under the polygons, and which areas are most at risk. That is open-ended and is deliberately not planned yet. Decide the question first, then build only what answers it. Costs to know: the surface water source is about 85 GB with its depth files (the band files alone are far less), and the FGS polygons would need the same point-in-polygon step against `fgs_risk_polygons`.

## Units and how each number is made

**The unit of truth is a point where points exist, a cell where they do not.** Either way a person is counted once, so the overlap rules below apply to both.

**With address or receptor points** (OS AddressBase, the ONS UPRN directory, the EA National Receptor Database, subject to your licence):
- **People and households.** Each output area's Census totals are shared among the residential address points inside it (`allocate_to_points`), so people sit where the buildings are. An output area with totals but no residential point is reported as unallocated and placed over its cells instead.
- **Warning areas.** Exact point in polygon (`points_in_areas`). No grid, no 50 m error. This matters most for small areas.
- **Flood bands.** Looked up at the point in the rivers and sea raster and in the Flood Zone rasters.
- **Deprivation.** The LSOA decile comes with the point.
- **Properties at risk.** Counted from the points (or the NaFRA2 receptor counts where the Agency's own assessment is preferred).
- **Critical sites.** Points in the same way.

**With no points** (continuous layers, and any gap in the address data), a polygon owns the cells whose **centre** is inside it. One rule is used everywhere so layers agree, with an error of at most 50 m at a boundary. A polygon smaller than a cell owns one cell, so no total is lost. Code: `utils/grid100.py`.
- **Hazard bands on the grid.** The cell stores the **share of its area in each band** (`band_shares_from_samples`), never "worst band wins", which would turn a cell with one patch of surface water High.
- **Population over cells.** An output area's total is spread over its cells only where there are no points, and is labelled as an even spread in the output.

**The grid** is then built by adding the points up into cells and adding the continuous layers, to give the product, the maps and the exports.

**Licensing.** Address-level data is licensed. The address-level table stays in the **restricted** schema. Only aggregates (per area, per cell with small counts handled, per constituency) go to the open schema and the app, and the licence terms for a derived grid should be checked before it is shared.

## Overlapping areas

Adding polygon totals counts people twice wherever areas overlap. The rule is: **the cell is the unit of truth**. A person lives in one cell.

**Alerts and warnings.** A flood alert area contains the flood warning areas inside it. That interaction is easy: filter to alerts or to warnings, and the two never meet.

**Warning over warning** is the real problem, because there is no parent and child between warnings: two fluvial areas can meet, and a fluvial and a coastal or tidal area can cover the same ground. The approach has four parts.

1. **Measure it first.** `overlap_depth` says how many cells are covered once, twice, three times, and how many people are in each group. `pair_overlaps` lists every pair of warning areas that share ground, the shared cells and people, and what share of each area is shared. A pair where both shares are near 1 is the same ground under two codes, which is a data question and not an analysis one. Run on warning areas only, this shows how large the problem is before any rule is chosen.
2. **Mass analysis uses distinct cells.** Everyone under any warning in force, a regional total, a national total: taken over the distinct cells the areas cover (`union_total`). Never a sum of area totals.
3. **Each area keeps its own figure, labelled gross**: everything inside it, which may also sit in another warning area. It is right for "how many people are in this area". The warning areas table shows it with "includes people also in N other warning areas", and the pair table is one click away.
4. **Where figures must add up across warning areas**, the default is an **equal split**: each cell is shared equally among the warning areas that cover it, so the parts sum to the union total. That treats overlapping warnings alike, which suits areas with no natural hierarchy. An optional **primary area** rule is kept (in force first, then the higher severity, then the smaller area, then the lowest code), and a source-aware rule is possible where the hazard bands say which source drives a cell (fluvial or coastal).

**By level in force.** Each cell takes the highest in-force level covering it. The figures for severe, warning and alert are additive and sum to the union.

The same rule applies to the forecast polygons, which overlap by source and day: a cell takes the worst band, and totals are over distinct cells. Code and tests: `utils/grid100.py` (`covered_cells`, `union_total`, `overlap_depth`, `pair_overlaps`, `primary_area`, `area_overlap_stats`).

## The score

`score = 100 * hazard * (w_pop * population_percentile + w_vuln * deprivation + w_crit * critical)`, with hazard as the **rivers and sea** band divided by four. Surface water is left out by default, because warning and alert areas are issued for rivers, the sea and tidal waters, not for surface water (`config.RISK_SCORE_INCLUDE_SURFACE`; set it to True to use the worse of the two). No hazard means no score. Defaults: population 0.4, vulnerability 0.3, critical sites 0.3 (normalised). Critical sites beyond three in one cell add nothing more. This is a judgement, so the weights are one setting and the method is written down.

## Inputs

| Layer | Source | How it arrives | Status |
|---|---|---|---|
| Output area boundaries, population-weighted centroids, OA to LSOA lookup | ONS Open Geography Portal | Download by the notebook | Format to check on first run |
| Population, age, households (Census 2021, output area) | ONS via Nomis bulk tables | Download by the notebook | Format to check on first run |
| Deprivation | English Indices of Deprivation 2025 (MHCLG, published 30 October 2025, 33,755 LSOAs) | Download by the notebook | Not comparable with 2019 |
| Risk of Flooding from Rivers and Sea, from Surface Water (NaFRA2, January 2025) | Environment Agency | Governed table or volume file, by widget | **Format unknown**: vector or raster, to be found by the inspect step |
| Flood Zones 2 and 3 | Environment Agency (Flood Map for Planning; only some products are open) | Governed table or volume file | Licence to confirm |
| Address points | ONS National Statistics UPRN Lookup (NSUL, Epoch 121, October 2025): UPRN, grid reference, 2021 output area, local authority, police force area, by region file | Governed volume, readable now | **Used.** England region files only |
| Critical sites (interim) | OpenStreetMap UK (`.osm.pbf`), DfE Get Information About Schools, NHS ODS API | Governed volume and downloads | **Used until AddressBase access arrives.** Coverage is uneven, see below |
| Critical sites and residential split (target) | OS AddressBase Premium classification codes (restricted) | Governed volume, restricted | **Major improvement, blocked.** No read access yet, see below |
| Receptors | EA National Receptor Database | Governed table | Not found in the survey; optional |
| Properties at risk | NaFRA2 receptor counts or the National Receptor Database | Governed table | Which table |
| Hospitals, GP surgeries | NHS Organisation Data Service | Download | Needs postcodes turned into coordinates (ONS postcode directory) |
| Care homes | CQC directory | Download or API | Coordinates supplied |
| Schools, colleges | DfE Get Information About Schools | Download | Coordinates supplied |
| Fire, police, ambulance stations, prisons | Mixed public sources | To be chosen | Open sources are patchy: check completeness |
| Electricity substations, water and sewage works, COMAH sites | Mixed; possibly EA internal | Governed table if you have it | **Needed from you** |
| Combined authority, local authority, police and fire areas | ONS Open Geography Portal | Download | Local resilience forums: source not yet found |

## Major improvement pending: AddressBase Premium

**Status: access denied.** The survey returned `PERMISSION_DENIED ... does not have READ VOLUME on Volume 'prd_dash_bronze.ordnance_survey_data_hub_restricted.addressbase_premium'`. Access needs the Access Approver's agreement and a MyIT "Access to restricted data" request, and will take a while. The pipeline is built to work without it and to switch over when it arrives.

**What it would add, and why it matters:**
- **Critical sites from one national, consistent source.** The classification table (about 45 million rows) marks hospitals, surgeries, care homes, schools, nurseries, police, fire and ambulance stations, prisons, substations and water works. The interim sources below are patchier, and this is the largest single gain in quality.
- **A residential and non-residential split.** Census people are shared over homes only, rather than over every NSUL address, and "homes at risk" becomes a true count.
- **Currency.** NSUL is Epoch 121 (October 2025) and the AddressBase copy is newer.

**What the interim sources cost:**
- OpenStreetMap is crowdsourced. Schools and hospitals are well covered, substations and water works less so, and care homes and some emergency sites are undercounted.
- With NSUL alone, a non-residential address (a shop, an office) counts as a place people live, so population is spread a little too widely in town centres.
- Every output carries its source in a `source` column, so interim and AddressBase figures are never mixed unseen.

**How the switch works:** job 31 reads a `critical_source` widget (`interim` or `addressbase`). The `critical_sites` table has the same columns either way, so jobs 33 to 35 and the app do not change. Job 33 takes a residential filter from AddressBase when it is available.

## First step: survey what the workspace holds

`explore/Explore_exposure_inputs.ipynb` is read only. It tests which public sites the cluster can reach, searches the catalogue for governed tables that look like the datasets above, and lists the volumes. Its three result tables decide how jobs 29 to 32 are built.

## Jobs and order

Each is a notebook under `jobs/`, manual or on a slow cadence. Address-level tables go in the restricted schema; aggregates in the unrestricted one.

1. **29 load_census_deprivation** (built, not yet run): Census 2021 counts by output area (Nomis bulk tables ts001, ts007a, ts041) and the IoD2025 by LSOA. Writes `census2021_oa` and `imd2025_lsoa`. No boundaries are needed: with address points, the output area code comes with each point.
2. **30 build_hazard_rasters** (built, not yet run): the present-day NaFRA2 band polygons (rivers and sea) and Flood Zones 2 and 3 (surface water only when asked for, later) are painted onto 10 m rasters, one per 50 km tile, with the worst band winning at a pixel. It writes the rasters to a volume (job 33 reads a band at each address from them) and `hazard_cells_100m`, the share of each 100 m cell in each band. Only the four-band and extent files are used; the depth and hazard files and the Climate_Change folders are skipped for now. A point's band is within 7 m of the polygon edge. The source layout was confirmed by the survey: polygons with a `Risk_band` of High, Medium, Low or Very low, in British National Grid, tiled by OS grid reference.
3. **31 build_critical_sites** (built, not yet run): one `critical_sites` table in the restricted schema from interim public sources, each optional and failing on its own: open schools and colleges from DfE Get Information About Schools; NHS trust sites and GP practices from the NHS ODS API, placed by postcode through the ONS Postcode Directory; and from the OpenStreetMap UK file in the lake, emergency services (police, fire, ambulance, coastguard and lifeboat stations), prisons, care homes, substations of 66 kV or more, power stations, sewage and water works, plus hospitals, clinics, colleges and nurseries the other lists lack. The same place from two sources is kept once (DfE and NHS win), sites outside England are dropped, and the rivers and sea band and Flood Zone rank at each site is read from the job 30 rasters. Gaps, stated up front: hazardous (COMAH) sites, and a complete care home list (CQC was unavailable). AddressBase Premium classification would replace all of it. Job 34 counts the table per cell.
4. **32 build_responder_geographies** (run in Databricks for the names; the fire and combined authority routes checked on a local Spark): names and flood exposure for the geographies responders work to. For the geographies NSUL carries (local authority district, county or unitary authority, ward, constituency, police force area, region) it finds the names in the NSUL reference folder (361 districts, 30 counties, 8,407 wards, 650 constituencies, 47 police force areas, 9 regions), writes `responder_geography_names`, and adds the address points up by each code into `geography_flood_exposure` (people, households, over-65s, people by rivers and sea band and Flood Zone, critical sites). **Combined authorities:** the ONS "Local Authority District to Combined Authority (May 2025) Lookup in EN" CSV (widget `combined_authority_csv`), rolled up from the districts; any district in none is reported. **Fire and rescue authorities:** the ONS "Fire and Rescue Authorities (December 2024) Boundaries EW BFC" GeoJSON (widget `fra_geojson`); each output area's centre is placed in an authority (the nearest within 2 km if it falls just outside a boundary clipped to the coast) and the address points are added up by output area, which is exact where an authority boundary cuts through a district. Still open: critical sites per geography other than district and fire authority, and a suppressed version for the app, since small wards hold few people. Local resilience forums are out of scope for now.
5. **33 build_address_points** (built, not yet run): every England UPRN from NSUL with its output area, LSOA, local authority, county or unitary authority, ward, constituency, police force and region, its equal share of its output area's Census people, households and over-65s, its IoD2025 decile, and the flood band at its position read from the job 30 rasters: rivers and sea, and the Flood Zone rank split into coastal (sea), fluvial (river) and mapped together. Output areas with people but no UPRN get one point at their centre, so totals are conserved. Built tile by tile and resumable. Restricted schema. Which warning and alert areas each point lies in is job 35's link table, so this job does not depend on the warning polygons. Known limit: NSUL holds businesses as well as homes, so people are shared a little too widely in town centres until AddressBase classification is available.
6. **34 build_exposure_grid** (built, not yet run): `exposure_grid_100m`, one row per 100 m cell that has people, a flood band or a critical site. Per cell: people, households, over-65s, addresses and people-weighted deprivation from the address points; people and households by rivers and sea band, and people by Flood Zone split coastal, fluvial and mapped together; the share of the cell's area in each band from job 30; `haz_rs`, the area-weighted band from 0 to 4 (a quarter High and the rest dry is 1.0, never 4.0); critical sites by group if job 31 has run; a national population percentile and the composite score from the weights in `config.RISK_SCORE_WEIGHTS`, rivers and sea only. The layers are always stored and the score is a ranking aid made from them. Restricted schema, because it is built from address-level data; a suppressed aggregate version for the app is a later step.
7. **35 build_warning_area_exposure** (built, not yet run): per flood warning area and flood alert area, counted from the address points with an exact point-in-polygon test (no 50 m cell edge). Areas overlap, so each measure (people, households, addresses, over-65s, people in the rivers and sea High or Medium band) is given four ways: gross (everyone in the area), unique (covered by this area alone), split (each person shared equally between the areas covering them, which adds up to the true total) and shared (gross minus unique). Warnings are counted against warnings and alerts against alerts. Also per area: people by rivers and sea band and by Flood Zone (coastal, fluvial, mapped together), people in the most deprived fifth, mean deprivation, critical sites by group, and what the area is for (fluvial, coastal, tidal, groundwater, from its code). Further tables: each pair of overlapping areas with what they share; each area split by constituency (from NSUL's constituency on every address); the distinct people covered by any warning or alert area against everyone, so the gap is visible; and per constituency, the people in High or Medium that no warning area covers. Rivers and sea only: no surface water. Areas that are lines or empty cannot hold addresses and are listed, not counted. Restricted schema; a suppressed version for the app is a later step.
8. The app: the Areas section, the extra lines in map popups, constituency figures and the Exports.

## What I need from you

- **AddressBase Premium access.** Email the Access Approver named in the Data Catalogue entry, then raise "Access to restricted data" in MyIT. Aggregates per warning area are assumed to be permitted; site-level and address-level data stay in the restricted schema regardless.
- The governed table names for NaFRA2, Flood Zones and any critical-site tables you hold.
- Whether the cluster can reach the ONS, Nomis, gov.uk, NHS and CQC sites. The survey notebook reports it.

## Known limits, stated up front

- With points: an output area's people are shared equally among its residential addresses, which ignores that homes differ in size. Without points: an even spread over cells, not building level.
- On the grid only: cell-centre ownership gives a 50 m boundary error. Point-based sums have none.
- The band thresholds assumed are the usual ones (High above 3.3 per cent chance in any year, Medium 1 to 3.3, Low 0.1 to 1, Very Low below 0.1). Confirm against the NaFRA2 documentation.
- Interim critical site lists are incomplete for emergency services, utilities and care homes. AddressBase Premium access is the fix and is flagged above as the major improvement.
- Local resilience forums are out of scope for now.
- The score is a ranking aid, not a prediction.
