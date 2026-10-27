# Exposure and risk grid: plan

Status: plan and foundation only. Nothing here has run against real data. England only (Scotland and Wales are out of scope for now).

## What it gives you

For every flood warning area and flood alert area, and for every constituency:

- **People.** Population, households, people aged 65 and over, and deprivation (IoD2025), from Census 2021.
- **Risk zones.** How much of the area, and how many people, properties and critical sites, sit in each NaFRA2 band (High, Medium, Low, Very Low), for **rivers and the sea** and for **surface water** separately. Flood Zones 2 and 3 as a second view.
- **Critical sites.** Counts by group (health and care, education, emergency services and prisons, utilities and hazardous sites), and by risk band.
- **A gridded risk product.** A 100 m British National Grid layer holding all of the above per cell, with an adjustable composite score on top. The layers are always stored. The score is never the only thing kept.

## Decisions already made

| Decision | Choice |
|---|---|
| Grid | 100 m British National Grid. Cell key = (northing // 100) * 10,000 + (easting // 100). |
| Risk zones | NaFRA2 bands, rivers and sea and surface water kept separate. Flood Zones 2 and 3 added as a second view. |
| Critical sites | All four groups: health and care, education, emergency services and prisons, utilities and hazardous sites. |
| Score | Layers first, plus a simple adjustable score. Weights in `config.RISK_SCORE_WEIGHTS`. |
| Big Environment Agency datasets | May already be governed tables, so notebooks read a table name or a file path from a widget, whichever you have. |
| Out of scope | Scotland and Wales observed data. |

## How a cell is owned

A polygon owns the cells whose **centre** is inside it. One rule is used for warning areas, risk bands and output areas, so every layer agrees. The error is at most half a cell (50 m) at a boundary. A polygon smaller than a cell still owns one cell, so no total is lost. Code: `utils/grid100.py` (tested on synthetic polygons).

- **Population and households.** Each output area's total is spread evenly over the cells it owns. Totals are conserved. This is an even spread, not a building-level one: in a large rural output area the people are smeared across empty cells. That is a known limit and is stated in the output table comment.
- **Deprivation.** The LSOA decile (1 is most deprived) is given to every cell of the LSOA. Reported for an area as the population-weighted mean decile and the share of people in the most deprived 20 per cent of LSOAs.
- **Hazard bands.** Band polygons (or raster cells) are put on the grid. Where they overlap, the worse band wins. Stored as `haz_rs` and `haz_sw`, each 0 (none) to 4 (High).
- **Critical sites.** A point goes to the cell containing it. Stored as counts per group and in total.
- **Warning areas.** Each area owns its cells. Its figures are sums over those cells, optionally split by band.

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
| Properties at risk | NaFRA2 receptor counts or the National Receptor Database if you hold it | Governed table | **Needed from you**: which table |
| Hospitals, GP surgeries | NHS Organisation Data Service | Download | Needs postcodes turned into coordinates (ONS postcode directory) |
| Care homes | CQC directory | Download or API | Coordinates supplied |
| Schools, colleges | DfE Get Information About Schools | Download | Coordinates supplied |
| Fire, police, ambulance stations, prisons | Mixed public sources | To be chosen | Open sources are patchy: check completeness |
| Electricity substations, water and sewage works, COMAH sites | Mixed; possibly EA internal | Governed table if you have it | **Needed from you** |
| Combined authority, local authority, police and fire areas | ONS Open Geography Portal | Download | Local resilience forums: source not yet found |

## Jobs and order

Each is a notebook under `jobs/`, manual or run on a slow cadence, writing Delta tables in the unrestricted schema.

1. **29 load_census_deprivation**: output areas, Census 2021 counts, IoD2025.
2. **30 load_hazard_layers**: NaFRA2 and Flood Zones into a common band-by-cell table. Starts with an inspect step that prints the layers, columns and sizes, so I can adapt to what you hold.
3. **31 load_critical_sites**: one standard `critical_sites` table (site id, group, type, name, coordinates, source).
4. **32 load_responder_geographies**: combined authority, local authority, police and fire areas, and their lookups to constituencies and warning areas.
5. **33 build_grid_100m**: the `exposure_grid_100m` table: one row per cell that holds anything.
6. **34 warning_area_exposure**: `ea_warning_area_exposure` and a constituency roll-up, by band and source.
7. The app: the stats on the map popups and a table of warning areas, a section of the Exports tab, and constituency figures.

## What I need from you

- The governed table names for NaFRA2, Flood Zones, properties at risk and any critical-site tables you hold, if they exist.
- Whether the cluster can reach the ONS, Nomis, gov.uk, NHS and CQC sites. Job 29 prints which it could not reach.
- A cluster big enough for the build. 100 m is about 13 million cells for England, but only cells holding anything are stored.

## Known limits, stated up front

- Even spread of people over an output area, not building level.
- Cell-centre ownership: a 50 m boundary error.
- The band thresholds assumed are the usual ones (High above 3.3 per cent chance in any year, Medium 1 to 3.3, Low 0.1 to 1, Very Low below 0.1). Confirm against the NaFRA2 documentation.
- Public critical site lists are incomplete for emergency services and utilities.
- The score is a ranking aid, not a prediction.
