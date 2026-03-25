#!/usr/bin/env python3
"""
json_to_appendix.py — ATLAS Dataset Supplementary Material Generator
Generates a multi-page PDF appendix (one page per building) for use
as Supplementary Material in academic publications.

Each page contains:
  ① Building summary table (floors, area, node/edge counts, classes)
  ② Representative floor 2D bubble diagram (1F or standard floor)
  ③ Space class distribution bar chart
  ④ Vertical core cluster summary

Usage:
    # Single building
    python json_to_appendix.py --input building_001/ --output appendix/

    # All buildings → merged PDF
    python json_to_appendix.py --input json_output/ --batch \
        --output appendix/ --title "ATLAS Dataset Appendix"
"""

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch
import numpy as np

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, Image, PageBreak,
                                     HRFlowable)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm, mm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

try:
    import networkx as nx
    HAS_NX = True
except ImportError:
    HAS_NX = False

# ── Constants ─────────────────────────────────────────────────────────────────
CLS_COLOR = {
    1:"#3A8DDD",2:"#888780",3:"#DD6622",4:"#AA8833",5:"#8888AA",
    6:"#9966CC",7:"#CC4499",8:"#EE2222",9:"#EE2222",10:"#BB3388",
    11:"#AA5522",12:"#9966AA",13:"#8B7014",14:"#888780",15:"#9966AA",
    16:"#1D9E75",17:"#1D9E75",18:"#888780",19:"#9966AA",
}
CLS_LABEL = {
    1:"Leasable",2:"Main Road",3:"Corridor",4:"Vestibule",5:"Water Tank",
    6:"Hall",7:"MEP",8:"Elevator",9:"Staircase",10:"Restroom",
    11:"Management",12:"Storage",13:"Terrace",14:"Sec.Road",
    15:"Parking Entr.",16:"Open Space",17:"Green Space",18:"Surr.Mass",
    19:"Parking",20:"Mechanical",21:"Disaster Prev.",22:"Electrical",
}
EXT_CLS  = {2,14,16,17,18}
CORE_CLS = {8,9}


# ── Union-Find ────────────────────────────────────────────────────────────────
class UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        if self.p[x] != x: self.p[x] = self.find(self.p[x])
        return self.p[x]
    def union(self, a, b): self.p[self.find(a)] = self.find(b)


# ── Data helpers ──────────────────────────────────────────────────────────────
def norm_key(s):
    return re.sub(r'[\s_.]', '', s)


def build_area_lu(a):
    lu = {}
    for fk, fd in a["floors"].items():
        raw = fd["floor_label"]
        for k, v in fd["spaces"].items():
            lu[raw + "::" + norm_key(k)] = v
    return lu


def get_ar(node, raw, area_lu, floor_total):
    ak = raw + "::" + norm_key(node["id"])
    v  = area_lu.get(ak, {})
    ar = v.get("area_ratio", 0)
    asm= v.get("area_sqm", 0)
    if ar == 0 and asm > 0 and floor_total > 0:
        ar = asm / floor_total
    return round(ar, 4), round(asm, 1)


def compute_vert_clusters(v):
    """Return {node_id: floor_count} for physical (w>0) vertical clusters."""
    uf = UF()
    for e in v["edges"]:
        if float(e["weight"]) > 0:
            uf.union(e["source_node"], e["target_node"])
    clusters = defaultdict(list)
    for nid in uf.p: clusters[uf.find(nid)].append(nid)
    node_cls = {}
    for e in v["edges"]:
        node_cls[e["source_node"]] = e["source_class_id"]
        node_cls.setdefault(e["target_node"], e.get("target_class_id", 0))
    core_clusters = []
    for rep, members in clusters.items():
        if len(members) < 2: continue
        cc = defaultdict(int)
        for m in members: cc[node_cls.get(m, 0)] += 1
        mc = max(cc, key=cc.get)
        if mc in CORE_CLS:
            core_clusters.append({
                "cls": mc,
                "nodes": members,
                "count": len(members),
                "label": "EV" if mc == 8 else "Staircase",
            })
    return core_clusters


def compute_class_stats(h, area_lu):
    """Return {cls_id: total_sqm} across all floors."""
    by_cls = defaultdict(float)
    for fl in h:
        raw = fl["floor_label"]
        for n in fl["nodes"]:
            if n["is_external_context"]: continue
            ak = raw + "::" + norm_key(n["id"])
            asm = area_lu.get(ak, {}).get("area_sqm", 0)
            by_cls[n["class_id"]] += asm
    return by_cls


# ── Spring layout ─────────────────────────────────────────────────────────────
def floor_layout_for(fl_data):
    """Return {node_id: (x,z)} for one floor."""
    if not HAS_NX:
        int_nodes = [n for n in fl_data["nodes"] if not n["is_external_context"]]
        N = len(int_nodes)
        return {n["id"]: (math.cos(2*math.pi*i/max(N,1))*40,
                          math.sin(2*math.pi*i/max(N,1))*40)
                for i, n in enumerate(int_nodes)}
    int_ids = [n["id"] for n in fl_data["nodes"]
               if not n["is_external_context"]]
    if not int_ids: return {}
    G = nx.Graph(); G.add_nodes_from(int_ids)
    for e in fl_data["edges"]:
        s, t = e["source"], e["target"]
        if s in int_ids and t in int_ids:
            G.add_edge(s, t, weight=max(float(e["weight"]), 0.1))
    # pin leasable to center, cores to fixed positions
    fixed = {}
    for n in fl_data["nodes"]:
        if n["is_external_context"]: continue
        if n["class_id"] == 1: fixed[n["id"]] = (0, 0)
        elif n["class_id"] == 8: fixed[n["id"]] = (-0.4, 0.2)
        elif n["class_id"] == 9:
            idx = sum(1 for nn in fl_data["nodes"]
                      if nn["class_id"]==9 and nn["id"]<n["id"])
            a = (0.5 + idx * 1.0) * math.pi / 2
            fixed[n["id"]] = (math.cos(a)*0.45, math.sin(a)*0.45)
    init = {nid: fixed.get(nid, (0,0)) for nid in int_ids}
    fixed_list = list(fixed.keys())
    try:
        pos = nx.spring_layout(G, pos=init,
                               fixed=fixed_list or None,
                               weight="weight", k=0.35,
                               iterations=100, seed=42)
    except Exception:
        pos = {nid: (0,0) for nid in int_ids}
    scale = 55
    return {nid: (round(px*scale, 1), round(pz*scale, 1))
            for nid, (px, pz) in pos.items()}


# ── 2D bubble diagram (matplotlib) ───────────────────────────────────────────
def draw_floor_bubble(ax, fl_data, area_lu, title=""):
    """Draw a 2D bubble diagram for one floor on ax."""
    layout = floor_layout_for(fl_data)
    raw    = fl_data["floor_label"]

    # ── Edges ────────────────────────────────────────────────────
    nm = {}
    for n in fl_data["nodes"]:
        if not n["is_external_context"] and n["id"] in layout:
            nm[n["id"]] = layout[n["id"]]

    seen_e = set()
    for e in fl_data["edges"]:
        key = tuple(sorted([e["source"], e["target"]]))
        if key in seen_e: continue
        seen_e.add(key)
        sp = nm.get(e["source"]); tp = nm.get(e["target"])
        if not sp or not tp: continue
        w = float(e["weight"])
        lw  = 2.2 if w >= 0.9 else (1.2 if w >= 0.4 else 0.5)
        col = "#EE3322" if w >= 0.9 else ("#3A8DDD" if w >= 0.4 else "#CCCCCC")
        ls  = "-" if w > 0 else (0, (4, 3))
        ax.plot([sp[0], tp[0]], [sp[1], tp[1]],
                color=col, linewidth=lw, linestyle=ls,
                alpha=0.7, solid_capstyle="round", zorder=3)

    # ── Nodes ────────────────────────────────────────────────────
    for n in fl_data["nodes"]:
        if n["is_external_context"]: continue
        p = layout.get(n["id"])
        if not p: continue
        ak  = raw + "::" + norm_key(n["id"])
        ar  = area_lu.get(ak, {}).get("area_ratio", 0.02)
        r   = max(4, min(28, math.sqrt(ar or 0.01) * 55))
        col = CLS_COLOR.get(n["class_id"], "#888888")
        circle = plt.Circle(p, r, color=col, alpha=0.88, zorder=5)
        ax.add_patch(circle)
        ax.add_patch(plt.Circle(p, r, fill=False, ec="white",
                                linewidth=0.5, alpha=0.6, zorder=6))
        if r > 8:
            lbl = CLS_LABEL.get(n["class_id"], "")[:4]
            ax.text(p[0], p[1], lbl, ha="center", va="center",
                    fontsize=max(4.5, min(7, r*0.42)),
                    color="white", fontweight="bold", zorder=7)

    ax.set_aspect("equal"); ax.axis("off")
    ax.set_xlim(-75, 75); ax.set_ylim(-75, 75)
    if title:
        ax.set_title(title, fontsize=8, fontweight="bold",
                     color="#333333", pad=4)


# ── Per-building page figure ──────────────────────────────────────────────────
def make_building_page(h, v, a, m, building_id: str,
                       out_path: Path, dpi: int = 180):
    """
    Create one A4-ish page figure per building for the appendix.
    Layout:
      Row 0: Title / summary table
      Row 1: Left=representative floor bubble, Right=class distribution bars
      Row 2: Vertical core cluster summary
    """
    area_lu    = build_area_lu(a)
    core_segs  = compute_vert_clusters(v)
    by_cls     = compute_class_stats(h, area_lu)
    total_area = sum(by_cls.values())

    # Choose representative floor: 1F for ground, else lowest above-ground
    rep_floor = next(
        (f for f in h if f["floor_label"] == "1"),
        next((f for f in h if not f["floor_label"].startswith("B")),
             h[0]))

    # Floor table data
    fl_rows = []
    for fl in h:
        raw = fl["floor_label"]
        int_n  = [n for n in fl["nodes"] if not n["is_external_context"]]
        ext_n  = [n for n in fl["nodes"] if n["is_external_context"]]
        area_f = sum(area_lu.get(raw+"::"+norm_key(n["id"]),{}).get("area_sqm",0)
                     for n in int_n)
        leas_a = sum(area_lu.get(raw+"::"+norm_key(n["id"]),{}).get("area_sqm",0)
                     for n in int_n if n["class_id"]==1)
        leas_pct = (leas_a/area_f*100) if area_f > 0 else 0
        fl_rows.append((raw, f"{area_f:.0f}", len(int_n), len(ext_n),
                        len(fl["edges"]), f"{leas_pct:.0f}%"))

    # ── Figure layout ──────────────────────────────────────────
    fig = plt.figure(figsize=(8.27, 11.69), dpi=dpi)  # A4
    fig.patch.set_facecolor("white")

    n_fl = len(h)
    tbl_h = max(0.18, min(0.32, 0.04 + n_fl * 0.025))  # 층수에 따라 표 높이 동적
    viz_h = 0.82 - tbl_h
    gs = gridspec.GridSpec(4, 2, figure=fig,
                           height_ratios=[0.08, tbl_h, viz_h*0.78, viz_h*0.22],
                           hspace=0.38, wspace=0.28,
                           left=0.08, right=0.95,
                           top=0.96, bottom=0.04)

    # ── Title + table (row 0, span both cols) ──────────────────
    ax_title = fig.add_subplot(gs[0, :])
    ax_title.axis("off")

    # Header
    ax_title.text(0.0, 1.0,
                  f"ATLAS Dataset — Supplementary Material",
                  transform=ax_title.transAxes,
                  fontsize=7.5, color="#888", va="top")
    ax_title.text(0.0, 0.72,
                  f"{building_id}",
                  transform=ax_title.transAxes,
                  fontsize=12, fontweight="bold", color="#111", va="top")

    meta_str = (f"{m.get('num_floors','?')} floors  ·  "
                f"{m.get('total_nodes','?')} nodes  ·  "
                f"{m.get('total_horizontal_edges','?')} h-edges  ·  "
                f"{m.get('total_vertical_edges','?')} v-edges  ·  "
                f"Total GFA {total_area:.0f} ㎡")
    ax_title.text(0.0, 0.30, meta_str,
                  transform=ax_title.transAxes,
                  fontsize=7.5, color="#444", va="top")

    # 표 전용 axes
    ax_tbl = fig.add_subplot(gs[1, :])
    ax_tbl.axis("off")
    col_labels = ["Floor","Area(sqm)","Int.Nodes","Ext.Nodes","Edges","Leasable%"]
    tbl = ax_tbl.table(
        cellText=fl_rows,
        colLabels=col_labels,
        loc="center", cellLoc="center",
        bbox=[0.0, 0.0, 1.0, 1.0])
    tbl.auto_set_font_size(False); tbl.set_fontsize(6.5)
    for (r, c), cell in tbl.get_celld().items():
        if r == 0:
            cell.set_facecolor("#2C3E50")
            cell.set_text_props(color="white", fontweight="bold")
        elif r % 2 == 0:
            cell.set_facecolor("#F4F5F8")
        cell.set_edgecolor("#DDDDDD")

    # ── Bubble diagram (row 1, left) ───────────────────────────
    ax_bub = fig.add_subplot(gs[2, 0])
    draw_floor_bubble(ax_bub, rep_floor, area_lu,
                      title=f"Representative Floor: {rep_floor['floor_label']}")

    # ── Class distribution (row 1, right) ──────────────────────
    ax_bar = fig.add_subplot(gs[2, 1])
    sorted_cls = sorted(by_cls.items(), key=lambda x: -x[1])[:10]
    labels = [CLS_LABEL.get(c, f"cls{c}") for c, _ in sorted_cls]
    values = [v for _, v in sorted_cls]
    cols   = [CLS_COLOR.get(c, "#888888") for c, _ in sorted_cls]
    y_pos  = range(len(labels)-1, -1, -1)
    ax_bar.barh(list(y_pos), values, color=cols, alpha=0.85, height=0.68)
    ax_bar.set_yticks(list(y_pos))
    ax_bar.set_yticklabels(labels, fontsize=7)
    ax_bar.set_xlabel("Total area (㎡)", fontsize=7)
    ax_bar.set_title("Space Class Distribution\n(cumulative across all floors)",
                     fontsize=8, fontweight="bold", pad=4)
    ax_bar.tick_params(axis="both", labelsize=7)
    ax_bar.spines[["top","right"]].set_visible(False)
    for i, (v2, (cls, _)) in enumerate(zip(values, sorted_cls)):
        pct = v2/total_area*100 if total_area else 0
        ax_bar.text(v2 + total_area*0.005,
                    len(labels)-1-i,
                    f"{pct:.1f}%", va="center", fontsize=6.5, color="#555")

    # ── Vertical core summary (row 2, span) ────────────────────
    ax_vc = fig.add_subplot(gs[3, :])
    ax_vc.axis("off")
    ax_vc.text(0.0, 0.95, "Vertical Core Clusters (Union-Find, w>0)",
               transform=ax_vc.transAxes,
               fontsize=8, fontweight="bold", color="#333", va="top")

    # Count floors per cluster
    node_floors = defaultdict(set)
    for fl in h:
        for n in fl["nodes"]:
            if not n["is_external_context"]:
                node_floors[n["id"]].add(fl["floor_label"])

    # 항목 수에 따라 y 간격 동적 조정
    n_segs = len(core_segs) if core_segs else 0
    y_step = min(0.22, 0.65 / max(n_segs + 1, 3))
    y = 0.78

    if core_segs:
        for seg in core_segs:
            fls = set()
            for nid in seg["nodes"]: fls.update(node_floors.get(nid, set()))
            def _fl_key(x):
                try:
                    if x.startswith("B"):
                        return -int(x[1:])
                    return int(x)
                except (ValueError, TypeError):
                    return 999  # 옥탑, RF 등 비정규 층 → 맨 뒤
            fl_sorted = sorted(fls, key=_fl_key)
            fl_range = f"{fl_sorted[-1]}F ~ {fl_sorted[0]}F" if fl_sorted else "?"
            col = "#CC0000" if seg["cls"]==8 else "#AA0000"
            ax_vc.text(0.0, y,
                f"  {'▪' if seg['cls']==8 else '▫'} {seg['label']}  "
                f"({len(seg['nodes'])} nodes · {len(fls)} floors · {fl_range})",
                transform=ax_vc.transAxes,
                fontsize=7.5, color=col, va="top")
            y -= y_step
    else:
        ax_vc.text(0.0, y, "  No vertical core clusters detected",
                   transform=ax_vc.transAxes, fontsize=7.5, color="#888", va="top")
        y -= y_step

    # w=0 clusters (programmatic) — 항상 core_segs 아래 y_step 간격 유지
    uf_clus = UF()
    for e in v["edges"]:
        if float(e["weight"]) == 0:
            uf_clus.union(e["source_node"], e["target_node"])
    prog_clusters = defaultdict(list)
    for nid in uf_clus.p: prog_clusters[uf_clus.find(nid)].append(nid)
    prog_big = [(rep, mems) for rep, mems in prog_clusters.items()
                if len(mems) >= 3]
    if prog_big:
        prog_y = max(y - y_step * 0.3, 0.04)
        ax_vc.text(0.0, prog_y,
            f"  Programmatic clusters (w=0): "
            f"{len(prog_big)} groups  "
            f"(e.g. {prog_big[0][1][0]} + {len(prog_big[0][1])-1} more)",
            transform=ax_vc.transAxes, fontsize=7.0, color="#3A5888", va="top")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    return out_path


# ── PDF assembly ──────────────────────────────────────────────────────────────
def assemble_pdf(page_images: list, out_pdf: Path, title: str):
    """Merge per-building PNGs into one PDF using reportlab."""
    if not HAS_REPORTLAB:
        print("  ⚠️  reportlab not installed — skipping PDF assembly")
        print("     pip install reportlab")
        return

    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4,
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)
    styles = getSampleStyleSheet()
    story  = []

    # Cover
    cover_style = ParagraphStyle("cover", parent=styles["Title"],
                                 fontSize=18, spaceAfter=12)
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph("ATLAS Dataset", cover_style))
    story.append(Paragraph("Supplementary Material — Building Profiles",
                            styles["Heading2"]))
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph(
        f"{len(page_images)} buildings · Generated by json_to_appendix.py",
        styles["Normal"]))
    story.append(PageBreak())

    # One page per building
    W_pt = A4[0] - 3*cm   # ~510 pt
    H_pt = A4[1] - 3*cm   # ~757 pt  (but frame is ~744)
    # Fit image within frame preserving aspect ratio
    for img_path in page_images:
        from PIL import Image as PILImage
        try:
            with PILImage.open(img_path) as im:
                iw, ih = im.size
        except Exception:
            iw, ih = 1, 1
        scale = min(W_pt/iw, (A4[1]-3.5*cm)/ih)
        story.append(Image(str(img_path),
                           width=iw*scale, height=ih*scale))
        story.append(PageBreak())

    doc.build(story)
    print(f"  📄 PDF assembled → {out_pdf}  ({len(page_images)} pages)")


# ── Main ──────────────────────────────────────────────────────────────────────
def process_building(bldg_dir: Path, output_dir: Path,
                     dpi: int = 180) -> Path:
    bldg_dir = Path(bldg_dir)
    h = json.loads((bldg_dir/"horizontal_graph.json").read_text(encoding="utf-8"))
    v = json.loads((bldg_dir/"vertical_graph.json").read_text(encoding="utf-8"))
    a = json.loads((bldg_dir/"area_ratios.json").read_text(encoding="utf-8"))
    m = json.loads((bldg_dir/"metadata.json").read_text(encoding="utf-8"))

    building_id = m["building_id"]
    out_png = output_dir / f"{building_id}_appendix.png"

    print(f"  Processing: {building_id}")
    make_building_page(h, v, a, m, building_id, out_png, dpi=dpi)
    print(f"  ✅ {out_png.name}")
    return out_png


def main():
    parser = argparse.ArgumentParser(
        description="ATLAS JSON → Appendix PDF/PNG")
    parser.add_argument("--input",  required=True,
        help="Building dir or dataset folder (with --batch)")
    parser.add_argument("--output", default="./appendix",
        help="Output directory (default: ./appendix)")
    parser.add_argument("--batch",  action="store_true",
        help="Process all buildings in --input folder")
    parser.add_argument("--pdf",    action="store_true",
        help="Merge into single PDF (requires reportlab)")
    parser.add_argument("--title",  default="ATLAS Dataset Appendix",
        help="PDF cover title")
    parser.add_argument("--dpi",    type=int, default=180,
        help="Figure DPI (default: 180)")
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.batch:
        input_dir  = Path(args.input)
        bldg_dirs  = sorted(d for d in input_dir.iterdir()
                            if d.is_dir() and
                            (d/"horizontal_graph.json").exists())
        print(f"\nBatch: {len(bldg_dirs)} buildings\n")
        pages = []
        for bd in bldg_dirs:
            pages.append(process_building(bd, output_dir, dpi=args.dpi))
        if args.pdf:
            out_pdf = output_dir / "appendix.pdf"
            assemble_pdf(pages, out_pdf, args.title)
    else:
        png = process_building(Path(args.input), output_dir, dpi=args.dpi)
        if args.pdf:
            assemble_pdf([png], output_dir / "appendix.pdf", args.title)

    print("\nDone.")


if __name__ == "__main__":
    main()
