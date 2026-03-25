#!/usr/bin/env python3
"""
atlas_validate.py — ATLAS Dataset Quality Checker
Validates all JSON buildings in a dataset folder and reports issues.

Usage:
    python atlas_validate.py --input json_output/
    python atlas_validate.py --input json_output/ --verbose
    python atlas_validate.py --input json_output/ --report report.csv
"""

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


EXTERNAL_CLS   = {2, 14, 16, 17, 18}
CORE_CLS       = {8, 9}
VALID_CLS      = set(range(1, 26))  # 1~25: cls23=PIT, 24=썬큰, 25=DA
EXPECTED_FILES = {
    "horizontal_graph.json",
    "vertical_graph.json",
    "area_ratios.json",
    "metadata.json",
}

CLS_NAME = {
    1:"Leasable",2:"Main Road",3:"Corridor",4:"Vestibule",5:"Water Tank",
    6:"Hall",7:"MEP",8:"Elevator",9:"Staircase",10:"Restroom",
    11:"Management",12:"Storage",13:"Terrace",14:"Sec.Road",
    15:"Parking Entr.",16:"Open Space",17:"Green Space",
    18:"Surr.Mass",19:"Parking",20:"Mechanical",21:"Disaster Prev.",22:"Electrical",
    23:"PIT",24:"Sunken Court",25:"Dry Area",
}


# ── Validators ────────────────────────────────────────────────────────────────
def validate_building(bldg_dir: Path, verbose: bool = False) -> dict:
    """Run all checks on one building directory. Returns result dict."""
    issues   = []
    warnings = []
    info     = {}

    def err(msg):  issues.append(msg)
    def warn(msg): warnings.append(msg)

    # ── 1. File existence ─────────────────────────────────────────
    missing = EXPECTED_FILES - {f.name for f in bldg_dir.iterdir()}
    if missing:
        err(f"Missing files: {missing}")
        return _result(bldg_dir.name, issues, warnings, info)

    h = json.loads((bldg_dir/"horizontal_graph.json").read_text(encoding="utf-8"))
    v = json.loads((bldg_dir/"vertical_graph.json").read_text(encoding="utf-8"))
    a = json.loads((bldg_dir/"area_ratios.json").read_text(encoding="utf-8"))
    m = json.loads((bldg_dir/"metadata.json").read_text(encoding="utf-8"))

    # ── 2. Metadata consistency ───────────────────────────────────
    info["num_floors"] = m.get("num_floors", len(h))
    info["total_nodes"] = m.get("total_nodes", 0)

    if m.get("num_floors") != len(h):
        warn(f"metadata num_floors={m['num_floors']} but horizontal has {len(h)} floors")

    # ── 3. Per-floor checks ───────────────────────────────────────
    zero_area_nodes   = []
    missing_face_nodes= []
    no_leasable_floors= []
    unknown_cls_nodes = []
    isolated_nodes    = []

    for fl_data in h:
        raw   = fl_data["floor_label"]
        nodes = {n["id"]: n for n in fl_data["nodes"]}
        edges = fl_data["edges"]

        # Connected node IDs
        connected = set()
        for e in edges:
            connected.add(e["source"]); connected.add(e["target"])

        int_nodes = [n for n in fl_data["nodes"] if not n.get("is_external_context")]
        ext_nodes = [n for n in fl_data["nodes"] if n.get("is_external_context")]

        # 3a. Unknown class IDs
        for n in fl_data["nodes"]:
            if n["class_id"] not in VALID_CLS:
                unknown_cls_nodes.append(f"{raw}::{n['id']} (cls={n['class_id']})")

        # 3b. Zero area on internal non-external nodes
        area_floor = a["floors"].get(
            next((k for k in a["floors"] if a["floors"][k]["floor_label"]==raw), ""),
            {}).get("spaces", {})
        import re as _re
        for n in int_nodes:
            if n["class_id"] in EXTERNAL_CLS | {15, 19}: continue
            found_area = False
            # Match 1: exact normalized (spaces/underscores/dots removed)
            nid_clean = _re.sub(r'[\s_.]','', n["id"]).lower()
            for k in area_floor:
                k_clean = _re.sub(r'[\s_.]','', k).lower()
                if k_clean == nid_clean:
                    found_area = True
                    if area_floor[k]["area_sqm"] == 0:
                        zero_area_nodes.append(
                            f"{raw}::{n['id']} (cls={CLS_NAME.get(n['class_id'],n['class_id'])})")
                    break
            if found_area: continue
            # Match 2: class name prefix match
            # e.g. node '계단실_3' → area key '계단실6' (different numbering)
            cls_name_clean = _re.sub(r'[\s_.]','', n["class_name"]).lower()
            for k in area_floor:
                k_clean = _re.sub(r'[\s_.]','', k).lower()
                k_clean_base = _re.sub(r'\d+$','', k_clean)
                if k_clean_base == cls_name_clean:
                    found_area = True; break
                # Match 3: node id prefix (e.g. '테라스_3' → prefix '테라스')
                nid_base = _re.sub(r'[_\d]+$','', nid_clean)
                if nid_base and k_clean_base == nid_base:
                    found_area = True; break
            if not found_area:
                # cls 23,24,25 (PIT/썬큰/DA) 및 외부맥락성 공간은 면적 없어도 정상
                if n['class_id'] in {23, 24, 25}:
                    found_area = True  # 신규 클래스 - 면적 없어도 WARN 제외
                else:
                    zero_area_nodes.append(
                        f"{raw}::{n['id']} (cls={CLS_NAME.get(n['class_id'],n['class_id'])}, NO KEY)")

        # 3c. Missing face on external nodes
        for n in ext_nodes:
            if "face" not in n:
                missing_face_nodes.append(f"{raw}::{n['id']}")

        # 3d. No leasable unit on above-ground floors
        has_leasable = any(n["class_id"] == 1 for n in int_nodes)
        is_basement  = raw.startswith("B") or raw.startswith("b")
        if not has_leasable and not is_basement and int_nodes:
            no_leasable_floors.append(raw)

        # 3e. Isolated internal nodes (not connected to any edge)
        for n in int_nodes:
            if n["id"] not in connected and n["class_id"] not in CORE_CLS:
                isolated_nodes.append(f"{raw}::{n['id']}")

    # ── 4. Vertical graph checks ──────────────────────────────────
    v_edges = v.get("edges", [])
    mandatory = [e for e in v_edges if e["is_mandatory_continuity"]]
    has_ev_chain = any(
        e["source_class_id"]==8 or e.get("target_class_id")==8
        for e in mandatory)
    has_stair_chain = any(
        e["source_class_id"]==9 or e.get("target_class_id")==9
        for e in mandatory)
    if not has_ev_chain:
        warn("No mandatory EV vertical chain found")
    if not has_stair_chain:
        warn("No mandatory staircase vertical chain found")

    # ── 5. Summary ────────────────────────────────────────────────
    if zero_area_nodes:
        warn(f"{len(zero_area_nodes)} internal nodes with area=0 or missing area key: "
             + "; ".join(zero_area_nodes[:5])
             + (f" … +{len(zero_area_nodes)-5} more" if len(zero_area_nodes)>5 else ""))
    if missing_face_nodes:
        warn(f"{len(missing_face_nodes)} ext nodes without face field: "
             + "; ".join(missing_face_nodes[:3]))
    if no_leasable_floors:
        warn(f"Floors with no leasable unit: {no_leasable_floors}")
    if unknown_cls_nodes:
        err(f"Unknown class IDs: {unknown_cls_nodes[:5]}")
    if isolated_nodes:
        warn(f"{len(isolated_nodes)} isolated internal nodes: "
             + "; ".join(isolated_nodes[:3]))

    info.update({
        "zero_area_count":    len(zero_area_nodes),
        "missing_face_count": len(missing_face_nodes),
        "isolated_count":     len(isolated_nodes),
        "has_ev_chain":       has_ev_chain,
        "has_stair_chain":    has_stair_chain,
    })

    return _result(bldg_dir.name, issues, warnings, info)


def _result(name, issues, warnings, info):
    status = "ERROR" if issues else ("WARN" if warnings else "OK")
    return {
        "building":  name,
        "status":    status,
        "errors":    issues,
        "warnings":  warnings,
        "info":      info,
    }


# ── Reporter ──────────────────────────────────────────────────────────────────
def print_result(r, verbose: bool):
    icon = {"OK":"✅","WARN":"⚠️ ","ERROR":"❌"}[r["status"]]
    print(f"  {icon} {r['building']:40s}  [{r['status']}]")
    if verbose or r["status"] != "OK":
        for e in r["errors"]:   print(f"       ERROR: {e}")
        for w in r["warnings"]: print(f"       WARN : {w}")


def write_csv(results: list, path: Path):
    rows = []
    for r in results:
        rows.append({
            "building":          r["building"],
            "status":            r["status"],
            "num_floors":        r["info"].get("num_floors",""),
            "total_nodes":       r["info"].get("total_nodes",""),
            "zero_area_nodes":   r["info"].get("zero_area_count",""),
            "missing_face_nodes":r["info"].get("missing_face_count",""),
            "isolated_nodes":    r["info"].get("isolated_count",""),
            "has_ev_chain":      r["info"].get("has_ev_chain",""),
            "has_stair_chain":   r["info"].get("has_stair_chain",""),
            "errors":            " | ".join(r["errors"]),
            "warnings":          " | ".join(r["warnings"]),
        })
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n📋 Report written → {path}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="ATLAS Dataset Quality Validator")
    parser.add_argument("--input",   required=True,
        help="Dataset directory (contains building_XXX/ subdirs)")
    parser.add_argument("--verbose", action="store_true",
        help="Show all warnings even for passing buildings")
    parser.add_argument("--report",  default=None,
        help="Write CSV report to this path")
    args = parser.parse_args()

    input_dir = Path(args.input)
    # Find building dirs (contain horizontal_graph.json)
    bldg_dirs = sorted([
        d for d in input_dir.iterdir()
        if d.is_dir() and (d/"horizontal_graph.json").exists()])

    if not bldg_dirs:
        print(f"No building directories found in {input_dir}")
        return

    print(f"\nATLAS Validator — {len(bldg_dirs)} buildings\n")
    results  = []
    counts   = defaultdict(int)

    for bd in bldg_dirs:
        r = validate_building(bd, verbose=args.verbose)
        print_result(r, args.verbose)
        results.append(r)
        counts[r["status"]] += 1

    print(f"\n{'─'*55}")
    print(f"  Total : {len(results):4d}  |  "
          f"✅ OK: {counts['OK']}  |  "
          f"⚠️  WARN: {counts['WARN']}  |  "
          f"❌ ERROR: {counts['ERROR']}")

    if args.report:
        write_csv(results, Path(args.report))

    print()


if __name__ == "__main__":
    main()
