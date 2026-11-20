"""Install the ChainSmith zip, set pivots from an Edit Mode selection with the operators, add the modifier.
Run: ./run_blender.sh tests/verify_extension.py -- <chainsmith-x.y.z.zip>
"""
import bpy, sys, json, bmesh
from mathutils import Vector

zp = sys.argv[sys.argv.index("--") + 1]
R = {"install": list(bpy.ops.extensions.package_install_files(filepath=zp, repo="user_default", enable_on_install=True))}
R["enabled"] = [k for k in bpy.context.preferences.addons.keys() if "chainsmith" in k]

# a link: box + two 4-vert loops inside it (joint holes) at x = -0.04 and +0.04
bpy.ops.mesh.primitive_cube_add(size=1); ob = bpy.context.object
ob.scale = (0.1, 0.02, 0.02); bpy.ops.object.transform_apply(scale=True)
bm = bmesh.new(); bm.from_mesh(ob.data)
for cx in (-0.04, 0.04):
    for o in ((0, .004, .004), (0, -.004, .004), (0, -.004, -.004), (0, .004, -.004)):
        bm.verts.new(Vector((cx, 0, 0)) + Vector(o))
bm.to_mesh(ob.data); bm.free()

def select_verts(pred):
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(ob.data)
    for v in bm.verts: v.select = pred(v.co)
    bm.select_flush(True); bmesh.update_edit_mesh(ob.data)

select_verts(lambda c: abs(c.x + 0.04) < 1e-6)
R["set_start"] = list(bpy.ops.mesh.chainsmith_set_pivot(which="START"))
select_verts(lambda c: abs(c.x - 0.04) < 1e-6)
R["set_end"] = list(bpy.ops.mesh.chainsmith_set_pivot(which="END"))
select_verts(lambda c: False)
R["set_empty"] = list(bpy.ops.mesh.chainsmith_set_pivot(which="END"))   # nothing selected -> cancelled, pivots kept
bpy.ops.object.mode_set(mode="OBJECT")
R["groups"] = {vg.name: sorted(i for i, v in enumerate(ob.data.vertices) for g in v.groups if g.group == vg.index)
               for vg in ob.vertex_groups}

R["add"] = list(bpy.ops.object.chainsmith_add())
md = ob.modifiers[-1]; ng = md.node_group
ids = {i.name: i.identifier for i in ng.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"}
bpy.ops.curve.primitive_bezier_circle_add(radius=0.1); circ = bpy.context.object
getattr(md.properties.inputs, ids["Curve Object"]).value = circ
getattr(md.properties.inputs, ids["Count"]).value = 7
bpy.context.view_layer.update()
ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()); m = ev.to_mesh()
vs = [v.co.copy() for v in m.vertices]; ev.to_mesh_clear()
pcs = [vs[i:i + 16] for i in range(0, len(vs), 16)]
S = [sum(pc[8:12], Vector()) / 4 for pc in pcs]; E = [sum(pc[12:16], Vector()) / 4 for pc in pcs]
R["pieces"] = len(pcs); R["max_joint_gap"] = round(max((E[i] - S[i + 1]).length for i in range(len(pcs) - 1)), 6)
R["warnings"] = [w.message for w in md.node_warnings]
R["measure_gap_items"] = [e.identifier for e in getattr(md.properties.inputs, ids["Measure Gap"]).bl_rna.properties["value"].enum_items]
bpy.context.view_layer.objects.active = ob
R["clear"] = list(bpy.ops.mesh.chainsmith_clear_pivots()); R["groups_after_clear"] = [vg.name for vg in ob.vertex_groups]
bpy.context.view_layer.update(); ob.evaluated_get(bpy.context.evaluated_depsgraph_get())
R["warnings_after_clear"] = [w.message[:40] for w in md.node_warnings]
print("JSON>>" + json.dumps(R))
