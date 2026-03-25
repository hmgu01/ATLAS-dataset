#!/usr/bin/env python3
"""
json_to_figures.py — ATLAS Dataset Figure Generator
Converts ATLAS JSON graph data into publication-quality exploded axonometric
bubble diagrams (PNG, 300 dpi).

Usage:
    python json_to_figures.py --input  <building_dir>  --output <out_dir>
    python json_to_figures.py --input  sample_json/1_차이커뮤니케이션_사옥  --output figures/

Building dir must contain:
    horizontal_graph.json
    vertical_graph.json
    area_ratios.json

Output:
    <building_id>_axo.png          Full exploded axonometric (all floors)
    <building_id>_floor_<FL>.png   Single-floor bubble diagram (optional)
"""

import argparse, json, math, os, sys
from pathlib import Path
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines  as mlines
from matplotlib.patches import FancyArrowPatch
import numpy as np
import networkx as nx

# ── Constants ────────────────────────────────────────────────────────────────
FL_ORDER  = ["B2","B1","1","2","3","4","5","6","7","8","9","10"]
FL_LABEL  = {"B2":"B2","B1":"B1","1":"1F","2":"2F","3":"3F","4":"4F",
             "5":"5F","6":"6F","7":"7F","8":"8F","9":"9F","10":"10F"}
GROUND_FL = {"B2","B1","1"}

GAP        = 3.5   # vertical gap between floors (axis units)
BLDG_HALF  = 1.55  # building half-width
SLAB_W     = {2:.55,14:.40,16:.50,17:.50,18:1.1}  # context slab widths
LAYER_ORD  = {16:0,17:0,2:1,14:1,18:2}

# Axonometric projection matrix (isometric-ish, 22° FOV feel)
ISO_X = np.array([ math.cos(math.radians(30)), -math.cos(math.radians(30)), 0])
ISO_Y = np.array([ math.sin(math.radians(30)),  math.sin(math.radians(30)), 1])

def proj(x, y, z):
    """Project 3D point to 2D axonometric."""
    return np.dot(ISO_X, [x,y,z]), np.dot(ISO_Y, [x,y,z])

def proj_plan(x, z):
    """Top-down orthographic projection for single-floor plan view.
    x → right,  z → up (standard plan orientation)."""
    return float(x), float(z)

# ── Class colors ─────────────────────────────────────────────────────────────
CLS_COLOR = {
    1:  "#3A8DDD",   # Leasable
    2:  "#1A1A1A",   # Main Road
    3:  "#DD6622",   # Corridor
    4:  "#AA8833",   # Windbreak
    5:  "#8888AA",   # Hall/Lobby
    6:  "#9966CC",   # Hall
    7:  "#CC4499",   # MEP
    8:  "#EE2222",   # Elevator
    9:  "#EE2222",   # Staircase
    10: "#BB3388",   # Restroom
    11: "#AA5522",   # Management
    12: "#AA5522",   # Storage
    13: "#8B7014",   # Terrace
    14: "#444444",   # Secondary Road
    15: "#8866AA",   # Parking Entrance
    16: "#1D9E75",   # Open Space
    17: "#1D9E75",   # Green Space
    18: "#888780",   # Surrounding Mass
    19: "#8866AA",   # Parking Lot
    20: "#CC4499",   # Mechanical Room
    21: "#CC4499",   # Disaster Prevention
    22: "#CC4499",   # Electrical Room
}
CLS_LABEL = {
    1:"Leasable",2:"Road",3:"Corridor",4:"Windbreak",5:"Hall",6:"Hall",
    7:"MEP",8:"Elevator",9:"Staircase",10:"Restroom",11:"Mgmt.",12:"Storage",
    13:"Terrace",14:"Sec.Road",15:"Parking Entr.",16:"Open Space",17:"Green",
    18:"Surr.Mass",19:"Parking",20:"Mech.",21:"Disaster Prev.",22:"Electrical"
}

# ── Union-Find ────────────────────────────────────────────────────────────────
class UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        if self.p[x] != x: self.p[x] = self.find(self.p[x])
        return self.p[x]
    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)

# ── Data loaders ─────────────────────────────────────────────────────────────
def norm_key(s):
    return s.replace("_","").replace(" ","").replace(".","")

def load_data(building_dir):
    bd = Path(building_dir)
    h  = json.loads((bd/"horizontal_graph.json").read_text(encoding="utf-8"))
    v  = json.loads((bd/"vertical_graph.json").read_text(encoding="utf-8"))
    a  = json.loads((bd/"area_ratios.json").read_text(encoding="utf-8"))
    return h, v, a

# ── Layout: force-directed with fixed vertical shafts ────────────────────────
def compute_layouts(h, v):
    """Compute per-floor spring_layout. Returns {raw_floor: {node_id: (x,z)}}"""

    # 1. Build vertical clusters via Union-Find
    uf = UF()
    node_cls = {}
    for fl in h:
        for n in fl["nodes"]:
            node_cls[n["id"]] = n["class_id"]
    for e in v["edges"]:
        uf.union(e["source_node"], e["target_node"])

    clusters = defaultdict(list)
    for nid in uf.p:
        clusters[uf.find(nid)].append(nid)

    # 2. Assign fixed XZ to each cluster
    fixed_xz = {}
    ev_i = st_i = mep_i = wc_i = 0
    for rep, members in sorted(clusters.items(), key=lambda x: -len(x[1])):
        if len(members) < 2 or any(m in fixed_xz for m in members):
            # propagate existing
            existing = [m for m in members if m in fixed_xz]
            if existing:
                for m in members:
                    fixed_xz.setdefault(m, fixed_xz[existing[0]])
            continue
        cls_cnt = defaultdict(int)
        for m in members: cls_cnt[node_cls.get(m,0)] += 1
        mc = max(cls_cnt, key=cls_cnt.get)

        if mc == 8:     # EV
            x,z = -32 + ev_i*14, 16; ev_i += 1
        elif mc == 9:   # Staircase
            angles = [0.5, 1.5, 2.5, 3.2]
            a = angles[st_i % len(angles)] * math.pi/2
            x,z = round(math.cos(a)*34), round(math.sin(a)*34); st_i += 1
        elif mc == 7:   # MEP
            angles = [0.85, 1.75]
            a = angles[mep_i % 2] * math.pi/2 + 0.2
            x,z = round(math.cos(a)*52), round(math.sin(a)*52); mep_i += 1
        elif mc == 10:  # WC
            angles = [0.15, 0.75]
            a = angles[wc_i % 2] * math.pi/2
            x,z = round(math.cos(a)*46), round(math.sin(a)*46); wc_i += 1
        else:
            continue
        for m in members:
            fixed_xz[m] = (x, z)

    # Leasable always at center
    for fl in h:
        for n in fl["nodes"]:
            if n["class_id"] == 1 and not n["is_external_context"]:
                fixed_xz[n["id"]] = (0, 0)

    # 3. Per-floor spring layout
    SCALE = 72; BOUND = 0.85
    floor_layouts = {}
    for fl_data in h:
        raw = fl_data["floor_label"]
        int_nodes = [n for n in fl_data["nodes"] if not n["is_external_context"]]
        if not int_nodes: continue

        node_ids = [n["id"] for n in int_nodes]
        G = nx.Graph(); G.add_nodes_from(node_ids)
        for e in fl_data["edges"]:
            s,t,w = e["source"], e["target"], e["weight"]
            if s in node_ids and t in node_ids:
                G.add_edge(s, t, weight=max(w, 0.1))

        init_pos, fixed_list = {}, []
        for nid in node_ids:
            if nid in fixed_xz:
                fx,fz = fixed_xz[nid]
                init_pos[nid] = (fx/SCALE*BOUND, fz/SCALE*BOUND)
                fixed_list.append(nid)

        try:
            pos = nx.spring_layout(
                G, pos=init_pos or None,
                fixed=fixed_list or None,
                weight="weight", k=0.55, iterations=100, seed=42
            )
        except Exception:
            pos = {nid: (0,0) for nid in node_ids}

        layout = {}
        for nid in node_ids:
            px,pz = pos[nid]
            px = max(-BOUND, min(BOUND, px))
            pz = max(-BOUND, min(BOUND, pz))
            layout[nid] = (round(px*SCALE, 1), round(pz*SCALE, 1))
        floor_layouts[raw] = layout

    return floor_layouts, fixed_xz

# ── External face detection ───────────────────────────────────────────────────
def compute_faces(h, floor_layouts):
    """
    Determine face direction for external context nodes.

    Priority:
      1. Use pre-computed 'face' field in JSON (from excel_to_json.py)
      2. Fallback: 3-step chain computation
         Step1 name pattern, Step2 ext-ext propagation, Step3 position
    """
    ROAD_FACE_MAP = {"메인도로":"E","메인 도로":"E","부도로":"N",
                     "주차장 진입로":"E","지하주차장 진입로":"E"}
    MASS_FACE_NUM  = {1:"S",2:"W",3:"N",4:"E"}

    def face_of(x, z):
        if abs(z) > abs(x): return "N" if z > 0 else "S"
        return "E" if x > 0 else "W"

    # Use ground floor (1F or lowest) for reference
    fl_ref = next((f for f in h if f["floor_label"]=="1"), h[0])
    raw_ref = fl_ref["floor_label"]
    pos_ref = floor_layouts.get(raw_ref, {})
    edges_ref = [(e["source"],e["target"],float(e["weight"])) for e in fl_ref["edges"]]
    ext_ref = [n for n in fl_ref["nodes"] if n["is_external_context"]]

    EXT_FACE = {}

    # Priority 1: use pre-computed face from JSON
    for n in ext_ref:
        if "face" in n:
            EXT_FACE[n["id"]] = n["face"]

    if len(EXT_FACE) < len(ext_ref):
        # Priority 2: 3-step fallback for nodes without face field
        ext_map = {n["id"]:n for n in ext_ref}

        # Step 1 – name pattern
        for n in ext_ref:
            if n["id"] in EXT_FACE: continue
            name = n["class_name"]; num = n["instance_index"]
            for kw, f in ROAD_FACE_MAP.items():
                if kw in name: EXT_FACE[n["id"]] = f; break
            if n["id"] not in EXT_FACE and "주변매스" in name:
                EXT_FACE[n["id"]] = MASS_FACE_NUM.get(num, "N")

        # Step 2 – ext→ext propagation
        for _ in range(3):
            for s,t,w in edges_ref:
                for src,tgt in [(s,t),(t,s)]:
                    if (src in EXT_FACE and tgt in ext_map
                            and tgt not in EXT_FACE):
                        EXT_FACE[tgt] = EXT_FACE[src]

        # Step 3 – position of connected internal nodes
        for n in ext_ref:
            if n["id"] in EXT_FACE: continue
            conn = []
            for s,t,w in edges_ref:
                other = t if s==n["id"] else (s if t==n["id"] else None)
                if other and other in pos_ref and other not in ext_map:
                    conn.append(pos_ref[other])
            if conn:
                ax = sum(p[0] for p in conn)/len(conn)
                az = sum(p[1] for p in conn)/len(conn)
                EXT_FACE[n["id"]] = face_of(-ax, -az)
            else:
                name = n["class_name"]; num = n["instance_index"]
                if "공지" in name:
                    EXT_FACE[n["id"]] = {1:"S",2:"W",3:"N",4:"E"}.get(num,"N")
                else:
                    EXT_FACE[n["id"]] = "N"

    # EXT_OFF: sorted by face group
    face_items = defaultdict(list)
    for n in ext_ref:
        face2 = EXT_FACE.get(n["id"], "N")
        face_items[face2].append(
            (LAYER_ORD.get(n["class_id"],0), n["class_id"],
             n["instance_index"], n["id"]))

    EXT_OFF = {}
    for face2, items in face_items.items():
        items.sort(); off = BLDG_HALF*72 + 2
        for _order, cls, _num, nid in items:
            w = SLAB_W.get(cls, .50)*72
            off += w/2; EXT_OFF[nid] = off/72; off += w/2

    return EXT_FACE, EXT_OFF

# ── Area ratios lookup ─────────────────────────────────────────────────────────
def build_area_lookup(a):
    lu = {}
    for flkey, fdata in a["floors"].items():
        raw = fdata["floor_label"]
        for k, v in fdata["spaces"].items():
            lu[raw+"::"+norm_key(k)] = v
    return lu

# ── Node radius from area ratio ────────────────────────────────────────────────
def node_radius(ar, scale=0.42, for_plan=False):
    """Returns radius in axis units.
    for_plan=True: larger minimum for single-floor top-down view."""
    r_min = 0.065 if for_plan else 0.032
    r_max = 0.22  if for_plan else 0.17
    sc    = 0.52  if for_plan else scale
    return max(r_min, min(r_max, math.sqrt(ar or 0.02) * sc))

# ── Draw one floor ────────────────────────────────────────────────────────────
FV = {"N":(0,1),"S":(0,-1),"E":(1,0),"W":(-1,0)}

def draw_floor(ax, fl_data, raw, fy, layout, area_lu,
               EXT_FACE, EXT_OFF, is_ground, fade=1.0,
               draw_ext_slab=True, draw_labels=True):
    """Draw all elements for one floor on ax (2D projected)."""

    SC = 1/72  # data-unit → axis-unit

    nodes_map = {n["id"]:n for n in fl_data["nodes"]}
    int_nodes  = [n for n in fl_data["nodes"] if not n["is_external_context"]]
    edges      = fl_data["edges"]

    # Slab
    if draw_ext_slab:
        bh = BLDG_HALF
        corners = [(-bh,fy,-bh),(-bh,fy,bh),(bh,fy,bh),(bh,fy,-bh)]
        pts2 = [proj(*c) for c in corners]
        xs = [p[0] for p in pts2]+[pts2[0][0]]
        ys = [p[1] for p in pts2]+[pts2[0][1]]
        alpha = 0.62 if is_ground else 0.07*fade
        fc    = "#7A7060" if is_ground else "#BBBBCC"
        ax.fill(xs, ys, color=fc, alpha=alpha, zorder=2, linewidth=0)
        ax.plot(xs, ys, color="#4A4030" if is_ground else "#8899BB",
                linewidth=0.6 if is_ground else 0.3, alpha=0.7*fade, zorder=3)

    # Floor label
    if draw_labels:
        lx, lz = -bh-0.18, 0
        px,py = proj(lx, fy+0.05, lz)
        fl_lbl = FL_LABEL.get(raw, raw)
        ax.text(px, py, fl_lbl, fontsize=7, color="#333333",
                ha="right", va="center", fontweight="bold",
                bbox=dict(fc="white", ec="none", alpha=0.7, pad=1.5), zorder=10)

    # External context slabs (ground only)
    seen_ext = defaultdict(set)
    ext_nodes = [n for n in fl_data["nodes"] if n["is_external_context"] and not n.get("is_mass")]
    for n in ext_nodes:
        face  = EXT_FACE.get(n["id"], "N")
        off   = EXT_OFF.get(n["id"], BLDG_HALF+0.3)
        cls   = n["class_id"]
        key   = f"{face}_{cls}"
        if key in seen_ext[face]: continue
        seen_ext[face].add(key)

        slb_w = SLAB_W.get(cls, 0.50)
        dx,dz = FV[face]
        cx,cz = dx*off, dz*off
        NS = face in ("N","S")
        hw = (BLDG_HALF+0.02) if NS else slb_w/2
        hd = slb_w/2           if NS else (BLDG_HALF+0.02)

        if is_ground and not n.get("viewOnly"):
            corners = [(cx-hw,fy,cz-hd),(cx+hw,fy,cz-hd),
                       (cx+hw,fy,cz+hd),(cx-hw,fy,cz+hd)]
            pts2 = [proj(*c) for c in corners]
            xs = [p[0] for p in pts2]+[pts2[0][0]]
            ys = [p[1] for p in pts2]+[pts2[0][1]]
            clr = "#111111" if cls==2 else ("#444444" if cls==14 else "#1D9E75")
            alp = 0.80     if cls==2 else (0.65      if cls==14 else 0.58)
            ax.fill(xs, ys, color=clr, alpha=alp, zorder=2, linewidth=0)
            ax.plot(xs, ys, color="#000000" if cls==2 else "#0A6040",
                    linewidth=0.5, alpha=0.8, zorder=3)
            # slab label
            px2,py2 = proj(cx, fy+0.06, cz)
            lname = {2:"Road",14:"Sec.Rd.",16:"Open Sp."}
            if cls in lname:
                ax.text(px2,py2,lname[cls],fontsize=5.5,color="white",
                        ha="center",va="center",fontweight="bold",zorder=11)

    # Internal H-edges
    nm = {}
    for n in int_nodes:
        p = layout.get(n["id"])
        if p: nm[n["id"]] = p

    # Build radius map for node-surface offset
    rm = {}
    for n in int_nodes:
        ak = raw+"::"+norm_key(n["id"])
        ar = area_lu.get(ak,{}).get("area_ratio",0.02)
        rm[n["id"]] = node_radius(ar) * 0.85  # same as node draw

    for s,t,w in edges:
        sp = nm.get(s); tp = nm.get(t)
        if not sp or not tp: continue
        # 3D positions for offset calculation
        s3 = (sp[0]*SC, fy, sp[1]*SC)
        t3 = (tp[0]*SC, fy, tp[1]*SC)
        dx = t3[0]-s3[0]; dz = t3[2]-s3[2]
        dist = math.hypot(dx, dz)
        r1 = rm.get(s, 0.04); r2 = rm.get(t, 0.04)
        if dist <= r1+r2+0.001: continue
        ux,uz = dx/dist, dz/dist
        # offset start/end from node surface
        sx2 = s3[0]+ux*r1;  sz2 = s3[2]+uz*r1
        ex2 = t3[0]-ux*r2;  ez2 = t3[2]-uz*r2
        p1 = proj(sx2, fy, sz2)
        p2 = proj(ex2, fy, ez2)

        if w >= 0.9:
            lw,clr,alpha,ls = 2.2,"#FF7700",0.88*fade,"-"
        elif w >= 0.4:
            lw,clr,alpha,ls = 1.2,"#3A8DDD",0.68*fade,"-"
        else:
            lw,clr,alpha,ls = 0.65,"#AA88CC",0.52*fade,(0,(4,3))
        ax.plot([p1[0],p2[0]],[p1[1],p2[1]],
                color=clr,linewidth=lw,alpha=alpha,linestyle=ls,
                solid_capstyle="round",zorder=5)

    # Internal nodes (spheres → circles in 2D)
    for n in int_nodes:
        p = layout.get(n["id"])
        if not p: continue
        ak = raw+"::"+norm_key(n["id"])
        ar = area_lu.get(ak,{}).get("area_ratio",0.02)
        r  = node_radius(ar) * 0.85  # slightly smaller in 2D
        clr= CLS_COLOR.get(n["class_id"],"#888888")
        px2,py2 = proj(p[0]*SC, fy, p[1]*SC)
        circle = plt.Circle((px2,py2), r, color=clr, alpha=0.88*fade, zorder=7)
        ax.add_patch(circle)
        # thin outline
        ax.add_patch(plt.Circle((px2,py2), r, fill=False,
                                ec="white", linewidth=0.4, alpha=0.6*fade, zorder=8))

# ── Compute cluster-based core segments (Union-Find) ─────────────────────────
def compute_core_segments(h, v, floor_layouts, active_floors):
    """
    Returns list of dicts:
      {cls, from, to, x, z, bw, bd, floors}
    Each dict = one continuous vertical core box segment.
    Uses Union-Find to group nodes across floors into shafts,
    then splits into contiguous floor segments.
    """
    # Build Union-Find
    uf = UF()
    node_cls = {}
    for fl in h:
        for n in fl["nodes"]:
            node_cls[n["id"]] = n["class_id"]
    for e in v["edges"]:
        uf.union(e["source_node"], e["target_node"])

    # Group members per cluster
    clusters = defaultdict(list)
    for nid in uf.p:
        clusters[uf.find(nid)].append(nid)

    # For each node, record which floors it appears on
    node_floors = defaultdict(set)
    for fl in h:
        if fl["floor_label"] not in active_floors: continue
        for n in fl["nodes"]:
            if not n["is_external_context"]:
                node_floors[n["id"]].add(fl["floor_label"])

    # For each node, record its layout XZ per floor
    node_pos = {}  # node_id -> (x, z)  — use first found
    for fl in h:
        raw = fl["floor_label"]
        if raw not in active_floors: continue
        for n in fl["nodes"]:
            if n["is_external_context"]: continue
            p = floor_layouts.get(raw, {}).get(n["id"])
            if p and n["id"] not in node_pos:
                node_pos[n["id"]] = p

    segments = []
    for rep, members in clusters.items():
        if len(members) < 2: continue
        cls_cnt = defaultdict(int)
        for m in members: cls_cnt[node_cls.get(m, 0)] += 1
        mc = max(cls_cnt, key=cls_cnt.get)
        if mc not in [8, 9]: continue  # core only

        # Collect all floors this cluster spans
        all_floors = set()
        for m in members:
            all_floors.update(node_floors.get(m, set()))
        sorted_idx = [FL_ORDER.index(f) for f in FL_ORDER
                      if f in all_floors and f in active_floors]
        if not sorted_idx: continue

        # Split into contiguous segments
        segs = []
        seg = [sorted_idx[0]]
        for i in range(1, len(sorted_idx)):
            if sorted_idx[i] == sorted_idx[i-1] + 1:
                seg.append(sorted_idx[i])
            else:
                segs.append(seg); seg = [sorted_idx[i]]
        segs.append(seg)

        # Average XZ for this cluster
        pts = [node_pos[m] for m in members if m in node_pos]
        if not pts: continue
        ax_xz = sum(p[0] for p in pts)/len(pts)
        az_xz = sum(p[1] for p in pts)/len(pts)

        for seg in segs:
            seg_floors = [FL_ORDER[i] for i in seg]
            segments.append({
                "cls":   mc,
                "from":  FL_ORDER[seg[0]],
                "to":    FL_ORDER[seg[-1]],
                "floors": seg_floors,
                "x":     round(ax_xz, 1),
                "z":     round(az_xz, 1),
                "bw":    0.062 if mc == 8 else 0.075,
                "bd":    0.062,
            })

    return segments


# ── Compute MEP vertical edges (Union-Find, non-core mandatory) ───────────────
def compute_mep_vedges(h, v, floor_layouts, active_floors):
    """Returns list of (src_raw, src_pos, tgt_pos) for MEP/WC vertical edges."""
    node_cls = {}
    for fl in h:
        for n in fl["nodes"]: node_cls[n["id"]] = n["class_id"]

    # Build NP map: raw::nid -> (x, y, z) in axis units
    NP = {}
    SC = 1/72
    for fl in h:
        raw = fl["floor_label"]
        if raw not in active_floors: continue
        fy = FL_ORDER.index(raw) * GAP
        for n in fl["nodes"]:
            if n["is_external_context"]: continue
            p = floor_layouts.get(raw, {}).get(n["id"])
            if p:
                NP[raw+"::"+n["id"]] = (p[0]*SC, fy, p[1]*SC)

    result = []
    seen = set()
    for e in v["edges"]:
        if not e["is_mandatory_continuity"]: continue
        sc = node_cls.get(e["source_node"], 0)
        if sc in [8, 9]: continue  # core handled by boxes
        sf = e["source_floor"]
        sn, tn = e["source_node"], e["target_node"]
        key = tuple(sorted([sn, tn]))
        if key in seen: continue
        seen.add(key)
        sp = NP.get(sf+"::"+sn)
        if not sp: continue
        # Find tn in any other floor
        tp = None
        for raw in FL_ORDER:
            p = NP.get(raw+"::"+tn)
            if p and abs(p[1] - sp[1]) > 0.2:
                tp = p; break
        if tp:
            result.append((sp, tp))
    return result


# ── Draw vertical elements (cluster-based core boxes + MEP lines) ─────────────
def draw_vertical_elements(ax, h, v, floor_layouts, area_lu, EXT_FACE, EXT_OFF,
                            active_floors):
    SC = 1/72
    all_raw = [fl["floor_label"] for fl in h if fl["floor_label"] in active_floors]
    if not all_raw: return
    y_vals = {raw: FL_ORDER.index(raw)*GAP for raw in all_raw}

    # ── Core boxes: cluster-based continuous segments ──────────────────────────
    core_segs = compute_core_segments(h, v, floor_layouts, active_floors)

    for seg in core_segs:
        x, z = seg["x"]*SC, seg["z"]*SC
        bw, bd = seg["bw"], seg["bd"]
        yB = y_vals.get(seg["from"], 0)
        yT = y_vals.get(seg["to"],   0) + GAP*0.38

        # 4 vertical corner lines
        for dx, dz in [(-bw/2,-bd/2),(bw/2,-bd/2),(bw/2,bd/2),(-bw/2,bd/2)]:
            p1 = proj(x+dx, yB, z+dz)
            p2 = proj(x+dx, yT, z+dz)
            ax.plot([p1[0],p2[0]], [p1[1],p2[1]],
                    color="#CC0000", linewidth=0.9, alpha=0.85, zorder=6,
                    solid_capstyle="round")

        # Top & bottom caps (filled + outline)
        for yy in [yB, yT]:
            corners = [(x-bw/2,yy,z-bd/2),(x+bw/2,yy,z-bd/2),
                       (x+bw/2,yy,z+bd/2),(x-bw/2,yy,z+bd/2)]
            pts2 = [proj(*c) for c in corners]
            xs = [p[0] for p in pts2] + [pts2[0][0]]
            ys = [p[1] for p in pts2] + [pts2[0][1]]
            ax.fill(xs, ys, color="#FF2222", alpha=0.18, zorder=5)
            ax.plot(xs, ys, color="#CC0000", linewidth=0.6, alpha=0.80, zorder=6)

        # Floor rings (bright fill stripe at each floor level)
        for raw in seg["floors"]:
            if raw not in y_vals: continue
            fy = y_vals[raw]
            if not (yB - 0.05 <= fy <= yT + 0.05): continue
            ew = bw + 0.005; ed = bd + 0.005
            corners = [(x-ew/2,fy,z-ed/2),(x+ew/2,fy,z-ed/2),
                       (x+ew/2,fy,z+ed/2),(x-ew/2,fy,z+ed/2)]
            pts2 = [proj(*c) for c in corners]
            xs = [p[0] for p in pts2] + [pts2[0][0]]
            ys = [p[1] for p in pts2] + [pts2[0][1]]
            ax.fill(xs, ys, color="#FF2222", alpha=0.65, zorder=7, linewidth=0)

    # ── MEP/WC vertical edges: dashed pink lines ───────────────────────────────
    mep_edges = compute_mep_vedges(h, v, floor_layouts, active_floors)
    for sp, tp in mep_edges:
        p1 = proj(sp[0], sp[1], sp[2])
        p2 = proj(tp[0], tp[1], tp[2])
        ax.plot([p1[0],p2[0]], [p1[1],p2[1]],
                color="#CC5599", linewidth=0.65, alpha=0.50,
                linestyle=(0,(3,2.5)), zorder=5)

    # ── Surrounding masses: wireframe style ───────────────────────────────────
    mass_ranges = {}
    for fl in h:
        raw = fl["floor_label"]
        if raw not in active_floors: continue
        for n in fl["nodes"]:
            if n["class_id"] == 18 and n["is_external_context"]:
                face = EXT_FACE.get(n["id"], "N")
                off  = EXT_OFF.get(n["id"], 1.3)
                key  = face
                fi   = FL_ORDER.index(raw)
                if key not in mass_ranges:
                    mass_ranges[key] = {"from":fi,"to":fi,"face":face,"off":off}
                else:
                    mass_ranges[key]["from"] = min(mass_ranges[key]["from"], fi)
                    mass_ranges[key]["to"]   = max(mass_ranges[key]["to"],   fi)

    for key, mr in mass_ranges.items():
        face = mr["face"]
        off  = mr["off"] * SC   # convert to axis units
        mw   = BLDG_HALF * 0.72 * 2  # same as web viewer
        h2   = mw / 2
        d    = {"N":(0,1),"S":(0,-1),"E":(1,0),"W":(-1,0)}[face]
        cx, cz = d[0]*off, d[1]*off

        # Floor range
        sfl = [FL_ORDER[i] for i in range(mr["from"], mr["to"]+1)
               if FL_ORDER[i] in active_floors]
        if not sfl: continue
        yB = FL_ORDER.index(sfl[0])  * GAP
        yT = FL_ORDER.index(sfl[-1]) * GAP

        # Draw floor rings (outline only, thin dark lines)
        for raw_f in sfl:
            fy_m = FL_ORDER.index(raw_f) * GAP
            corners = [(cx-h2,fy_m,cz-h2),(cx+h2,fy_m,cz-h2),
                       (cx+h2,fy_m,cz+h2),(cx-h2,fy_m,cz+h2)]
            pts2 = [proj(*c) for c in corners]
            xs = [p[0] for p in pts2] + [pts2[0][0]]
            ys = [p[1] for p in pts2] + [pts2[0][1]]
            ax.plot(xs, ys, color="#333333", linewidth=0.7, alpha=0.65, zorder=3)

        # Draw 4 vertical corner lines
        for dx, dz in [(-h2,-h2),(h2,-h2),(h2,h2),(-h2,h2)]:
            p1 = proj(cx+dx, yB, cz+dz)
            p2 = proj(cx+dx, yT, cz+dz)
            ax.plot([p1[0],p2[0]], [p1[1],p2[1]],
                    color="#333333", linewidth=0.7, alpha=0.60, zorder=3)

# ── Main figure generator ─────────────────────────────────────────────────────
def generate_axonometric(h, v, a, out_path,
                          title="ATLAS — Exploded Axonometric Bubble Diagram",
                          figsize=None, dpi=300):

    floor_layouts, _ = compute_layouts(h, v)
    EXT_FACE, EXT_OFF = compute_faces(h, floor_layouts)
    area_lu = build_area_lookup(a)

    # Determine active floors (those present in data)
    active = [fl["floor_label"] for fl in h]

    # Auto figsize: scale by number of floors
    n_fl = len(active)
    if figsize is None:
        # width: fixed ~14in; height: 2.2in per floor + 3in margins
        fw = 14
        fh = max(14, min(32, n_fl * 2.2 + 3.0))
        figsize = (fw, fh)

    fig, ax = plt.subplots(1,1, figsize=figsize)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")

    # Draw floors bottom→top
    n_floors = len(active)
    for idx, fl_data in enumerate(h):
        raw      = fl_data["floor_label"]
        fy_idx   = FL_ORDER.index(raw) if raw in FL_ORDER else idx
        fy       = fy_idx * GAP
        is_ground= raw in GROUND_FL
        # Upper floors fade gently
        fade = 1.0 if is_ground else max(0.55, 1 - 0.05*(fy_idx - FL_ORDER.index("1")))

        layout = floor_layouts.get(raw, {})
        draw_floor(ax, fl_data, raw, fy, layout, area_lu,
                   EXT_FACE, EXT_OFF,
                   is_ground=is_ground, fade=fade,
                   draw_ext_slab=True, draw_labels=True)

    # Vertical elements (cluster-based core boxes + MEP lines)
    draw_vertical_elements(ax, h, v, floor_layouts, area_lu,
                           EXT_FACE, EXT_OFF, set(active))

    # Title
    ax.set_title(title, fontsize=11, fontweight="bold",
                 color="#222222", pad=12)

    # Legend
    legend_items = []
    cls_seen = set()
    for fl in h:
        for n in fl["nodes"]:
            c = n["class_id"]
            if c not in cls_seen and c in CLS_LABEL:
                cls_seen.add(c)
                legend_items.append(mpatches.Patch(
                    color=CLS_COLOR.get(c,"#888"),
                    label=CLS_LABEL[c], alpha=0.85))

    # Edge legend
    legend_items += [
        mlines.Line2D([],[],color="#FF7700",linewidth=2.2,label="H-edge w=1.0"),
        mlines.Line2D([],[],color="#3A8DDD",linewidth=1.2,label="H-edge w=0.5"),
        mlines.Line2D([],[],color="#999",linewidth=0.6,linestyle="--",label="H-edge w=0"),
        mlines.Line2D([],[],color="#CC0000",linewidth=0.8,label="Core (vertical)"),
        mlines.Line2D([],[],color="#CC5599",linewidth=0.7,linestyle=(0,(3,3)),
                      label="MEP (vertical)"),
    ]

    ax.legend(handles=legend_items, loc="lower left",
              fontsize=6.5, framealpha=0.9, ncol=3,
              title="Node Type & Edge Weight",
              title_fontsize=7, edgecolor="#cccccc")

    plt.tight_layout(pad=0.5)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    print(f"  Saved: {out_path}")



# ── Draw single floor: top-down plan view ─────────────────────────────────────
def draw_floor_plan(ax, fl_data, raw, layout, area_lu,
                    EXT_FACE, EXT_OFF):
    """Top-down orthographic plan view for a single floor."""
    SC = 1/72
    bh = BLDG_HALF

    # Building outline
    for xs, ys in [
        ([-bh,bh,bh,-bh,-bh], [-bh,-bh,bh,bh,-bh])
    ]:
        ax.fill(xs, ys, color="#7A7060" if raw in GROUND_FL else "#CCCCDD",
                alpha=0.30, zorder=1, linewidth=0)
        ax.plot(xs, ys, color="#4A4030" if raw in GROUND_FL else "#8899BB",
                linewidth=1.0, alpha=0.85, zorder=2)

    # Floor label (bottom-left corner)
    ax.text(-bh-0.05, -bh-0.05, FL_LABEL.get(raw, raw),
            fontsize=9, color="#333", fontweight="bold",
            ha="right", va="top", zorder=10)

    # External context slabs
    is_ground = raw in GROUND_FL
    seen_ext = {}
    for n in fl_data["nodes"]:
        if not n["is_external_context"]: continue
        cls = n["class_id"]
        face = EXT_FACE.get(n["id"], "N")
        off  = EXT_OFF.get(n["id"], bh*72+25) / 72  # → axis units
        key  = f"{face}_{cls}"
        if key in seen_ext: continue
        seen_ext[key] = True

        d = {"N":(0,1),"S":(0,-1),"E":(1,0),"W":(-1,0)}[face]
        cx, cz = d[0]*off, d[1]*off
        slb_w = {2:0.38,14:0.28,16:0.34}.get(cls, 0.34)
        NS = face in ("N","S")
        hw = (bh+0.02) if NS else slb_w/2
        hd = slb_w/2   if NS else (bh+0.02)

        if cls == 18:  # surrounding mass — wireframe only
            # simple rectangle outline
            xs = [cx-hw,cx+hw,cx+hw,cx-hw,cx-hw]
            ys = [cz-hd,cz-hd,cz+hd,cz+hd,cz-hd]
            ax.plot(xs, ys, color="#444", linewidth=0.8,
                    linestyle="--", alpha=0.55, zorder=2)
            continue

        if n.get("viewOnly") and not is_ground: continue

        clr = "#111" if cls==2 else ("#444" if cls==14 else "#1D9E75")
        alp = 0.78   if cls==2 else (0.60   if cls==14 else 0.55)
        xs = [cx-hw,cx+hw,cx+hw,cx-hw,cx-hw]
        ys = [cz-hd,cz-hd,cz+hd,cz+hd,cz-hd]
        ax.fill(xs, ys, color=clr, alpha=alp, zorder=2, linewidth=0)
        ax.plot(xs, ys, color="#000" if cls==2 else "#0A6040",
                linewidth=0.5, alpha=0.8, zorder=3)

        # labels
        lname = {2:"Road",14:"Sec.Rd",16:"Open Sp."}
        if cls in lname:
            ax.text(cx, cz, lname[cls], fontsize=5.5, color="white",
                    ha="center", va="center", fontweight="bold", zorder=11)

    # Internal nodes (top-down: circles at XZ positions)
    nm = {}
    rm = {}
    for n in fl_data["nodes"]:
        if n["is_external_context"]: continue
        p = layout.get(n["id"])
        if p:
            nm[n["id"]] = p
            ak = raw+"::"+norm_key(n["id"])
            ar = area_lu.get(ak,{}).get("area_ratio",0.02)
            rm[n["id"]] = node_radius(ar, for_plan=True)

    # H-edges (top-down, with node-surface offset)
    for s,t,w in fl_data["edges"]:
        sp = nm.get(s); tp = nm.get(t)
        if not sp or not tp: continue
        r1 = rm.get(s,0.065); r2 = rm.get(t,0.065)
        sx,sz = sp[0]*SC, sp[1]*SC
        tx,tz = tp[0]*SC, tp[1]*SC
        dist = math.hypot(tx-sx, tz-sz)
        if dist <= r1+r2+0.001: continue
        ux,uz = (tx-sx)/dist, (tz-sz)/dist
        # offset from node surface
        p1 = (sx+ux*r1, sz+uz*r1)
        p2 = (tx-ux*r2, tz-uz*r2)
        if w >= 0.9:   lw,clr,al,ls = 2.0,"#FF7700",0.88,"-"
        elif w >= 0.4: lw,clr,al,ls = 1.1,"#3A8DDD",0.68,"-"
        else:          lw,clr,al,ls = 0.6,"#AA88CC",0.52,(0,(4,3))
        ax.plot([p1[0],p2[0]], [p1[1],p2[1]],
                color=clr, linewidth=lw, alpha=al, linestyle=ls,
                solid_capstyle="round", zorder=5)

    # Context edges (ext → internal nodes)
    for n in fl_data["nodes"]:
        if not n["is_external_context"] or n["class_id"]==18: continue
        face = EXT_FACE.get(n["id"],"N")
        off  = EXT_OFF.get(n["id"],bh*72+25)/72
        d    = {"N":(0,1),"S":(0,-1),"E":(1,0),"W":(-1,0)}[face]
        ep   = (d[0]*off, d[1]*off)
        for s,t,w in fl_data["edges"]:
            if s!=n["id"] and t!=n["id"]: continue
            oid = t if s==n["id"] else s
            tp2 = nm.get(oid)
            if not tp2: continue
            lc = "#111" if n["class_id"] in [2,14] else "#0D7050"
            lo = 0.55 if w>=0.7 else 0.30
            ax.plot([ep[0], tp2[0]*SC], [ep[1], tp2[1]*SC],
                    color=lc, linewidth=0.7, alpha=lo, zorder=4)

    # Nodes
    for nid, p in nm.items():
        n = next(x for x in fl_data["nodes"] if x["id"]==nid)
        r = rm[nid]
        clr = CLS_COLOR.get(n["class_id"],"#888888")
        circle = plt.Circle((p[0]*SC, p[1]*SC), r,
                             color=clr, alpha=0.90, zorder=7)
        ax.add_patch(circle)
        ax.add_patch(plt.Circle((p[0]*SC, p[1]*SC), r,
                                fill=False, ec="white",
                                linewidth=0.5, alpha=0.7, zorder=8))

    # Core markers (small cross at core node positions)
    for seg_cls, seg_xz in [(8,(-25.5,13.6)),(9,(20.4,20.4)),
                             (9,(-20.4,20.4)),(9,(-20.4,-20.4))]:
        x,z = seg_xz[0]*SC, seg_xz[1]*SC
        bw = 0.062 if seg_cls==8 else 0.076
        corners = [(x-bw/2,z-bw/2),(x+bw/2,z-bw/2),
                   (x+bw/2,z+bw/2),(x-bw/2,z+bw/2)]
        xs2 = [c[0] for c in corners]+[corners[0][0]]
        ys2 = [c[1] for c in corners]+[corners[0][1]]
        ax.fill(xs2, ys2, color="#FF2222", alpha=0.22, zorder=6)
        ax.plot(xs2, ys2, color="#CC0000", linewidth=0.8,
                alpha=0.85, zorder=7)

def generate_floor_diagram(h_floor, raw, layout, area_lu,
                           EXT_FACE, EXT_OFF, out_path,
                           title=None, dpi=250):
    """Single-floor top-down plan diagram (orthographic)."""
    bh = BLDG_HALF
    pad = 0.55  # padding around building
    fig, ax = plt.subplots(figsize=(9, 9))
    ax.set_aspect("equal"); ax.axis("off")
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # set limits with padding
    ax.set_xlim(-bh-pad, bh+pad)
    ax.set_ylim(-bh-pad, bh+pad)

    draw_floor_plan(ax, h_floor, raw, layout, area_lu, EXT_FACE, EXT_OFF)

    lbl = FL_LABEL.get(raw, raw)
    ax.set_title(title or f"Floor Plan — {lbl}", fontsize=11,
                 fontweight="bold", pad=10, color="#222")

    # legend (node types only — no edge types for single floor)
    legend_items = []
    cls_seen = set()
    for n in h_floor["nodes"]:
        c = n["class_id"]
        if c not in cls_seen and c in CLS_LABEL and not n["is_external_context"]:
            cls_seen.add(c)
            legend_items.append(mpatches.Patch(
                color=CLS_COLOR.get(c,"#888"), label=CLS_LABEL[c], alpha=0.88))
    legend_items += [
        mlines.Line2D([],[],color="#FF7700",linewidth=2.0,label="H-edge w=1.0"),
        mlines.Line2D([],[],color="#3A8DDD",linewidth=1.1,label="H-edge w=0.5"),
        mlines.Line2D([],[],color="#AA88CC",linewidth=0.6,
                      linestyle=(0,(4,3)),label="H-edge w=0"),
    ]
    ax.legend(handles=legend_items, loc="lower left",
              fontsize=6.5, framealpha=0.92, ncol=2,
              title=f"Floor: {lbl}", title_fontsize=7,
              edgecolor="#cccccc")

    plt.tight_layout(pad=0.3)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    print(f"  Saved: {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="ATLAS JSON → Publication Figure (PNG)")
    parser.add_argument("--input",  required=True,
        help="Path to building directory (contains *_graph.json)")
    parser.add_argument("--output", default="./figures",
        help="Output directory (default: ./figures)")
    parser.add_argument("--floors", default=None,
        help="Comma-separated floor labels to include, e.g. B2,B1,1,2,3")
    parser.add_argument("--single-floors", action="store_true",
        help="Also generate per-floor 2D diagrams")
    parser.add_argument("--dpi", type=int, default=300,
        help="Output DPI (default: 300)")
    parser.add_argument("--title", default=None,
        help="Figure title override")
    args = parser.parse_args()

    in_dir  = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    bldg_id = in_dir.name
    print(f"Processing: {bldg_id}")

    h, v, a = load_data(in_dir)

    # Filter floors if requested
    if args.floors:
        want = set(args.floors.split(","))
        h = [fl for fl in h if fl["floor_label"] in want]
        if not h:
            sys.exit(f"No floors matched: {args.floors}")

    floor_layouts, _ = compute_layouts(h, v)
    EXT_FACE, EXT_OFF = compute_faces(h, floor_layouts)
    area_lu = build_area_lookup(a)

    title = args.title or f"ATLAS — {bldg_id} (building_001)"

    # Full exploded axonometric
    generate_axonometric(h, v, a,
        out_path=out_dir/f"{bldg_id}_axo.png",
        title=title, dpi=args.dpi)

    # Per-floor diagrams
    if args.single_floors:
        for fl_data in h:
            raw = fl_data["floor_label"]
            lbl = FL_LABEL.get(raw, raw)
            layout = floor_layouts.get(raw, {})
            generate_floor_diagram(
                fl_data, raw, layout, area_lu,
                EXT_FACE, EXT_OFF,
                out_path=out_dir/f"{bldg_id}_floor_{lbl}.png",
                dpi=args.dpi)

    print(f"\nDone. Output: {out_dir}/")


if __name__ == "__main__":
    main()
