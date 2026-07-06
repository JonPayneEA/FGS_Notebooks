# DASH work plan

## Summary

Four dashboards organise everything this plan covers: an FGS intelligence tool (built), an AI testing hub, a post-event performance view, and an impact intelligence view. Each has a fixed list of required data, checked against what already exists. Fifteen items remain to build across the three unfinished dashboards, sequenced in one order at the end of this document. Two need decisions before build work can proceed: the Met Office forecast licence, and the scope of NAFRA2.

## Why this plan exists

The FGS pipeline and the work that followed it grew inside DASH the way projects do when the destination isn't fixed at the start. A source became useful, it was pulled in, and its purpose settled later. That built capability quickly. It also made a simple question hard to answer: for any given aim, what data does it need, and do we already hold it?

An unanswerable requirements question has a cost. Ingest work gets duplicated because nobody knew the data was already in Bronze. Gaps surface during build, when they're expensive, rather than during planning, when they're cheap. This plan closes the question by naming four destinations and tracing exactly what each one needs.

```mermaid
flowchart LR
  subgraph BEFORE["How the work grew"]
    direction TB
    S1[EA APIs] --> P((data collected<br/>as opportunities arose))
    S2[Met Office feeds] --> P
    S3[ONS and Parliament] --> P
    S4[Internal imports] --> P
    S5[National Data Library] --> P
  end
  P -.-> Q{{"For any given aim:<br/>what data does it need,<br/>and do we already hold it?"}}
  subgraph AFTER["What this plan fixes"]
    direction TB
    Q -.-> A["Four named destinations,<br/>each with a checkable<br/>list of requirements"]
  end
  classDef unclear fill:#ffe3e3,stroke:#c92a2a,stroke-dasharray:5 3,color:#1a1a1a
  classDef fixed fill:#d3f9d8,stroke:#2f9e44,color:#1a1a1a
  class Q unclear
  class A fixed
  style BEFORE fill:none,stroke:#adb5bd,stroke-dasharray:2 4
  style AFTER fill:none,stroke:#adb5bd,stroke-dasharray:2 4

```

## How the four fit together

The four dashboards share foundations. Gauge readings feed both the AI hub and the post-event view. The flood warning and alert area polygons feed both the FGS tool and the impact view. The EA Real-Time Flood Monitoring API sits behind all four. Building each dashboard as a separate project would mean building some of this twice.

```mermaid
flowchart LR
  subgraph SHARED["Shared foundations (build once)"]
    direction TB
    EA_API["EA Real-Time<br/>Flood Monitoring API"]
    RT["River and rain<br/>gauge readings"]
    AREAS["Flood warning and<br/>alert area polygons"]
    MET["Met Office forecasts<br/>(licence unresolved)"]
  end

  FGS{{"FGS Dashboard<br/><i>FGS intelligence</i>"}}
  AIFFM{{"AI FFM Dashboard<br/><i>AI testing hub</i>"}}
  EVENT{{"Event Performance Dashboard<br/><i>post-event insight</i>"}}
  IMPACT{{"Impact Intelligence Dashboard<br/><i>what warnings protect</i>"}}

  EA_API --> RT
  EA_API --> AREAS
  AREAS --> FGS
  AREAS --> IMPACT
  RT --> AIFFM
  RT --> EVENT
  MET --> AIFFM
  MET --> EVENT

  classDef dashboard fill:#d0ebff,stroke:#1971c2,stroke-width:2px,color:#1a1a1a
  classDef shared fill:#fff3bf,stroke:#e8a300,color:#1a1a1a
  classDef risk fill:#ffe3e3,stroke:#c92a2a,stroke-dasharray:5 3,color:#1a1a1a
  class FGS,AIFFM,EVENT,IMPACT dashboard
  class EA_API,RT,AREAS shared
  class MET risk
  style SHARED fill:none,stroke:#adb5bd,stroke-dasharray:2 4

```

One shared foundation carries a risk worth owning now rather than discovering later. The Met Office forecast feeds (MOGREPS-UK and UKV, drawn from AWS Open Data) sit under a non-commercial licence the Met Office does not support for operational use. Two of the four dashboards depend on them. Until the licence position is resolved, neither can be called deliverable, whatever else gets built.

## FGS Dashboard: FGS intelligence tool

Built. Every table in its chain is live: the FGS risk polygons, their intersections against EA flood warning and alert areas, and their intersections against parliamentary constituencies. The constituency work exists because of the Director-level requirement to notify MPs when a Flood Warning is issued in their constituency. The remaining work is operational: keeping the pipeline running.

```mermaid
flowchart LR
  subgraph SRC["Sources"]
    direction TB
    EA_Real_Time_Flood_Monitoring_API["EA Real-Time Flood Monitoring API"]
    FFC_API_v3["FFC API v3"]
    ONS_Open_Geography_Portal["ONS Open Geography Portal"]
  end
  subgraph TBL["Tables"]
    direction TB
    ea_flood_warning_areas["ea_flood_warning_areas"]
    ea_flood_alert_areas["ea_flood_alert_areas"]
    parliamentary_constituencies["parliamentary_constituencies"]
    ea_area_constituency_lookup["ea_area_constituency_lookup"]
    fgs_statements["fgs_statements"]
    fgs_risk_polygons["fgs_risk_polygons"]
    fgs_constituency_intersections["fgs_constituency_intersections"]
    fgs_ea_area_intersections["fgs_ea_area_intersections"]
  end
  FGS_Dashboard{{"FGS Dashboard"}}
  FFC_API_v3 --> fgs_statements
  EA_Real_Time_Flood_Monitoring_API --> ea_flood_warning_areas
  EA_Real_Time_Flood_Monitoring_API --> ea_flood_alert_areas
  ONS_Open_Geography_Portal --> parliamentary_constituencies
  fgs_statements --> fgs_risk_polygons
  fgs_risk_polygons --> fgs_ea_area_intersections
  ea_flood_warning_areas --> fgs_ea_area_intersections
  ea_flood_alert_areas --> fgs_ea_area_intersections
  fgs_risk_polygons --> fgs_constituency_intersections
  parliamentary_constituencies --> fgs_constituency_intersections
  fgs_ea_area_intersections --> FGS_Dashboard
  fgs_constituency_intersections --> FGS_Dashboard
  ea_flood_warning_areas --> ea_area_constituency_lookup
  ea_flood_alert_areas --> ea_area_constituency_lookup
  parliamentary_constituencies --> ea_area_constituency_lookup
  ea_area_constituency_lookup --> FGS_Dashboard
  class EA_Real_Time_Flood_Monitoring_API done
  class FFC_API_v3 done
  class ONS_Open_Geography_Portal done
  class ea_flood_warning_areas done
  class ea_flood_alert_areas done
  class parliamentary_constituencies done
  class ea_area_constituency_lookup done
  class fgs_statements done
  class fgs_risk_polygons done
  class fgs_constituency_intersections done
  class fgs_ea_area_intersections done
  class FGS_Dashboard dashboard
  style SRC fill:none,stroke:#adb5bd,stroke-dasharray:2 4
  style TBL fill:none,stroke:#adb5bd,stroke-dasharray:2 4
  classDef done fill:#d3f9d8,stroke:#2f9e44,color:#1a1a1a
  classDef backlog fill:#fff3bf,stroke:#e8a300,stroke-dasharray:5 3,color:#1a1a1a
  classDef dashboard fill:#d0ebff,stroke:#1971c2,stroke-width:2px,color:#1a1a1a
  classDef sourceNode fill:#f1f3f5,stroke:#868e96,color:#1a1a1a
```

**Built**
`EA Real-Time Flood Monitoring API`, `FFC API v3`, `ONS Open Geography Portal`, `ea_flood_warning_areas`, `ea_flood_alert_areas`, `parliamentary_constituencies`, `ea_area_constituency_lookup`, `fgs_statements`, `fgs_risk_polygons`, `fgs_constituency_intersections`, `fgs_ea_area_intersections`.

**Still to build, in dependency order**
Nothing. This chain is complete.

## AI FFM Dashboard: AI testing hub

A review surface for one question: are the LSTM model runs any good, and what were they trained on. Three training sets and three rainfall inputs feed a single model-output table, `lstm_runs`, which the dashboard reads.

`lstm_runs` sits in the restricted schema. Model outputs of this kind carry access implications that a permissive default would understate, and the source records currently disagree with themselves on this point. The source needs correcting to match.

```mermaid
flowchart LR
  subgraph SRC["Sources"]
    direction TB
    AWS_Open_Data["AWS Open Data"]
    National_Data_Library["National Data Library"]
    EA_Real_Time_Flood_Monitoring_API["EA Real-Time Flood Monitoring API"]
  end
  subgraph TBL["Tables"]
    direction TB
    CAMELS_GB["CAMELS-GB"]
    CAMELS_GB_V2["CAMELS-GB V2"]
    UKFlow_15["UKFlow-15"]
    ea_rt_readings_bronze["ea_rt_readings_bronze"]
    met_office_uk_deterministic["met_office_uk_deterministic"]
    met_office_uk_ensemble["met_office_uk_ensemble"]
    lstm_runs["lstm_runs<br/><i>restricted</i>"]
  end
  AI_FFM_Dashboard{{"AI FFM Dashboard"}}
  EA_Real_Time_Flood_Monitoring_API --> ea_rt_readings_bronze
  AWS_Open_Data --> met_office_uk_deterministic
  AWS_Open_Data --> met_office_uk_ensemble
  National_Data_Library --> CAMELS_GB
  National_Data_Library --> CAMELS_GB_V2
  National_Data_Library --> UKFlow_15
  CAMELS_GB --> lstm_runs
  CAMELS_GB_V2 --> lstm_runs
  UKFlow_15 --> lstm_runs
  ea_rt_readings_bronze --> lstm_runs
  met_office_uk_deterministic --> lstm_runs
  met_office_uk_ensemble --> lstm_runs
  lstm_runs --> AI_FFM_Dashboard
  class AWS_Open_Data done
  class National_Data_Library done
  class EA_Real_Time_Flood_Monitoring_API done
  class CAMELS_GB done
  class CAMELS_GB_V2 done
  class UKFlow_15 backlog
  class ea_rt_readings_bronze done
  class met_office_uk_deterministic backlog
  class met_office_uk_ensemble backlog
  class lstm_runs backlog
  class AI_FFM_Dashboard dashboard
  style SRC fill:none,stroke:#adb5bd,stroke-dasharray:2 4
  style TBL fill:none,stroke:#adb5bd,stroke-dasharray:2 4
  classDef done fill:#d3f9d8,stroke:#2f9e44,color:#1a1a1a
  classDef backlog fill:#fff3bf,stroke:#e8a300,stroke-dasharray:5 3,color:#1a1a1a
  classDef dashboard fill:#d0ebff,stroke:#1971c2,stroke-width:2px,color:#1a1a1a
  classDef sourceNode fill:#f1f3f5,stroke:#868e96,color:#1a1a1a
```

**Built**
`AWS Open Data`, `National Data Library`, `CAMELS-GB`, `CAMELS-GB V2`, `EA Real-Time Flood Monitoring API`, `ea_rt_readings_bronze`.

**Still to build, in dependency order**
- `UKFlow-15`
- `met_office_uk_deterministic`
- `met_office_uk_ensemble`
- `lstm_runs` (restricted)

## Event Performance Dashboard: post-event insight

The deepest chain of the four, and the only one owned jointly by two workstreams: Warning verification and Post-Event. It answers two questions after an event. How good were the warnings we issued, measured as lead time against false alarms? And how much uncertainty sat in the rainfall forecast at the catchment scale where operational decisions are actually made?

Nine items remain here, more than the other three dashboards combined. Most belong to the PDM rainfall chain, three tables deep before it produces anything usable. Two of its three uncertainty inputs trace back to the Met Office feeds, so the licence question above gates this dashboard as well as the AI hub.

```mermaid
flowchart LR
  subgraph SRC["Sources"]
    direction TB
    AWS_Open_Data["AWS Open Data"]
    EA_Real_Time_Flood_Monitoring_API["EA Real-Time Flood Monitoring API"]
    Internal_Import["Internal Import"]
  end
  subgraph TBL["Tables"]
    direction TB
    PDM_polygons["PDM_polygons"]
    ea_rt_readings_bronze["ea_rt_readings_bronze"]
    ea_thresholds_all["ea_thresholds_all<br/><i>restricted</i>"]
    ea_warning_history["ea_warning_history"]
    flood_warnings_log["flood_warnings_log"]
    fgs_warning_verification["fgs_warning_verification"]
    met_office_uk_deterministic["met_office_uk_deterministic"]
    met_office_uk_ensemble["met_office_uk_ensemble"]
    pdm_det["pdm_det"]
    pdm_ens["pdm_ens"]
    pdm_obs_rain["pdm_obs_rain"]
    pdm_uncertainty["pdm_uncertainty"]
  end
  Event_Performance_Dashboard{{"Event Performance Dashboard"}}
  EA_Real_Time_Flood_Monitoring_API --> ea_rt_readings_bronze
  EA_Real_Time_Flood_Monitoring_API --> flood_warnings_log
  Internal_Import --> ea_thresholds_all
  Internal_Import --> ea_warning_history
  ea_rt_readings_bronze --> fgs_warning_verification
  ea_thresholds_all --> fgs_warning_verification
  ea_warning_history --> fgs_warning_verification
  flood_warnings_log --> fgs_warning_verification
  AWS_Open_Data --> met_office_uk_deterministic
  AWS_Open_Data --> met_office_uk_ensemble
  Internal_Import --> PDM_polygons
  PDM_polygons --> pdm_det
  met_office_uk_deterministic --> pdm_det
  PDM_polygons --> pdm_ens
  met_office_uk_ensemble --> pdm_ens
  ea_rt_readings_bronze --> pdm_obs_rain
  pdm_det --> pdm_uncertainty
  pdm_ens --> pdm_uncertainty
  pdm_obs_rain --> pdm_uncertainty
  pdm_uncertainty --> Event_Performance_Dashboard
  fgs_warning_verification --> Event_Performance_Dashboard
  class AWS_Open_Data done
  class EA_Real_Time_Flood_Monitoring_API done
  class Internal_Import done
  class PDM_polygons backlog
  class ea_rt_readings_bronze done
  class ea_thresholds_all done
  class ea_warning_history backlog
  class flood_warnings_log done
  class fgs_warning_verification backlog
  class met_office_uk_deterministic backlog
  class met_office_uk_ensemble backlog
  class pdm_det backlog
  class pdm_ens backlog
  class pdm_obs_rain backlog
  class pdm_uncertainty backlog
  class Event_Performance_Dashboard dashboard
  style SRC fill:none,stroke:#adb5bd,stroke-dasharray:2 4
  style TBL fill:none,stroke:#adb5bd,stroke-dasharray:2 4
  classDef done fill:#d3f9d8,stroke:#2f9e44,color:#1a1a1a
  classDef backlog fill:#fff3bf,stroke:#e8a300,stroke-dasharray:5 3,color:#1a1a1a
  classDef dashboard fill:#d0ebff,stroke:#1971c2,stroke-width:2px,color:#1a1a1a
  classDef sourceNode fill:#f1f3f5,stroke:#868e96,color:#1a1a1a
```

**Built**
`AWS Open Data`, `EA Real-Time Flood Monitoring API`, `Internal Import`, `ea_rt_readings_bronze`, `ea_thresholds_all` (restricted), `flood_warnings_log`.

**Still to build, in dependency order**
- `PDM_polygons`
- `ea_warning_history`
- `fgs_warning_verification`
- `met_office_uk_deterministic`
- `met_office_uk_ensemble`
- `pdm_det`
- `pdm_ens`
- `pdm_obs_rain`
- `pdm_uncertainty`

## Impact Intelligence Dashboard: what warnings protect

The other three dashboards cover where the water goes and how well we predicted it. This one covers what's in its path: deprivation data, the national receptor database, and NAFRA2, intersected against the same warning and alert areas already live for the FGS tool. It turns a warning area from a polygon into a picture of who and what sits inside it.

One decision gates the build. NAFRA2 is broad, and its useful subset for this purpose is undecided. That decision comes first. Ingesting it whole would repeat, inside a single table, the exact collect-first-decide-later pattern this plan exists to end.

```mermaid
flowchart LR
  subgraph SRC["Sources"]
    direction TB
    EA_Real_Time_Flood_Monitoring_API["EA Real-Time Flood Monitoring API"]
    ONS_Open_Geography_Portal["ONS Open Geography Portal"]
    Internal_Import["Internal Import"]
  end
  subgraph TBL["Tables"]
    direction TB
    IMD_poly["IMD_poly"]
    NAFRA2_Data["NAFRA2_Data"]
    National_Receptor_Database["National_Receptor_Database"]
    ea_flood_warning_areas["ea_flood_warning_areas"]
    ea_flood_alert_areas["ea_flood_alert_areas"]
    alert_warn_impacts["alert_warn_impacts"]
  end
  Impact_Intelligence_Dashboard{{"Impact Intelligence Dashboard"}}
  EA_Real_Time_Flood_Monitoring_API --> ea_flood_warning_areas
  EA_Real_Time_Flood_Monitoring_API --> ea_flood_alert_areas
  ONS_Open_Geography_Portal --> IMD_poly
  Internal_Import --> National_Receptor_Database
  Internal_Import --> NAFRA2_Data
  IMD_poly --> alert_warn_impacts
  National_Receptor_Database --> alert_warn_impacts
  NAFRA2_Data --> alert_warn_impacts
  ea_flood_warning_areas --> alert_warn_impacts
  ea_flood_alert_areas --> alert_warn_impacts
  alert_warn_impacts --> Impact_Intelligence_Dashboard
  class EA_Real_Time_Flood_Monitoring_API done
  class ONS_Open_Geography_Portal done
  class Internal_Import done
  class IMD_poly backlog
  class NAFRA2_Data backlog
  class National_Receptor_Database backlog
  class ea_flood_warning_areas done
  class ea_flood_alert_areas done
  class alert_warn_impacts backlog
  class Impact_Intelligence_Dashboard dashboard
  style SRC fill:none,stroke:#adb5bd,stroke-dasharray:2 4
  style TBL fill:none,stroke:#adb5bd,stroke-dasharray:2 4
  classDef done fill:#d3f9d8,stroke:#2f9e44,color:#1a1a1a
  classDef backlog fill:#fff3bf,stroke:#e8a300,stroke-dasharray:5 3,color:#1a1a1a
  classDef dashboard fill:#d0ebff,stroke:#1971c2,stroke-width:2px,color:#1a1a1a
  classDef sourceNode fill:#f1f3f5,stroke:#868e96,color:#1a1a1a
```

**Built**
`EA Real-Time Flood Monitoring API`, `ONS Open Geography Portal`, `Internal Import`, `ea_flood_warning_areas`, `ea_flood_alert_areas`.

**Still to build, in dependency order**
- `IMD_poly`
- `NAFRA2_Data`
- `National_Receptor_Database`
- `alert_warn_impacts`

## Build sequence

All fifteen remaining items in one order. Shared dependencies appear once. Nothing is scheduled before its own inputs. The two decision gates (Met Office licence, NAFRA2 scope) sit outside this sequence and can be progressed in parallel with the early items, neither of which depends on them.

1. `IMD_poly`: Warning Reference, feeds Impact Intelligence Dashboard
2. `NAFRA2_Data`: Warning Reference, feeds Impact Intelligence Dashboard
3. `National_Receptor_Database`: Warning Reference, feeds Impact Intelligence Dashboard
4. `PDM_polygons`: Post-Event, feeds Event Performance Dashboard
5. `UKFlow-15`: AI Flood Forecasting, feeds AI FFM Dashboard
6. `alert_warn_impacts`: Warning Reference, feeds Impact Intelligence Dashboard
7. `ea_warning_history`: Warning verification, feeds Event Performance Dashboard
8. `fgs_warning_verification`: Warning verification, feeds Event Performance Dashboard
9. `met_office_uk_deterministic`: AI Flood Forecasting, feeds AI FFM Dashboard, Event Performance Dashboard
10. `met_office_uk_ensemble`: AI Flood Forecasting, feeds AI FFM Dashboard, Event Performance Dashboard
11. `lstm_runs` (restricted): AI Flood Forecasting, feeds AI FFM Dashboard
12. `pdm_det`: Post-Event, feeds Event Performance Dashboard
13. `pdm_ens`: Post-Event, feeds Event Performance Dashboard
14. `pdm_obs_rain`: Post-Event, feeds Event Performance Dashboard
15. `pdm_uncertainty`: Post-Event, feeds Event Performance Dashboard
