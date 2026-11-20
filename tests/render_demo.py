"""Render the README demo images with generic stand-in parts (no real product models).
Builds watch links, end caps and a stand-in fold-over clasp (body + two blades), then renders
closed, clasp close-up and open (wrist path scaled up).
Run: ./run_blender.sh tests/render_demo.py -- <chainsmith.blend> <out_dir>
"""
import bpy, bmesh, sys, os, math
from mathutils import Vector, Matrix

a = sys.argv[sys.argv.index("--") + 1:]
blend, out = a[0], a[1]
bpy.ops.wm.read_factory_settings(use_empty=True)
with bpy.data.libraries.load(blend) as (s, d):
    d.node_groups = ["ChainSmith"]
ng = bpy.data.node_groups["ChainSmith"]
ids = {i.name: i.identifier for i in ng.interface.items_tree if i.item_type == "SOCKET" and i.in_out == "INPUT"}
sc = bpy.context.scene
mm = 0.001


def metal(name, color, rough):
    m = bpy.data.materials.new(name); m.use_nodes = True
    p = m.node_tree.nodes["Principled BSDF"]
    p.inputs["Base Color"].default_value = (*color, 1); p.inputs["Metallic"].default_value = 1.0
    p.inputs["Roughness"].default_value = rough
    return m


STEEL = metal("Steel", (0.62, 0.63, 0.66), 0.32)
BRUSHED = metal("Gold", (0.8, 0.55, 0.25), 0.3)
DARK = metal("Dark Steel", (0.2, 0.2, 0.22), 0.35)


def obj(name, bm, mat, pins, bevel=0.35 * mm):
    """pins: (start_xy, end_xy) -> 8-vert loose rings (both pin ends) weighted into pivot_start / pivot_end."""
    for f in bm.faces: f.smooth = True
    dl = bm.verts.layers.deform.verify()  # before creating the ring verts (adding a layer invalidates BMVerts)
    for g, (cx, cy) in enumerate(pins):   # deform group 0 = pivot_start, 1 = pivot_end
        for zc in (-10.1 * mm, 10.1 * mm):
            for k in range(8):
                t = 2 * math.pi * k / 8
                v = bm.verts.new((cx + 0.5 * mm * math.cos(t), cy + 0.5 * mm * math.sin(t), zc)); v[dl][g] = 1.0
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free(); me.materials.append(mat)
    o = bpy.data.objects.new(name, me); sc.collection.objects.link(o)
    o.vertex_groups.new(name="pivot_start"); o.vertex_groups.new(name="pivot_end")
    if bevel:
        bv = o.modifiers.new("Bevel", "BEVEL"); bv.width = bevel; bv.segments = 3; bv.harden_normals = True
    return o


def box(bm, c, size, rot_z=0.0):
    r = bmesh.ops.create_cube(bm, size=1.0)
    m = Matrix.Translation(c) @ Matrix.Rotation(rot_z, 4, "Z") @ Matrix.Diagonal((*size, 1))
    bmesh.ops.transform(bm, matrix=m, verts=r["verts"])


def barrel(bm, c, r, h):
    bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=r, radius2=r, depth=h, matrix=Matrix.Translation(c))


def plate_between(bm, p, q, thick, width, side):
    """Flat plate from pin p to pin q (XY), offset to one side of the p->q line."""
    p, q = Vector((*p, 0)), Vector((*q, 0)); d = q - p
    n = Vector((-d.y, d.x, 0)).normalized() * side
    box(bm, (p + q) / 2 + n * (thick / 2 + 0.2 * mm), (d.length, thick, width), math.atan2(d.y, d.x))


# ---- links (X along the chain, Z along the pins, +Y toward the wrist)
bm = bmesh.new(); box(bm, (0, 0, 0), (8.6 * mm, 2.4 * mm, 20 * mm)); barrel(bm, (4.5 * mm, 0, 0), 1.0 * mm, 19.2 * mm)
link = obj("Link", bm, STEEL, ((-4.5 * mm, 0), (4.5 * mm, 0)))
bm = bmesh.new(); box(bm, (0, 0, 0), (12.6 * mm, 2.6 * mm, 22 * mm)); barrel(bm, (6.5 * mm, 0, 0), 1.0 * mm, 21 * mm)
cap = obj("Cap", bm, BRUSHED, ((-6.5 * mm, 0), (6.5 * mm, 0)))

# ---- stand-in fold-over clasp, modeled CLOSED (pins along Z; +Y = inside). Generic pin layout.
A, H2, H1, B = (-10.6 * mm, -31.7 * mm), (25.8 * mm, -21.9 * mm), (-7.5 * mm, -31.3 * mm), (3.5 * mm, -32.4 * mm)
bm = bmesh.new()
box(bm, ((H1[0] + B[0]) / 2, -33.6 * mm, 0), (13.5 * mm, 3.0 * mm, 23 * mm))          # lock body, outside
box(bm, ((H1[0] + B[0]) / 2, -35.2 * mm, 0), (6 * mm, 0.6 * mm, 2.2 * mm))             # push button
for p in (H1, B): barrel(bm, (*p, 0), 0.7 * mm, 23.6 * mm)
body = obj("Clasp Body", bm, BRUSHED, (H1, B))
bm = bmesh.new(); plate_between(bm, H1, H2, 0.6 * mm, 18 * mm, -1)
for p in (H1, H2): barrel(bm, (*p, 0), 1.0 * mm, 18 * mm)
inner = obj("Clasp Inner Blade", bm, DARK, (H2, H1))
bm = bmesh.new(); plate_between(bm, A, H2, 0.8 * mm, 21 * mm, 1)
for p in (A, H2): barrel(bm, (*p, 0), 1.0 * mm, 21.4 * mm)
outer = obj("Clasp Outer Blade", bm, STEEL, (A, H2))
for o in (cap, body, inner, outer): o.hide_render = True

# ---- bracelet
NL = 18
loop = NL * 9 * mm + 2 * 13 * mm + math.dist(A, B)
bpy.ops.curve.primitive_bezier_circle_add(radius=loop / (2 * math.pi)); path = bpy.context.object
md = link.modifiers.new("ChainSmith", "NODES"); md.node_group = ng
for k, v in {"Curve Object": path, "Count": NL, "Start Cap": True, "Start Cap Object": cap, "End Cap": True, "End Cap Object": cap,
             "Clasp": True, "Clasp Body": body, "Clasp Inner Blade": inner, "Clasp Outer Blade": outer}.items():
    getattr(md.properties.inputs, ids[k]).value = v

# ---- look
w = bpy.data.worlds.new("w"); sc.world = w; w.use_nodes = True
bg = w.node_tree.nodes["Background"]; bg.inputs["Color"].default_value = (0.07, 0.07, 0.075, 1)
for nm, loc, e, sz in (("Key", (0.12, -0.15, 0.18), 25, 0.12), ("Fill", (-0.18, -0.05, 0.08), 6, 0.15), ("Rim", (0, 0.2, 0.12), 20, 0.1)):
    L = bpy.data.objects.new(nm, bpy.data.lights.new(nm, "AREA")); sc.collection.objects.link(L)
    L.data.energy, L.data.size, L.location = e, sz, loc
    L.rotation_euler = (-Vector(loc)).to_track_quat("-Z", "Y").to_euler()
cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam")); sc.collection.objects.link(cam); sc.camera = cam
cam.data.clip_start = 0.001
sc.render.engine = "BLENDER_EEVEE"; sc.view_settings.view_transform = "AgX"
sc.render.resolution_x, sc.render.resolution_y = 1400, 1050

bpy.context.view_layer.update()
ev = link.evaluated_get(bpy.context.evaluated_depsgraph_get())
print("WARNINGS", [x.message for x in md.node_warnings])
# clasp sits where the chain closes: the start of the path (+X for a bezier circle) - aim there
cx = path.data.splines[0].bezier_points[0].co.x


def shoot(name, loc, look, lens=70):
    cam.location = loc; cam.data.lens = lens
    cam.rotation_euler = (Vector(look) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    sc.render.filepath = os.path.join(out, name); bpy.ops.render.render(write_still=True)


s = 1 if cx > 0 else -1
shoot("rig_closed.png", (s * 0.22, -0.16, 0.14), (0.0, 0, -0.006))
shoot("rig_closeup.png", (s * 0.12, -0.07, 0.045), (s * 0.035, -0.005, 0.0), lens=80)
path.scale = (1.15, 1.15, 1.15); bpy.context.view_layer.update()
shoot("rig_open.png", (s * 0.25, -0.18, 0.16), (0.004, 0, -0.006))
print("RENDERED", out)
