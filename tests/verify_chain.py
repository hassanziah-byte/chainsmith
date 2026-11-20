"""Verify ChainSmith joints: each piece's End pivot meets the next piece's Start pivot.
Run: ./run_blender.sh tests/verify_chain.py -- <chainsmith.blend>
"""
import bpy, sys, json, math, bmesh
from mathutils import Vector

blend = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(blend) as (s, d):
    d.node_groups = ["ChainSmith"]
ng = bpy.data.node_groups["ChainSmith"]
ids = {i.name: i.identifier for i in ng.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"}


def piece(name, length, ps, pe, with_pivots=True):
    """Box piece (8 verts) + a 4-vert loop around each pivot (verts 8-11 start, 12-15 end). Pivots are INSIDE the box."""
    me = bpy.data.meshes.new(name); bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts: v.co = Vector((v.co.x * length, v.co.y * 0.02, v.co.z * 0.02))
    loops = []
    for c in (ps, pe):
        vs = [bm.verts.new(Vector(c) + Vector(o)) for o in ((0, .004, .004), (0, -.004, .004), (0, -.004, -.004), (0, .004, -.004))]
        loops.append(vs)
    bm.to_mesh(me); bm.free()
    ob = bpy.data.objects.new(name, me); bpy.context.scene.collection.objects.link(ob)
    if with_pivots:
        for nm, idx in (("pivot_start", range(8, 12)), ("pivot_end", range(12, 16))):
            ob.vertex_groups.new(name=nm).add(list(idx), 1.0, "REPLACE")
    return ob


link = piece("Link", 0.1, (-0.04, 0, 0), (0.04, 0, 0))          # pitch 0.08
scap = piece("StartCap", 0.08, (-0.03, 0, 0), (0.03, 0, 0))     # pitch 0.06
ecap = piece("EndCap", 0.07, (-0.025, 0, 0), (0.025, 0, 0))     # pitch 0.05

def curve_line(length=1.0):
    cu = bpy.data.curves.new("ln", "CURVE"); cu.dimensions = "3D"
    sp = cu.splines.new("POLY"); sp.points.add(1)
    sp.points[0].co = (0, 0, 0, 1); sp.points[1].co = (length, 0, 0, 1)
    o = bpy.data.objects.new("Line", cu); bpy.context.scene.collection.objects.link(o); return o

line = curve_line(1.0)
bpy.ops.curve.primitive_bezier_circle_add(radius=0.1); circ = bpy.context.object
bpy.ops.curve.primitive_bezier_circle_add(radius=0.1, location=(0, 0, 0)); lifted = bpy.context.object
p = lifted.data.splines[0].bezier_points[1]
for a in ("co", "handle_left", "handle_right"):
    v = getattr(p, a); v.z += 0.06

mod = link.modifiers.new("ChainSmith", "NODES"); mod.node_group = ng
P = mod.properties.inputs
DEF = {"Count": 6, "Measure Gap": "Pivot to Pivot", "Gap": 0.0, "Start Offset": 0.0, "Lock X": False, "Lock Y": False,
       "Lock Z": False, "Show Pivots": False, "Realize Instances": True, "Accumulate": True, "Copy Offset": (0, 0, 0),
       "Copy Rotation": (0, 0, 0), "Copy Scale": (1, 1, 1), "Start Cap": False, "End Cap": False,
       "Start Cap Object": scap, "End Cap Object": ecap, "Bend In": math.pi, "Bend Out": math.pi}

def run(curve, **kw):
    getattr(P, ids["Curve Object"]).value = curve
    for k, v in {**DEF, **{k.replace("_", " "): v for k, v in kw.items()}}.items():
        getattr(P, ids[k]).value = v
    bpy.context.view_layer.update()
    ev = link.evaluated_get(bpy.context.evaluated_depsgraph_get()); m = ev.to_mesh()
    vs = [v.co.copy() for v in m.vertices]; ev.to_mesh_clear()
    pieces = [vs[i:i + 16] for i in range(0, len(vs), 16)]
    S = [sum(pc[8:12], Vector()) / 4 for pc in pieces]
    E = [sum(pc[12:16], Vector()) / 4 for pc in pieces]
    joints = [round((E[i] - S[i + 1]).length, 6) for i in range(len(pieces) - 1)]
    return {"pieces": len(pieces), "max_joint_gap": max(joints) if joints else None, "S": S, "E": E,
            "warnings": [w.message for w in mod.node_warnings]}

R = {}
r = run(line); R["line"] = {k: r[k] for k in ("pieces", "max_joint_gap")}; R["line"]["S_x"] = [round(s.x, 4) for s in r["S"]]
r = run(circ); R["circle"] = {k: r[k] for k in ("pieces", "max_joint_gap", "warnings")}
R["circle"]["joint_radii"] = sorted({round(s.length, 4) for s in r["S"]} | {round(e.length, 4) for e in r["E"]})
r = run(circ, Start_Cap=True, End_Cap=True); R["caps"] = {k: r[k] for k in ("pieces", "max_joint_gap")}
R["caps"]["pitches"] = [round((r["E"][i] - r["S"][i]).length, 4) for i in range(r["pieces"])]
r = run(line, Count=20); R["extend_past_end"] = {"pieces": r["pieces"], "max_joint_gap": r["max_joint_gap"],
                                                 "last_S": [round(c, 4) for c in r["S"][-1]]}
r = run(circ, Copy_Rotation=(math.radians(90), 0, 0)); R["roll90_accum"] = {k: r[k] for k in ("pieces", "max_joint_gap")}
r = run(circ, Copy_Scale=(0.9, 0.9, 0.9)); R["scale09_accum"] = {k: r[k] for k in ("pieces", "max_joint_gap")}
R["scale09_accum"]["pitches"] = [round((r["E"][i] - r["S"][i]).length, 4) for i in range(r["pieces"])]
r = run(circ, Gap=0.01); R["gap_0.01"] = {"joint_gaps": [round((r["E"][i] - r["S"][i + 1]).length, 4) for i in range(r["pieces"] - 1)]}
r = run(lifted); R["lifted_no_lock"] = {"z": sorted({round(s.z, 3) for s in r["S"]})}
r = run(lifted, Lock_Z=True); R["lifted_lock_z"] = {"z": sorted({round(s.z, 3) for s in r["S"]}), "max_joint_gap": r["max_joint_gap"]}
def boxes(curve, **kw):
    run(curve, **kw)
    ev = link.evaluated_get(bpy.context.evaluated_depsgraph_get()); m = ev.to_mesh()
    vs = [v.co.copy() for v in m.vertices]; ev.to_mesh_clear()
    pcs = [vs[i:i + 16] for i in range(0, len(vs), 16)]
    spans = [(min(v.x for v in pc[:8]), max(v.x for v in pc[:8])) for pc in pcs]
    return spans
sp = boxes(line, Measure_Gap="Edge to Edge", Gap=0.01)
R["edge_to_edge_gaps"] = sorted({round(sp[i + 1][0] - sp[i][1], 5) for i in range(len(sp) - 1)})
sp = boxes(line, Measure_Gap="Center to Center", Gap=0.15)
R["center_to_center"] = sorted({round((sp[i + 1][0] + sp[i + 1][1]) / 2 - (sp[i][0] + sp[i][1]) / 2, 5) for i in range(len(sp) - 1)})
import time
for n in (100, 500):
    t0 = time.time(); r = run(circ, Count=n); R[f"time_{n}_links_s"] = round(time.time() - t0, 2); R[f"gap_{n}"] = r["max_joint_gap"]
# warnings
for vg in list(link.vertex_groups): link.vertex_groups.remove(vg)
r = run(circ); R["no_pivots_warning"] = r["warnings"]
print("JSON>>" + json.dumps(R))
