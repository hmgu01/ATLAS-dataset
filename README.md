# ATLAS — Spatial Layout Alternative Generation Model
| 📊 Overview | 🔵 2D Graph | 📐 3D View |
|:---:|:---:|:---:|
| ![Overview](assets/overview.png) | ![2D Graph](assets/2d.png) | ![3D View](assets/3d.png) |

🔗 **[Live Demo — Interactive Viewer](https://hmgu01.github.io/ATLAS-dataset/viewers/building_001_viewer.html)**
### Dataset & Visualization Tools

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19225225.svg)](https://doi.org/10.5281/zenodo.19225225)
[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC_BY--NC_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
![Buildings](https://img.shields.io/badge/sample%20buildings-30-blue)
![Schema](https://img.shields.io/badge/node%20classes-25-blue)

> **ATLAS** is a spatial relationship dataset of multi-tenant neighborhood
> commercial buildings (근린생활시설) in South Korea, represented as
> attributed relational graphs for use in generative layout models.

---

## Data Availability

This repository contains **30 sample buildings** from the full ATLAS dataset
(**196 buildings** total). The complete dataset is available upon reasonable
request to the corresponding author.

---

## Dataset Overview

Each building is encoded as two graphs:

| Graph | Description |
|---|---|
| **Horizontal** | Intra-floor adjacency between spaces (weighted edges, w ∈ {0, 0.5, 1.0}) |
| **Vertical** | Inter-floor continuity (w=1.0 physical shaft; w=0 programmatic cluster) |

### Node Classes (25 types)

| ID | Class | ID | Class |
|---|---|---|---|
| 1 | Leasable Unit | 14 | Secondary Road |
| 2 | Main Road | 16 | Open Space |
| 3 | Corridor | 17 | Green Space |
| 4 | Hall / Lobby | 18 | Surrounding Mass |
| 5 | Vestibule | 19 | Parking |
| 6 | Hall | 20 | Mechanical Room |
| 7 | MEP | 21 | Disaster Prevention |
| 8 | Elevator | 22 | Electrical Room |
| 9 | Staircase | 23 | PIT |
| 10 | Restroom | 24 | Sunken Court |
| 11 | Ramp | 25 | Dry Area (DA) |
| 12 | Storage | — | — |
| 13 | Terrace | — | — |

External context nodes (class 2, 14, 16, 17, 18) are attached to each floor
graph to encode urban boundary conditions around the building.

---

## Repository Structure

```
ATLAS-dataset/
├── data/                         ← JSON dataset (30 sample buildings)
│   ├── building_001/
│   │   ├── horizontal_graph.json
│   │   ├── vertical_graph.json
│   │   ├── area_ratios.json
│   │   └── metadata.json
│   └── building_030/ …
│
├── viewers/                      ← Self-contained HTML viewers
│   ├── building_001_viewer.html  ← Open in any browser, no server needed
│   └── …
│
├── scripts/
│   ├── excel_to_json.py          ← Excel → JSON converter
│   ├── json_to_viewer.py         ← JSON → interactive HTML viewer (3-tab)
│   ├── json_to_figures.py        ← JSON → publication PNG figures
│   ├── json_to_appendix.py       ← JSON → appendix PDF
│   └── atlas_validate.py         ← Dataset quality checker
│
├── README.md
├── CITATION.cff
└── LICENSE
```

---

## Quick Start

### 1. View a building interactively

Download any `viewers/building_NNN_viewer.html` and open in a browser.
No server or internet required — all data is self-contained.

| Tab | Content |
|---|---|
| 📊 Overview | Floor table · class distribution · vertical core summary |
| 🔵 2D Graph | Force-directed graph per floor with N/S/E/W compass layout |
| 📐 3D View | Exploded axonometric with context layers (open space → road → surrounding mass) |

### 2. Convert your own Excel data

```bash
pip install openpyxl networkx

# Single building
python scripts/excel_to_json.py --input my_building.xlsx --output data/

# Batch + anonymize
python scripts/excel_to_json.py --batch --input xlsx/ --output data/ --anonymize
```

### 3. Generate viewer & figures

```bash
pip install matplotlib networkx reportlab Pillow

# HTML viewer (single)
python scripts/json_to_viewer.py --input data/building_001/ --output viewers/

# HTML viewer (batch, Windows)
for /d %d in (data\*) do python scripts/json_to_viewer.py --input %d --output viewers/

# Publication PNG (300 dpi)
python scripts/json_to_figures.py --input data/building_001/ --output figures/ --dpi 300

# Appendix PDF (batch, Windows)
for /d %d in (data\*) do python scripts/json_to_appendix.py --input %d --output appendix/
```

### 4. Validate dataset quality

```bash
python scripts/atlas_validate.py --input data/ --report validation_report.csv
```

**Status codes:**
- ✅ `OK` — ready to use
- ⚠️ `WARN` — minor issues (no EV chain, core-only floor) — usually normal
- ❌ `ERROR` — unknown class ID or missing files — requires Excel correction

---

## JSON Schema

### `horizontal_graph.json`

```json
[
  {
    "floor_key":   "building_001+1",
    "floor_label": "1",
    "num_nodes":   20,
    "num_edges":   28,
    "nodes": [
      {
        "id":                  "임대_1",
        "class_id":            1,
        "class_name_en":       "Leasable Unit",
        "instance_index":      1,
        "is_external_context": false,
        "is_vertical_core":    false,
        "face":   null,
        "offset": null
      },
      {
        "id":                  "공지_1",
        "class_id":            16,
        "is_external_context": true,
        "face":   "S",
        "offset": 84.5
      }
    ],
    "edges": [
      { "source": "임대_1", "target": "복도_1", "weight": 0.5 }
    ]
  }
]
```

### Edge weight semantics

| Weight | Meaning |
|---|---|
| **1.0** | Strong connection (main circulation, entrance) |
| **0.5** | Standard adjacency |
| **0.0** | Programmatic grouping (no direct passage) |

### Vertical edge weight semantics

| Weight | Meaning |
|---|---|
| **1.0** | Physical shaft (EV, staircase) — mandatory continuity |
| **0.0** | Programmatic vertical cluster (same-type across floors) |

---

## Citation

```bibtex
@dataset{atlas_dataset_2026,
  author    = {Hyeongmo Gu and Seungyeon Choo},
  title     = {ATLAS: Spatial Relationship Dataset for Multi-Tenant
               Neighborhood Commercial Buildings},
  year      = {2026},
  publisher = {Zenodo},
  doi       = {10.5281/zenodo.19225225},
  url       = {https://github.com/hmgu01/ATLAS-dataset}
}
```

---

## License

This dataset is licensed under
[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).
Free for academic and non-commercial use with attribution.
For commercial use, contact the authors.

---

## Contact

Hyeongmo Gu — ghm3186@gmail.com
Advisor: Prof. Seungyeon Choo
Dept. School of Architecture, Kyungpook National University
