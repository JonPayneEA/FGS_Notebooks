# Exposure and risk grid: plan

Status: plan and foundation only. Nothing here has run against real data. England only (Scotland and Wales are out of scope for now).

## What it gives you

For every flood warning area and flood alert area, and for every constituency:

- **People.** Population, households, people aged 65 and over, and deprivation (IoD2025), from Census 2021.
- **Risk zones.** How much of the area, and how many people, properties and critical sites, sit in each NaFRA2 band (High, Medium, Low, Very Low), for **rivers and the sea** and for **surface water** separately. Flood Zones 2 and 3 as a second view.
- **Critical sites.** Counts by group (health and care, education, emergency services and prisons, utilities and hazardous sites), and by risk band.
- **A gridded risk product.** A 100 m British National Grid layer holding all of the above per cell, with an adjustable composite score on top. The layers are always stored. The score is never the only thing kept.

## What it will look like

A mock-up with invented numbers, in the app's design, is in `docs/EXPOSURE_MOCKUP.html` (open it in a browser). It shows a new Areas section with a sortable warning areas table, one page per area (key figures, the 100 m risk grid, a breakdown by band for rivers and sea and for surface water, and the critical sites in High or Medium bands), the extra lines in a forecast map popup, and the new exports. The flood bands use a blue ramp and the score a magenta one, so neither is mistaken for a forecast level.

## Decisions already made

| Decision | Choice |
|---|---|
| Method | **Points first, grid second.** Where address or receptor points are held, the sums are made from the points (exact point in area, band looked up at the point). The 100 m British National Grid is the aggregation and product layer, and carries the layers that have no points. |
| Grid | 100 m British National Grid. Cell key = (northing // 100) * 10,000 + (easting // 100). |
| Risk zones | NaFRA2 bands, rivers and sea and surface water kept separate. Flood Zones 2 and 3 added as a second view. |
| Critical sites | All four groups: health and care, education, emergency services and prisons, utilities and hazardous sites. |
| Score | Layers first, plus a simple adjustable score. Weights in `config.RISK_SCORE_WEIGHTS`. |
| Big Environment Agency datasets | May already be governed tables, so notebooks read a table name or a file path from a widget, whichever you have. |
| Out of scope | Scotland and Wales observed data. |

## Units and how each number is made

**The unit of truth is a point where points exist, a cell where they do not.** Either way a person is counted once, so the overlap rules below apply to both.

**With address or receptor points** (OS AddressBase, the ONS UPRN directory, the EA National Receptor Database, subject to your licence):
- **People and households.** Each output area's Census totals are shared among the residential address points inside it (`allocate_to_points`), so people sit where the buildings are. An output area with totals but no residential point is reported as unallocated and placed over its cells instead.
- **Warning areas.** Exact point in polygon (`points_in_areas`). No grid, no 50 m error. This matters most for small areas.
- **Flood bands.** Looked up at the point, separately for rivers and sea and for surface water.
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

`score = 100 * hazard * (w_pop * population_percentile + w_vuln * deprivation + w_crit * critical)`, with hazard as the worse of the two bands divided by four. No hazard means no score. Defaults: population 0.4, vulnerability 0.3, critical sites 0.3 (normalised). Critical sites beyond three in one cell add nothing more. This is a judgement, so the weights are one setting and the method is written down.

## Inputs

| Layer | Source | How it arrives | Status |
|---|---|---|---|
| Output area boundaries, population-weighted centroids, OA to LSOA lookup | ONS Open Geography Portal | Download by the notebook | Format to check on first run |
| Population, age, households (Census 2021, output area) | ONS via Nomis bulk tables | Download by the notebook | Format to check on first run |
| Deprivation | English Indices of Deprivation 2025 (MHCLG, published 30 October 2025, 33,755 LSOAs) | Download by the notebook | Not comparable with 2019 |
| Risk of Flooding from Rivers and Sea, from Surface Water (NaFRA2, January 2025) | Environment Agency | Governed table or volume file, by widget | **Format unknown**: vector or raster, to be found by the inspect step |
| Flood Zones 2 and 3 | Environment Agency (Flood Map for Planning; only some products are open) | Governed table or volume file | Licence to confirm |
| Address or receptor points | OS AddressBase, the ONS UPRN directory (which gives each UPRN its output area and LSOA), or the EA National Receptor Database | Governed table, restricted schema | **You believe you have access.** Which tables, and the licence terms, to confirm |
| Properties at risk | NaFRA2 receptor counts or the National Receptor Database | Governed table | Which table |
| Hospitals, GP surgeries | NHS Organisation Data Service | Download | Needs postcodes turned into coordinates (ONS postcode directory) |
| Care homes | CQC directory | Download or API | Coordinates supplied |
| Schools, colleges | DfE Get Information About Schools | Download | Coordinates supplied |
| Fire, police, ambulance stations, prisons | Mixed public sources | To be chosen | Open sources are patchy: check completeness |
| Electricity substations, water and sewage works, COMAH sites | Mixed; possibly EA internal | Governed table if you have it | **Needed from you** |
| Combined authority, local authority, police and fire areas | ONS Open Geography Portal | Download | Local resilience forums: source not yet found |

## First step: survey what the workspace holds

`explore/Explore_exposure_inputs.ipynb` is read only. It tests which public sites the cluster can reach, searches the catalogue for governed tables that look like the datasets above, and lists the volumes. Its three result tables decide how jobs 29 to 32 are built.

## Jobs and order

Each is a notebook under `jobs/`, manual or on a slow cadence. Address-level tables go in the restricted schema; aggregates in the unrestricted one.

1. **29 load_census_deprivation**: output areas, Census 2021 counts, IoD2025.
2. **30 load_hazard_layers**: NaFRA2 and Flood Zones, with the sampling that gives the share of a cell in each band.
3. **31 load_critical_sites**: one standard `critical_sites` table (site id, group, type, name, coordinates, source).
4. **32 load_responder_geographies**: combined authority, local authority, police and fire areas, and lookups.
5. **33 build_address_points**: residential address points with output area, LSOA, deprivation decile, each warning area and flood band they fall in, and their share of the Census totals. Restricted schema.
6. **34 build_grid_100m**: the `exposure_grid_100m` table, from the points plus the continuous layers.
7. **35 warning_area_exposure**: `ea_warning_area_exposure` (gross and equal split), the overlap depth and pair tables, and the constituency roll-up, by band and source.
8. The app: the Areas section, the extra lines in map popups, constituency figures and the Exports.

## What I need from you

- **Which point datasets you can use**, and the table names: addresses (AddressBase or the UPRN directory) and receptors (the National Receptor Database). The survey notebook searches for these names. Please also confirm the licence allows a derived exposure grid and area statistics to be shared inside the Agency.
- The governed table names for NaFRA2, Flood Zones and any critical-site tables you hold.
- Whether the cluster can reach the ONS, Nomis, gov.uk, NHS and CQC sites. The survey notebook reports it.

## Known limits, stated up front

- With points: an output area's people are shared equally among its residential addresses, which ignores that homes differ in size. Without points: an even spread over cells, not building level.
- On the grid only: cell-centre ownership gives a 50 m boundary error. Point-based sums have none.
- The band thresholds assumed are the usual ones (High above 3.3 per cent chance in any year, Medium 1 to 3.3, Low 0.1 to 1, Very Low below 0.1). Confirm against the NaFRA2 documentation.
- Public critical site lists are incomplete for emergency services and utilities.
- The score is a ranking aid, not a prediction.
