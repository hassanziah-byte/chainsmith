"""ChainSmith: pivot-linked chains, bracelets and straps along a curve.

- Edit Mode > right-click > ChainSmith > Set Start Pivot / Set End Pivot: the pivot is the center of the
  selected vertices (e.g. select the loop inside a link's hole), stored as vertex groups
  "pivot_start" / "pivot_end" so it moves with the mesh.
- Object Mode > Add Modifier > Generate > ChainSmith: adds the ChainSmith Geometry Nodes modifier.
"""
import bpy
import bmesh
import os

GROUP = "ChainSmith"
GROUP_VERSION = 3  # must match build_chainsmith.GROUP_VERSION
BLEND = os.path.join(os.path.dirname(__file__), "chainsmith.blend")
PIVOTS = {"START": "pivot_start", "END": "pivot_end"}


# ------------------------------------------------------------------------------------------------ node group
def get_group():
    """Return the file's ChainSmith group, appending it (or upgrading an older copy in place)."""
    old = bpy.data.node_groups.get(GROUP)
    if old and not old.library and old.get("chainsmith_version", 0) >= GROUP_VERSION:
        return old
    with bpy.data.libraries.load(BLEND, link=False) as (src, dst):
        dst.node_groups = [GROUP]
    new = dst.node_groups[0]
    if old and not old.library:
        nested_old = {n.node_tree for n in old.nodes if getattr(n, "node_tree", None)}
        old.user_remap(new)
        bpy.data.node_groups.remove(old)
        new.name = GROUP
        for g in nested_old:
            if g.users - int(g.use_fake_user) == 0:
                base = g.name
                bpy.data.node_groups.remove(g)
                for n in new.nodes:
                    t = getattr(n, "node_tree", None)
                    if t and t.name.rsplit(".", 1)[0] == base and base not in bpy.data.node_groups:
                        t.name = base
    return new


class OBJECT_OT_chainsmith_add(bpy.types.Operator):
    """Add the ChainSmith modifier (repeat this object along a curve, joined pivot to pivot)"""
    bl_idname = "object.chainsmith_add"
    bl_label = "ChainSmith"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and ob.type == "MESH"

    def execute(self, context):
        ng = get_group()
        targets = [o for o in context.selected_objects if o.type == "MESH"] or [context.object]
        for ob in targets:
            mod = ob.modifiers.new(GROUP, "NODES")
            mod.node_group = ng
            ob.modifiers.active = mod
        return {"FINISHED"}


# ------------------------------------------------------------------------------------------------ pivots
def set_pivot_from_selection(ob, which):
    """Store the selected vertices of an edit-mode mesh as the pivot vertex group. Returns (count, center)."""
    name = PIVOTS[which]
    me = ob.data
    bm = bmesh.from_edit_mesh(me)
    sel = [v.co.copy() for v in bm.verts if v.select]
    if not sel:
        return 0, None
    count, center = len(sel), sum(sel[1:], sel[0]) / len(sel)  # before verify(): adding the layer invalidates BMVerts
    vg = ob.vertex_groups.get(name) or ob.vertex_groups.new(name=name)
    dl = bm.verts.layers.deform.verify()
    for v in bm.verts:
        d = v[dl]
        if v.select:
            d[vg.index] = 1.0
        elif vg.index in d:
            del d[vg.index]
    bmesh.update_edit_mesh(me)
    return count, center


class MESH_OT_chainsmith_set_pivot(bpy.types.Operator):
    """Set this piece's pivot to the center of the selected vertices (e.g. the loop inside a link's hole)"""
    bl_idname = "mesh.chainsmith_set_pivot"
    bl_label = "Set Pivot"
    bl_options = {"REGISTER", "UNDO"}

    which: bpy.props.EnumProperty(items=[("START", "Start", "Joint toward the start of the chain"),
                                         ("END", "End", "Joint toward the end of the chain")])

    @classmethod
    def description(cls, context, props):
        return ("Set the START pivot (joint toward the chain start)" if props.which == "START"
                else "Set the END pivot (joint toward the chain end)") + " to the center of the selected vertices"

    @classmethod
    def poll(cls, context):
        return context.mode == "EDIT_MESH" and context.edit_object is not None

    def execute(self, context):
        ob = context.edit_object
        n, c = set_pivot_from_selection(ob, self.which)
        if not n:
            self.report({"WARNING"}, "Select the vertices around the joint first")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{self.which.title()} pivot set from {n} vertices at ({c.x:.4g}, {c.y:.4g}, {c.z:.4g})")
        return {"FINISHED"}


class MESH_OT_chainsmith_clear_pivots(bpy.types.Operator):
    """Remove this piece's Start and End pivots"""
    bl_idname = "mesh.chainsmith_clear_pivots"
    bl_label = "Clear Pivots"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        ob = context.object
        return ob is not None and ob.type == "MESH"

    def execute(self, context):
        ob = context.object
        was_edit = ob.mode == "EDIT"
        if was_edit:
            bpy.ops.object.mode_set(mode="OBJECT")
        removed = 0
        for name in PIVOTS.values():
            vg = ob.vertex_groups.get(name)
            if vg:
                ob.vertex_groups.remove(vg); removed += 1
        if was_edit:
            bpy.ops.object.mode_set(mode="EDIT")
        self.report({"INFO"}, f"Removed {removed} pivot(s)")
        return {"FINISHED"}


class VIEW3D_MT_chainsmith_pivots(bpy.types.Menu):
    bl_label = "ChainSmith"
    bl_idname = "VIEW3D_MT_chainsmith_pivots"

    def draw(self, context):
        l = self.layout
        l.operator(MESH_OT_chainsmith_set_pivot.bl_idname, text="Set Start Pivot").which = "START"
        l.operator(MESH_OT_chainsmith_set_pivot.bl_idname, text="Set End Pivot").which = "END"
        l.separator()
        l.operator(MESH_OT_chainsmith_clear_pivots.bl_idname)


def edit_menu(self, context):
    self.layout.separator()
    self.layout.menu(VIEW3D_MT_chainsmith_pivots.bl_idname)


def add_modifier_menu(self, context):
    self.layout.operator(OBJECT_OT_chainsmith_add.bl_idname, icon="LINKED")


CLASSES = (OBJECT_OT_chainsmith_add, MESH_OT_chainsmith_set_pivot, MESH_OT_chainsmith_clear_pivots,
           VIEW3D_MT_chainsmith_pivots)
MENUS = (("VIEW3D_MT_edit_mesh_context_menu", edit_menu), ("VIEW3D_MT_edit_mesh_vertices", edit_menu),
         ("OBJECT_MT_modifier_add_generate", add_modifier_menu))


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    for menu, fn in MENUS:
        m = getattr(bpy.types, menu, None)
        if m:
            m.append(fn)


def unregister():
    for menu, fn in MENUS:
        m = getattr(bpy.types, menu, None)
        if m:
            m.remove(fn)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
