"""Verify the ChainSmith rig: closed loop with a fold-over clasp, rigid joints, bend limits, no sliding.
Clasp stand-in uses a typical push-button fold-over clasp pin layout.
Run: ./run_blender.sh tests/verify_rig.py -- <chainsmith.blend>
"""
import bpy, sys, json, math, bmesh, time
from itertools import permutations
from mathutils import Vector

blend = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(blend) as (s, d):
    d.node_groups = ["ChainSmith"]
ng = bpy.data.node_groups["ChainSmith"]
ids = {i.name: i.identifier for i in ng.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"}


def part(name, p, q, extra=0, swap=False):
    """Small box + a 4-vert loop around each pin (verts 8-11 = pivot_start, 12-15 = pivot_end) + `extra` id verts."""
    me = bpy.data.meshes.new(name); bm = bmesh.new()
    c = (Vector(p) + Vector(q)) / 2
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts: v.co = c + Vector((v.co.x * 0.004, v.co.y * 0.004, v.co.z * 0.004))
    for pin in (p, q):
        for o in ((0.0008, 0.0008, 0), (-0.0008, 0.0008, 0), (-0.0008, -0.0008, 0), (0.0008, -0.0008, 0)):
            bm.verts.new(Vector(pin) + Vector(o))
    for k in range(extra): bm.verts.new(c + Vector((0, 0, 0.001 * (k + 1))))
    bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(ob)
    a, b_ = (range(12, 16), range(8, 12)) if swap else (range(8, 12), range(12, 16))
    ob.vertex_groups.new(name="pivot_start").add(list(a), 1.0, "REPLACE")
    ob.vertex_groups.new(name="pivot_end").add(list(b_), 1.0, "REPLACE")
    return ob


# links lie along X, pins along Z (bracelet in the XY plane)
link = part("Link", (-0.0045, 0, 0), (0.0045, 0, 0))                       # pitch 0.009
# clasp pins (closed pose), typical layout; pivots deliberately in mixed order
A, H2, H1, Bp = (-0.01068, -0.03167, 0), (0.0258, -0.02196, 0), (-0.00747, -0.03129, 0), (0.00346, -0.03231, 0)
body = part("Body", Bp, H1, extra=1)                 # reversed on purpose: order must not matter
inner = part("Inner", H2, H1, extra=2, swap=True)
outer = part("Outer", A, H2, extra=3)
SIZES = {"Body": 17, "Inner": 18, "Outer": 19}

NL = 18
L_CLOSED = 0.009 * NL + math.dist(A, Bp)


def circle(name, r):
    bpy.ops.curve.primitive_bezier_circle_add(radius=r); o = bpy.context.object; o.name = name; return o


mod = link.modifiers.new("ChainSmith", "NODES"); mod.node_group = ng
P = mod.properties.inputs
DEF = {"Count": NL, "Measure Gap": "Pivot to Pivot", "Gap": 0.0, "Start Offset": 0.0, "Realize Instances": True,
       "Clasp": True, "Clasp Body": body, "Clasp Inner Blade": inner, "Clasp Outer Blade": outer, "Clasp Open Limit": 1.0,
       "Bend In": math.radians(35), "Bend Out": math.radians(8), "Solver Iterations": 60}


def run(curve, **kw):
    getattr(P, ids["Curve Object"]).value = curve
    for k, v in {**DEF, **{k.replace("_", " "): v for k, v in kw.items()}}.items():
        getattr(P, ids[k]).value = v
    bpy.context.view_layer.update()
    t0 = time.perf_counter()
    ev = link.evaluated_get(bpy.context.evaluated_depsgraph_get()); m = ev.to_mesh()
    vs = [v.co.copy() for v in m.vertices]; ev.to_mesh_clear()
    dt = time.perf_counter() - t0
    nchain = 16 * kw.get("Count", NL)
    chain, tail = vs[:nchain], vs[nchain:]
    pcs = [chain[i:i + 16] for i in range(0, nchain, 16)]
    S = [sum(pc[8:12], Vector()) / 4 for pc in pcs]; E = [sum(pc[12:16], Vector()) / 4 for pc in pcs]
    out = {"warnings": [w.message for w in mod.node_warnings], "t": round(dt, 3)}
    out["max_link_gap"] = round(max((E[i] - S[i + 1]).length for i in range(len(pcs) - 1)), 7)
    # bends (+ = toward the circle center), between consecutive links
    th = []
    for i in range(1, len(pcs)):
        e1, e2 = E[i - 1] - S[i - 1], E[i] - S[i]
        th.append(math.degrees(math.atan2(e1.cross(e2).z, e1.dot(e2))))
    out["bend_deg"] = (round(min(th), 2), round(max(th), 2))
    out["radii"] = (round(min(s.length for s in S), 4), round(max(s.length for s in S), 4))
    out["first_S"], out["last_E"] = S[0], E[-1]
    if kw.get("Clasp", True) and len(tail) == 54:
        best = None
        for order in permutations(("Body", "Inner", "Outer")):   # realized order of the 3 parts is not fixed: try all
            blocks, k = {}, 0
            for nm in order: blocks[nm] = tail[k:k + SIZES[nm]]; k += SIZES[nm]
            piv = {n: (sum(v[8:12], Vector()) / 4, sum(v[12:16], Vector()) / 4) for n, v in blocks.items()}
            Bw, H1b = piv["Body"]; H2i, H1i = piv["Inner"]; Aw, H2o = piv["Outer"]   # vertex order of the stand-ins
            gaps = {"A-endlink": (Aw - E[-1]).length, "B-startlink": (Bw - S[0]).length,
                    "H1": (H1b - H1i).length, "H2": (H2i - H2o).length}
            if best is None or max(gaps.values()) < max(best[0].values()):
                best = (gaps, Aw, Bw, H2o)
        gaps, Aw, Bw, H2o = best
        out["clasp_gaps"] = {k_: round(v, 7) for k_, v in gaps.items()}
        out["span"] = round((Bw - Aw).length, 5)
        out["blades_inside"] = H2o.to_2d().length < ((Aw + Bw) / 2).to_2d().length
    return out


R = {}
c_fit = circle("fit", L_CLOSED / (2 * math.pi))
c_big = circle("big", 1.15 * L_CLOSED / (2 * math.pi))
c_huge = circle("huge", 1.6 * L_CLOSED / (2 * math.pi))
c_small = circle("small", 0.9 * L_CLOSED / (2 * math.pi))
for nm, c in (("fit", c_fit), ("big_15pct", c_big), ("huge_60pct", c_huge), ("small_10pct", c_small)):
    r = run(c); r.pop("first_S"); r.pop("last_E"); R[nm] = r
R["closed_span_expected"] = round(math.dist(A, Bp), 5)

# open limit 0: clasp must stay closed even on the big curve
r = run(c_big, Clasp_Open_Limit=0.0); R["big_open_limit_0"] = {k: r[k] for k in ("span", "clasp_gaps", "max_link_gap") if k in r}

# bend limit: tight circle needs ~40 deg per joint; limit 20 -> bends capped, chain leaves the curve
c_tight = circle("tight", 0.009 * 9 / (2 * math.pi) * 1.0)
r = run(c_tight, Clasp=False, Count=9, Bend_In=math.radians(20)); R["tight_limit_20"] = {k: r[k] for k in ("bend_deg", "max_link_gap", "radii")}

# no sliding: bump the curve point opposite the clasp, the clasp end should barely move
c_bump = circle("bump", 1.15 * L_CLOSED / (2 * math.pi))
r0 = run(c_bump)
bp = c_bump.data.splines[0].bezier_points[2]  # opposite the start (point 0)
for a in ("co", "handle_left", "handle_right"):
    v = getattr(bp, a); v.x *= 1.15
r1 = run(c_bump)
R["bump_far"] = {"start_moved": round((r0["first_S"] - r1["first_S"]).length, 5), "end_moved": round((r0["last_E"] - r1["last_E"]).length, 5),
                 "clasp_gaps": r1.get("clasp_gaps")}

# no clasp: open chain still exact, limits wide -> on the curve
r = run(c_fit, Clasp=False, Bend_In=math.pi, Bend_Out=math.pi)
R["no_clasp"] = {k: r[k] for k in ("max_link_gap", "radii", "warnings")}

# missing pivots warning
r = run(c_fit, Clasp_Body=None); R["no_body_warning"] = r["warnings"]

# timing, 300 links
big300 = circle("c300", (0.009 * 300 + math.dist(A, Bp)) / (2 * math.pi) * 1.05)
r = run(big300, Count=300); R["t_300"] = r["t"]; R["gap_300"] = r["max_link_gap"]; R["clasp_300"] = r.get("clasp_gaps")
print("JSON>>" + json.dumps(R, default=str))
