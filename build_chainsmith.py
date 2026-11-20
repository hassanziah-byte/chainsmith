"""Build the "ChainSmith" Geometry Nodes group: pivot-linked chains along a curve.

Places links (and optional start/end caps) along a curve so each piece's End pivot meets the next piece's
Start pivot, measured as a straight-line (chord) distance so joints meet exactly on tight bends.
Pivots come from vertex groups "pivot_start" / "pivot_end" on each piece (set with the add-on's
Edit Mode tools), or from the piece's bounding box (Edge to Edge) / origin (Center to Center).

Run headless (use run_blender.sh so your real Blender profile is untouched):
  ./run_blender.sh build_chainsmith.py -- <out.blend>
"""
import bpy, sys, os

GROUP = "ChainSmith"
GROUP_VERSION = 3  # bump on every node-group change; the add-on upgrades older copies in a .blend
MODES = ["Pivot to Pivot", "Edge to Edge", "Center to Center"]
NEWTON_STEPS = 5


# ------------------------------------------------------------------------------------------------ helpers
class B:
    """Tiny node-building helper. Values may be sockets (linked) or constants (default_value)."""

    def __init__(self, ng):
        self.ng, self.N, self.L = ng, ng.nodes, ng.links
        self.x, self.yc = 0, 0

    def sec(self, x, y=0):
        """Start a node column: nodes created without an explicit x stack downward from (x, y)."""
        self.x, self.yc = x, y

    def n(self, t, x=None, y=0, label=None, **props):
        node = self.N.new(t)
        if x is None and y == 0:
            x, y = self.x, self.yc; self.yc -= 170
            if self.yc < -6000 + 0: self.x += 220; self.yc = 0
        node.location = (self.x if x is None else x, y)
        if label: node.label = label
        for k, v in props.items(): setattr(node, k, v)
        return node

    def put(self, sock, v):
        if v is None: return
        if isinstance(v, bpy.types.NodeSocket): self.L.new(v, sock)
        else: sock.default_value = v

    def math(self, op, a, b=None, c=None, x=None, y=0, label=None):
        n = self.n("ShaderNodeMath", x, y, label, operation=op)
        for i, v in enumerate((a, b, c)): self.put(n.inputs[i], v)
        return n.outputs[0]

    def vmath(self, op, a, b=None, scale=None, x=None, y=0, label=None, out=0):
        n = self.n("ShaderNodeVectorMath", x, y, label, operation=op)
        self.put(n.inputs[0], a); self.put(n.inputs[1], b)
        if scale is not None: self.put(n.inputs["Scale"], scale)
        return n.outputs[out]

    def switch(self, t, cond, f, tr, x=None, y=0, label=None):
        n = self.n("GeometryNodeSwitch", x, y, label, input_type=t)
        self.put(n.inputs["Switch"], cond); self.put(n.inputs["False"], f); self.put(n.inputs["True"], tr)
        return n.outputs[0]

    def cmp(self, dt, op, a, b, x=None, y=0, label=None):
        n = self.n("FunctionNodeCompare", x, y, label, data_type=dt, operation=op)
        self.put(n.inputs["A"], a); self.put(n.inputs["B"], b)
        return n.outputs[0]

    def boolm(self, op, a, b=None, x=None, y=0):
        n = self.n("FunctionNodeBooleanMath", x, y, operation=op)
        self.put(n.inputs[0], a)
        if b is not None: self.put(n.inputs[1], b)
        return n.outputs[0]

    def imath(self, op, a, b, x=None, y=0, label=None):
        n = self.n("FunctionNodeIntegerMath", x, y, label, operation=op)
        self.put(n.inputs[0], a); self.put(n.inputs[1], b)
        return n.outputs[0]

    def xyz(self, x_, y_, z_, x=None, y=0, label=None):
        n = self.n("ShaderNodeCombineXYZ", x, y, label)
        for s, v in zip("XYZ", (x_, y_, z_)): self.put(n.inputs[s], v)
        return n.outputs[0]

    def sep(self, v, x=None, y=0):
        n = self.n("ShaderNodeSeparateXYZ", x, y); self.put(n.inputs[0], v)
        return n.outputs

    def index_switch(self, t, idx, values, x=None, y=0, label=None):
        n = self.n("GeometryNodeIndexSwitch", x, y, label, data_type=t)
        while len(n.inputs) - 2 < len(values): n.index_switch_items.new()
        self.put(n.inputs["Index"], idx)
        for i, v in enumerate(values): self.put(n.inputs[i + 1], v)
        return n.outputs[0]

    def group(self, tree, x=None, y=0, **ins):
        n = self.n("GeometryNodeGroup", x, y); n.node_tree = tree
        for k, v in ins.items(): self.put(n.inputs[k.replace("_", " ")], v)
        return n.outputs

    def named(self, dt, name, x=None, y=0):
        n = self.n("GeometryNodeInputNamedAttribute", x, y, data_type=dt)
        n.inputs["Name"].default_value = name
        return n.outputs

    def store(self, geo, dt, name, value, x=None, y=0):
        n = self.n("GeometryNodeStoreNamedAttribute", x, y, data_type=dt, domain="POINT")
        self.put(n.inputs["Geometry"], geo); n.inputs["Name"].default_value = name; self.put(n.inputs["Value"], value)
        return n.outputs[0]

    # -- short vector helpers (auto-placed)
    def sidx(self, geo, dt, value, idx, domain="POINT", clamp=False):
        n = self.n("GeometryNodeSampleIndex", data_type=dt, domain=domain, clamp=clamp)
        self.put(n.inputs["Geometry"], geo); self.put(n.inputs["Value"], value); self.put(n.inputs["Index"], idx)
        return n.outputs[0]

    def dot(self, a, b): return self.vmath("DOT_PRODUCT", a, b, out="Value")
    def len(self, a): return self.vmath("LENGTH", a, out="Value")
    def nrm(self, a): return self.vmath("NORMALIZE", a)
    def add(self, a, b): return self.vmath("ADD", a, b)
    def sub(self, a, b): return self.vmath("SUBTRACT", a, b)
    def scl(self, a, s): return self.vmath("SCALE", a, scale=s)
    def crs(self, a, b): return self.vmath("CROSS_PRODUCT", a, b)
    def clampf(self, v, lo, hi): return self.math("MINIMUM", self.math("MAXIMUM", v, lo), hi)

    def vrot(self, v, axis, angle):
        aa = self.n("FunctionNodeAxisAngleToRotation"); self.put(aa.inputs["Axis"], axis); self.put(aa.inputs["Angle"], angle)
        rv = self.n("FunctionNodeRotateVector"); self.put(rv.inputs["Vector"], v); self.put(rv.inputs["Rotation"], aa.outputs[0])
        return rv.outputs[0]

    def single(self, geo1pt, field, dt="FLOAT", x=None, y=0, label=None):
        """Evaluate a field on a 1-point geometry and return it as a single value (Attribute Statistic mean)."""
        n = self.n("GeometryNodeAttributeStatistic", x, y, label, data_type=dt, domain="POINT")
        self.put(n.inputs["Geometry"], geo1pt); self.put(n.inputs["Attribute"], field)
        return n.outputs["Mean"]


def new_tree(name, ins, outs):
    old = bpy.data.node_groups.get(name)
    if old: bpy.data.node_groups.remove(old)
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    for nm, t in ins: ng.interface.new_socket(nm, in_out="INPUT", socket_type=t)
    for nm, t in outs: ng.interface.new_socket(nm, in_out="OUTPUT", socket_type=t)
    b = B(ng)
    gi = b.n("NodeGroupInput", -800, 0); go = b.n("NodeGroupOutput", 2400, 0)
    return ng, b, gi, go


# ------------------------------------------------------------------------------------------------ sub-groups
def build_sample_ext():
    """Sample an (all-curves) curve at a length; past either end of an open curve, continue straight."""
    ng, b, gi, go = new_tree(".CS Sample Ext",
                             [("Curve", "NodeSocketGeometry"), ("Length", "NodeSocketFloat")],
                             [("Position", "NodeSocketVector"), ("Tangent", "NodeSocketVector"), ("Normal", "NodeSocketVector")])
    curve, L = gi.outputs["Curve"], gi.outputs["Length"]
    total = b.n("GeometryNodeCurveLength", -600, 300); b.put(total.inputs[0], curve); total = total.outputs[0]
    cyc_in = b.n("GeometryNodeInputSplineCyclic", -600, 150)
    cyc = b.n("GeometryNodeSampleIndex", -400, 150, data_type="BOOLEAN", domain="CURVE")
    b.put(cyc.inputs["Geometry"], curve); b.put(cyc.inputs["Value"], cyc_in.outputs[0]); cyc = cyc.outputs[0]
    wrap = b.math("FLOORED_MODULO", L, total, x=-200, y=300)
    clamp = b.math("MINIMUM", b.math("MAXIMUM", L, 0.0, x=-400, y=0), total, x=-200, y=0)
    over = b.math("SUBTRACT", L, clamp, x=0, y=0)
    Ls = b.switch("FLOAT", cyc, clamp, wrap, x=0, y=300)
    over = b.switch("FLOAT", cyc, over, 0.0, x=200, y=0)
    s = b.n("GeometryNodeSampleCurve", 200, 300, mode="LENGTH", use_all_curves=True)
    b.put(s.inputs["Curves"], curve); b.put(s.inputs["Length"], Ls)
    T = b.vmath("NORMALIZE", s.outputs["Tangent"], x=400, y=200)
    P = b.vmath("ADD", s.outputs["Position"], b.vmath("SCALE", T, scale=over, x=600, y=100), x=800, y=300)
    b.put(go.inputs["Position"], P); b.put(go.inputs["Tangent"], T); b.put(go.inputs["Normal"], s.outputs["Normal"])
    return ng


def build_chord_step(sample):
    """Find the curve length where the straight-line distance from Start reaches Distance (signed)."""
    ng, b, gi, go = new_tree(".CS Chord Step",
                             [("Curve", "NodeSocketGeometry"), ("Start", "NodeSocketFloat"), ("Distance", "NodeSocketFloat")],
                             [("End", "NodeSocketFloat")])
    curve, s0, d = gi.outputs["Curve"], gi.outputs["Start"], gi.outputs["Distance"]
    P0 = b.group(sample, -500, 300, Curve=curve, Length=s0)["Position"]
    sgn = b.math("SIGN", d, x=-500, y=0)
    s = b.math("ADD", s0, d, x=-300, y=100)
    for k in range(NEWTON_STEPS):
        Pk = b.group(sample, -100 + 400 * k, 300, Curve=curve, Length=s)["Position"]
        c = b.vmath("DISTANCE", Pk, P0, x=100 + 400 * k, y=200, out="Value")
        s = b.math("ADD", s, b.math("SUBTRACT", d, b.math("MULTIPLY", c, sgn, x=100 + 400 * k, y=0), x=200 + 400 * k, y=0),
                   x=300 + 400 * k, y=100)
    b.put(go.inputs["End"], s)
    return ng


def build_pivots():
    """Start/End points of a piece in its own local space, for the chosen Measure Gap mode."""
    ng, b, gi, go = new_tree(".CS Pivots",
                             [("Geometry", "NodeSocketGeometry"), ("Mode", "NodeSocketInt")],
                             [("Start", "NodeSocketVector"), ("End", "NodeSocketVector"), ("Has Pivots", "NodeSocketBool")])
    geo, mode = gi.outputs["Geometry"], gi.outputs["Mode"]
    pos = b.n("GeometryNodeInputPosition", -600, -300).outputs[0]
    res = {}
    for k, nm in enumerate(("pivot_start", "pivot_end")):
        w = b.named("FLOAT", nm, x=-600, y=200 - 300 * k)
        sel = b.cmp("FLOAT", "GREATER_THAN", w["Attribute"], 0.5, x=-400, y=200 - 300 * k)
        st = b.n("GeometryNodeAttributeStatistic", -200, 200 - 300 * k, data_type="FLOAT_VECTOR", domain="POINT")
        b.put(st.inputs["Geometry"], geo); b.put(st.inputs["Selection"], sel); b.put(st.inputs["Attribute"], pos)
        # "has pivot" as a SINGLE value (Named Attribute's Exists is a field): count of vertices in the group > 0
        cnt = b.n("GeometryNodeAttributeStatistic", 0, 100 - 300 * k, data_type="FLOAT", domain="POINT")
        b.put(cnt.inputs["Geometry"], geo); b.put(cnt.inputs["Attribute"], sel)
        res[nm] = (st.outputs["Mean"], b.cmp("FLOAT", "GREATER_THAN", cnt.outputs["Sum"], 0.5, x=200, y=100 - 300 * k))
    bb = b.n("GeometryNodeBoundBox", -600, -600); b.put(bb.inputs["Geometry"], geo)
    mn, mx = b.sep(bb.outputs["Min"], x=-400, y=-600), b.sep(bb.outputs["Max"], x=-400, y=-750)
    edge_s, edge_e = b.xyz(mn["X"], 0.0, 0.0, x=-200, y=-600), b.xyz(mx["X"], 0.0, 0.0, x=-200, y=-750)
    zero = (0.0, 0.0, 0.0)
    b.put(go.inputs["Start"], b.index_switch("VECTOR", mode, [res["pivot_start"][0], edge_s, zero], x=200, y=200))
    b.put(go.inputs["End"], b.index_switch("VECTOR", mode, [res["pivot_end"][0], edge_e, zero], x=200, y=-100))
    b.put(go.inputs["Has Pivots"], b.boolm("AND", res["pivot_start"][1], res["pivot_end"][1], x=200, y=-400))
    return ng


# ------------------------------------------------------------------------------------------------ main group
def build_main():
    sample = build_sample_ext()
    chord, piv = build_chord_step(sample), build_pivots()
    old = bpy.data.node_groups.get(GROUP)
    if old: bpy.data.node_groups.remove(old)
    ng = bpy.data.node_groups.new(GROUP, "GeometryNodeTree")
    ng.is_modifier = True
    I = ng.interface

    def sock(name, t, desc, default=None, sub=None, mn=None, mx=None, parent=None, toggle=False):
        s = I.new_socket(name, in_out="INPUT", socket_type=t, parent=parent)
        s.description = desc
        if sub: s.subtype = sub
        if default is not None and t != "NodeSocketMenu": s.default_value = default
        if mn is not None: s.min_value = mn
        if mx is not None: s.max_value = mx
        if t not in ("NodeSocketGeometry", "NodeSocketObject", "NodeSocketMenu"): s.force_non_field = True
        if toggle: s.is_panel_toggle = True
        return s

    I.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    sock("Geometry", "NodeSocketGeometry", "The link that is repeated along the curve")
    sock("Curve Object", "NodeSocketObject", "Curve the chain follows")
    sock("Count", "NodeSocketInt", "Number of links (caps not included)", 10, mn=1, mx=100000)
    sock("Measure Gap", "NodeSocketMenu", "How pieces are joined")
    sock("Gap", "NodeSocketFloat", "Extra space between joints (negative = overlap)", 0.0, "DISTANCE", -10000.0, 10000.0)
    sock("Start Offset", "NodeSocketFloat", "Where along the curve the chain starts (start cap included)", 0.0, "DISTANCE", -10000.0, 10000.0)
    for ax in "XYZ":
        sock(f"Lock {ax}", "NodeSocketBool",
             f"Flatten the curve on {ax}: pieces ignore curve points moved along {ax} and stay level", False)
    sock("Show Pivots", "NodeSocketBool", "Show joint markers in the viewport (never rendered)", False)
    sock("Realize Instances", "NodeSocketBool", "Output a single mesh instead of instances", True)

    p_copy = I.new_panel("Copy Transform", description="Per-link offset, rotation and scale", default_closed=True)
    sock("Accumulate", "NodeSocketBool", "On: link n gets (n-1)x the values (scale compounds). Off: every link the same", True, parent=p_copy)
    sock("Copy Offset", "NodeSocketVector", "Offset in the chain frame (X along the chain)", (0.0, 0.0, 0.0), "TRANSLATION", parent=p_copy)
    sock("Copy Rotation", "NodeSocketVector", "Rotation around the joint; X rolls the link around its pivot axis (joints stay connected)",
         (0.0, 0.0, 0.0), "EULER", parent=p_copy)
    sock("Copy Scale", "NodeSocketVector", "Scale of each link; joints stay connected", (1.0, 1.0, 1.0), "XYZ", parent=p_copy)

    for side in ("Start", "End"):
        p = I.new_panel(f"{side} Cap", description=f"Different {side.lower()} link (Count does not include it)", default_closed=True)
        sock(f"{side} Cap", "NodeSocketBool", f"Use a {side.lower()} cap", False, parent=p, toggle=True)
        sock(f"{side} Cap Object", "NodeSocketObject", f"{side} link object (with its own Start/End pivots)", parent=p)
        sock(f"{side} Cap Offset", "NodeSocketVector", "Offset of the cap", (0.0, 0.0, 0.0), "TRANSLATION", parent=p)
        sock(f"{side} Cap Rotation", "NodeSocketVector", "Rotation of the cap around its joint", (0.0, 0.0, 0.0), "EULER", parent=p)
        sock(f"{side} Cap Scale", "NodeSocketVector", "Scale of the cap", (1.0, 1.0, 1.0), "XYZ", parent=p)

    p_cl = I.new_panel("Clasp", description="Fold-over clasp closing the bracelet loop between the end cap and the start", default_closed=True)
    sock("Clasp", "NodeSocketBool", "Close the bracelet with a fold-over clasp", False, parent=p_cl, toggle=True)
    sock("Clasp Body", "NodeSocketObject", "Logo plate / lock body. Pivots: the pin the inner blade hinges on and the pin of the start link",
         parent=p_cl)
    sock("Clasp Inner Blade", "NodeSocketObject", "Blade hinged to the body (top plate). Pivots: its two hinge pins", parent=p_cl)
    sock("Clasp Outer Blade", "NodeSocketObject", "Blade attached to the end cap (bottom plate). Pivots: the end cap pin and the blade hinge",
         parent=p_cl)
    sock("Clasp Open Limit", "NodeSocketFloat", "How far the blades may unfold: 0 = stays closed, 1 = blades fully straight",
         1.0, "FACTOR", 0.0, 1.0, parent=p_cl)

    p_rig = I.new_panel("Joint Limits", description="The chain is a rigid rig: fixed link lengths, limited joint bending",
                        default_closed=True)
    sock("Bend In", "NodeSocketFloat", "Most a joint can bend toward the inside of the bracelet", 0.6109, "ANGLE", 0.0, 3.1416, parent=p_rig)
    sock("Bend Out", "NodeSocketFloat", "Most a joint can bend toward the outside", 0.1396, "ANGLE", 0.0, 3.1416, parent=p_rig)
    sock("Solver Iterations", "NodeSocketInt", "More = follows the curve and the limits more closely (0 = plain layout along the curve)",
         60, mn=0, mx=2000, parent=p_rig)

    b = B(ng)
    gi = b.n("NodeGroupInput", -2400, 0)
    go = b.n("NodeGroupOutput", 6000, 0)
    G = lambda name: next(s for s in gi.outputs if s.name == name)

    # ---- Measure Gap menu -> int mode
    ms = b.n("GeometryNodeMenuSwitch", -2100, 600, "Measure Gap", data_type="INT")
    ms.enum_items.clear()
    for k, nm in enumerate(MODES):
        ms.enum_items.new(nm); ms.inputs[nm].default_value = k
    b.put(ms.inputs["Menu"], G("Measure Gap"))
    I.items_tree["Measure Gap"].default_value = MODES[0]
    mode = ms.outputs["Output"]

    # ---- curve (relative to this object), flattened on locked axes around the curve origin
    oi = b.n("GeometryNodeObjectInfo", -2100, 300, transform_space="RELATIVE"); b.put(oi.inputs["Object"], G("Curve Object"))
    keep = b.vmath("SUBTRACT", (1.0, 1.0, 1.0), b.xyz(G("Lock X"), G("Lock Y"), G("Lock Z"), x=-2100, y=0), x=-1900, y=0)
    t1 = b.n("GeometryNodeTransform", -1700, 300); b.put(t1.inputs["Geometry"], oi.outputs["Geometry"])
    b.put(t1.inputs["Translation"], b.vmath("SCALE", oi.outputs["Location"], scale=-1.0, x=-1900, y=150))
    t2 = b.n("GeometryNodeTransform", -1500, 300); b.put(t2.inputs["Geometry"], t1.outputs[0])
    b.put(t2.inputs["Scale"], keep); b.put(t2.inputs["Translation"], oi.outputs["Location"])
    curve = t2.outputs[0]
    nspl = b.n("GeometryNodeAttributeDomainSize", -1300, 500, component="CURVE"); b.put(nspl.inputs[0], curve)
    no_curve = b.cmp("INT", "EQUAL", nspl.outputs["Spline Count"], 0, x=-1100, y=500)

    # ---- pieces: 0 link, 1 start cap, 2 end cap
    caps = {}
    for side, y in (("Start", -900), ("End", -1200)):
        o = b.n("GeometryNodeObjectInfo", -2100, y, transform_space="ORIGINAL"); b.put(o.inputs["Object"], G(f"{side} Cap Object"))
        caps[side] = o.outputs["Geometry"]
    sources = [G("Geometry"), caps["Start"], caps["End"]]
    pv = [b.group(piv, -1700, -600 - 300 * k, Geometry=g, Mode=mode) for k, g in enumerate(sources)]

    hs = b.switch("INT", G("Start Cap"), 0, 1, x=-1900, y=-1500, label="Has Start Cap")
    he = b.switch("INT", G("End Cap"), 0, 1, x=-1900, y=-1650, label="Has End Cap")
    total_n = b.imath("ADD", b.imath("ADD", G("Count"), hs, x=-1700, y=-1500), he, x=-1500, y=-1500, label="Pieces")
    last = b.imath("SUBTRACT", total_n, 1, x=-1300, y=-1650)

    # ---- walk the chain: one piece per iteration, joints found by straight-line distance
    ri = b.n("GeometryNodeRepeatInput", -1100, -300); ro = b.n("GeometryNodeRepeatOutput", 1900, -300)
    ri.pair_with_output(ro)
    ro.repeat_items.new("FLOAT", "S")
    b.put(ri.inputs["Iterations"], total_n)
    b.put(ri.inputs["Item_1"], G("Start Offset"))
    it = ri.outputs["Iteration"]
    is_sc = b.boolm("AND", G("Start Cap"), b.cmp("INT", "EQUAL", it, 0, x=-900, y=-700), x=-700, y=-700)
    is_ec = b.boolm("AND", G("End Cap"), b.cmp("INT", "EQUAL", it, last, x=-900, y=-850), x=-700, y=-850)
    ptype = b.switch("INT", is_sc, b.switch("INT", is_ec, 0, 2, x=-500, y=-850), 1, x=-300, y=-700, label="Piece Type")
    kidx = b.imath("SUBTRACT", it, hs, x=-700, y=-1000, label="Link Index")
    f = b.switch("FLOAT", G("Accumulate"), 1.0, kidx, x=-500, y=-1000, label="Copy Factor")
    cs = b.sep(G("Copy Scale"), x=-500, y=-1150)
    link_scale = b.xyz(*[b.math("POWER", cs[a], f, x=-300, y=-1150 - 120 * i) for i, a in enumerate("XYZ")], x=-100, y=-1150)
    scale = b.index_switch("VECTOR", ptype, [link_scale, G("Start Cap Scale"), G("End Cap Scale")], x=100, y=-1000, label="Piece Scale")
    S = b.index_switch("VECTOR", ptype, [pv[0]["Start"], pv[1]["Start"], pv[2]["Start"]], x=100, y=-700, label="Start Pivot")
    E = b.index_switch("VECTOR", ptype, [pv[0]["End"], pv[1]["End"], pv[2]["End"]], x=100, y=-850, label="End Pivot")
    pitch = b.vmath("LENGTH", b.vmath("MULTIPLY", b.vmath("SUBTRACT", E, S, x=300, y=-800), scale, x=500, y=-850),
                    x=700, y=-850, out="Value", label="Pitch")
    one = b.n("GeometryNodePoints", -900, -300); one.inputs["Count"].default_value = 1
    s_start = ri.outputs["Item_1"]
    s_end_f = b.group(chord, 900, -500, Curve=curve, Start=s_start, Distance=pitch)["End"]
    s_end = b.single(one.outputs[0], s_end_f, x=1100, y=-500, label="End Joint")
    s_next_f = b.group(chord, 1100, -700, Curve=curve, Start=s_end, Distance=G("Gap"))["End"]
    s_next = b.single(one.outputs[0], s_next_f, x=1300, y=-700, label="Next Start")
    pt = one.outputs[0]
    for nm, dt, v in (("_cs_a", "FLOAT", s_start), ("_cs_b", "FLOAT", s_end), ("_cs_t", "INT", ptype),
                      ("_cs_f", "FLOAT", f), ("_cs_scale", "FLOAT_VECTOR", scale), ("_cs_S", "FLOAT_VECTOR", S),
                      ("_cs_E", "FLOAT_VECTOR", E), ("_cs_pitch", "FLOAT", pitch)):
        pt = b.store(pt, dt, nm, v, x=1300, y=-300)
    jn = b.n("GeometryNodeJoinGeometry", 1600, -300)
    # Multi-input sockets list the most recently linked socket FIRST: link the new point first so the
    # accumulated points stay in chain order.
    b.put(jn.inputs[0], pt); b.put(jn.inputs[0], ri.outputs["Item_0"])
    b.put(ro.inputs["Item_0"], jn.outputs[0]); b.put(ro.inputs["Item_1"], s_next)
    pts = ro.outputs["Item_0"]

    pos_f = b.n("GeometryNodeInputPosition", -900, -1300).outputs[0]
    idx_f = b.n("GeometryNodeInputIndex", -900, -1450).outputs[0]
    Lpos = lambda node_out: node_out  # readability

    # ---- curve frame data: dense copy with unit tangent / normal, the overall turning axis, a proximity mesh
    b.sec(2200, 3000)
    rsm = b.n("GeometryNodeResampleCurve", label="Dense Curve"); b.put(rsm.inputs["Curve"], curve)
    rsm.inputs["Mode"].default_value = "Count"; rsm.inputs["Count"].default_value = 1000
    res = b.store(rsm.outputs[0], "FLOAT_VECTOR", "_cs_T", b.nrm(b.n("GeometryNodeInputTangent").outputs[0]))
    res = b.store(res, "FLOAT_VECTOR", "_cs_N", b.n("GeometryNodeInputNormal").outputs[0])
    T_f = b.named("FLOAT_VECTOR", "_cs_T")["Attribute"]
    T_next = b.sidx(res, "FLOAT_VECTOR", T_f, b.imath("ADD", idx_f, 1), clamp=True)
    turn = b.n("GeometryNodeAttributeStatistic", data_type="FLOAT_VECTOR", domain="POINT")
    b.put(turn.inputs["Geometry"], res); b.put(turn.inputs["Attribute"], b.crs(T_f, T_next))
    turn_sum = turn.outputs["Sum"]
    n_glob = b.nrm(turn_sum)
    turn_ok = b.cmp("FLOAT", "GREATER_THAN", b.len(turn_sum), 1e-3)

    c2m = b.n("GeometryNodeCurveToMesh"); b.put(c2m.inputs["Curve"], res)
    res_mesh = c2m.outputs[0]  # same points (and attributes) as the dense curve; nearest-point lookups need a mesh

    def frame_at(p):
        """Curve frame nearest to p: (tangent, normal, hinge axis). The hinge axis is perpendicular to the tangent,
        signed so the curve's overall turn is positive around it (+ bend = toward the inside)."""
        ni = b.n("GeometryNodeSampleNearest", domain="POINT"); b.put(ni.inputs["Geometry"], res_mesh); b.put(ni.inputs["Sample Position"], p)
        Tq = b.sidx(res_mesh, "FLOAT_VECTOR", T_f, ni.outputs[0])
        Nq = b.sidx(res_mesh, "FLOAT_VECTOR", b.named("FLOAT_VECTOR", "_cs_N")["Attribute"], ni.outputs[0])
        n0 = b.sub(n_glob, b.scl(Tq, b.dot(n_glob, Tq)))
        good = b.boolm("AND", turn_ok, b.cmp("FLOAT", "GREATER_THAN", b.len(n0), 1e-3))
        ax = b.switch("VECTOR", good, b.nrm(b.crs(Tq, Nq)), b.nrm(n0))
        return Tq, Nq, ax

    # straight extensions past the ends of an open curve (cyclic curves: none)
    cyc_c = b.sidx(curve, "BOOLEAN", b.n("GeometryNodeInputSplineCyclic").outputs[0], 0, domain="CURVE")
    clen = b.n("GeometryNodeCurveLength"); b.put(clen.inputs[0], curve)
    pstat = b.n("GeometryNodeAttributeStatistic", data_type="FLOAT", domain="POINT")
    b.put(pstat.inputs["Geometry"], pts); b.put(pstat.inputs["Attribute"], b.named("FLOAT", "_cs_pitch")["Attribute"])
    chain_len = b.math("ADD", pstat.outputs["Sum"], b.math("MULTIPLY", b.math("ABSOLUTE", G("Gap")), total_n))
    Lext = b.math("MULTIPLY", b.math("ADD", b.math("ADD", clen.outputs[0], chain_len), 0.001), 2.0)
    ext_j = b.n("GeometryNodeJoinGeometry")
    for fac, sg in ((0.0, -1.0), (1.0, 1.0)):
        sc_ = b.n("GeometryNodeSampleCurve", mode="FACTOR", use_all_curves=True)
        b.put(sc_.inputs["Curves"], curve); sc_.inputs["Factor"].default_value = fac
        ml = b.n("GeometryNodeMeshLine", mode="END_POINTS"); ml.inputs["Count"].default_value = 2
        b.put(ml.inputs["Start Location"], sc_.outputs["Position"])
        b.put(ml.inputs["Offset"], b.add(sc_.outputs["Position"], b.scl(b.nrm(sc_.outputs["Tangent"]), b.math("MULTIPLY", Lext, sg))))
        b.put(ext_j.inputs[0], ml.outputs[0])
    prox_mesh = b.n("GeometryNodeJoinGeometry", label="Curve + Extensions")
    b.put(prox_mesh.inputs[0], b.switch("GEOMETRY", cyc_c, ext_j.outputs[0], None)); b.put(prox_mesh.inputs[0], res_mesh)
    prox_mesh = prox_mesh.outputs[0]

    # ---- joints: J0 = start of the first piece ... J(M-1) = end of the last piece (M = pieces + 1).
    # Segment j -> j+1 is rigid (piece pitch + gap); the closing segment J(M-1) -> J0 is the clasp.
    b.sec(2200, -2600)
    M = b.imath("ADD", total_n, 1, label="Joints")
    clasp_on = G("Clasp")
    pc = b.imath("MINIMUM", idx_f, last)
    s_j = b.switch("FLOAT", b.cmp("INT", "LESS_THAN", idx_f, total_n),
                   b.sidx(pts, "FLOAT", b.named("FLOAT", "_cs_b")["Attribute"], pc),
                   b.sidx(pts, "FLOAT", b.named("FLOAT", "_cs_a")["Attribute"], pc))
    jp = b.n("GeometryNodePoints", label="Joint Points"); b.put(jp.inputs["Count"], M)
    b.put(jp.inputs["Position"], b.group(sample, Curve=curve, Length=s_j)["Position"])
    pitch_j = b.sidx(pts, "FLOAT", b.named("FLOAT", "_cs_pitch")["Attribute"], pc)
    L_j = b.switch("FLOAT", b.cmp("INT", "LESS_THAN", idx_f, last), pitch_j, b.math("ADD", pitch_j, G("Gap")))
    jg = b.store(jp.outputs[0], "FLOAT", "_cs_L", L_j)
    is_cl = b.cmp("INT", "EQUAL", idx_f, total_n)
    jg = b.store(jg, "BOOLEAN", "_cs_cl", is_cl)
    jg = b.store(jg, "BOOLEAN", "_cs_ok", b.boolm("OR", b.boolm("NOT", is_cl), clasp_on))
    jg = b.store(jg, "FLOAT_VECTOR", "_cs_n", frame_at(pos_f)[2])

    # ---- clasp rest pose (pins found from each part's two pivots, in any order)
    b.sec(2200, -5200)
    cparts = {}
    for nm in ("Clasp Body", "Clasp Inner Blade", "Clasp Outer Blade"):
        o = b.n("GeometryNodeObjectInfo", transform_space="RELATIVE"); b.put(o.inputs["Object"], G(nm))
        cparts[nm] = (o.outputs["Geometry"], b.group(piv, Geometry=o.outputs["Geometry"], Mode=0))
    O1, O2 = cparts["Clasp Outer Blade"][1]["Start"], cparts["Clasp Outer Blade"][1]["End"]
    I1, I2 = cparts["Clasp Inner Blade"][1]["Start"], cparts["Clasp Inner Blade"][1]["End"]
    Y1, Y2 = cparts["Clasp Body"][1]["Start"], cparts["Clasp Body"][1]["End"]
    dist = lambda p, q: b.vmath("DISTANCE", p, q, out="Value")
    o1_shared = b.cmp("FLOAT", "LESS_THAN", b.math("MINIMUM", dist(O1, I1), dist(O1, I2)), b.math("MINIMUM", dist(O2, I1), dist(O2, I2)))
    H2o = b.switch("VECTOR", o1_shared, O2, O1); A_r = b.switch("VECTOR", o1_shared, O1, O2)
    i1_shared = b.cmp("FLOAT", "LESS_THAN", dist(I1, H2o), dist(I2, H2o))
    H2i = b.switch("VECTOR", i1_shared, I2, I1); H1i = b.switch("VECTOR", i1_shared, I1, I2)
    y1_h1 = b.cmp("FLOAT", "LESS_THAN", dist(Y1, H1i), dist(Y2, H1i))
    H1b = b.switch("VECTOR", y1_h1, Y2, Y1); B_r = b.switch("VECTOR", y1_h1, Y1, Y2)
    H2_r = b.scl(b.add(H2o, H2i), 0.5); H1_r = b.scl(b.add(H1i, H1b), 0.5)
    tol = b.math("MULTIPLY", b.math("MINIMUM", b.math("MINIMUM", dist(O1, O2), dist(I1, I2)), dist(Y1, Y2)), 0.15)
    clasp_apart = b.boolm("OR", b.cmp("FLOAT", "GREATER_THAN", dist(H2o, H2i), tol), b.cmp("FLOAT", "GREATER_THAN", dist(H1i, H1b), tol))
    u_r = b.nrm(b.sub(B_r, A_r))
    sd = b.sub(H2_r, A_r)
    i_r = b.nrm(b.sub(sd, b.scl(u_r, b.dot(sd, u_r))))
    n_r = b.crs(u_r, i_r)
    f2 = lambda p: (b.dot(b.sub(p, A_r), u_r), b.dot(b.sub(p, A_r), i_r))
    h1x, h1y = f2(H1_r); h2x, h2y = f2(H2_r); bx, by = f2(B_r)
    L1, L2 = dist(A_r, H2_r), dist(H2_r, H1_r)
    lx, ly = b.math("SUBTRACT", h1x, bx), b.math("SUBTRACT", h1y, by)
    d0 = dist(A_r, B_r)
    r0 = dist(A_r, H1_r)
    L12 = b.math("ADD", L1, L2)
    reach = b.math("MINIMUM", b.math("ADD", r0, b.math("MULTIPLY", b.clampf(G("Clasp Open Limit"), 0.0, 1.0), b.math("SUBTRACT", L12, r0))), L12)
    dmax = b.math("ADD", b.math("MULTIPLY", lx, -1.0),
                  b.math("SQRT", b.math("MAXIMUM", b.math("SUBTRACT", b.math("MULTIPLY", reach, reach), b.math("MULTIPLY", ly, ly)), 0.0)))
    dmax = b.math("MAXIMUM", dmax, d0, label="Clasp Max Span")
    # rest side of the blade hinge relative to the line A -> H1
    r_h1 = b.math("MAXIMUM", b.math("SQRT", b.math("ADD", b.math("MULTIPLY", h1x, h1x), b.math("MULTIPLY", h1y, h1y))), 1e-9)
    side = b.math("SIGN", b.math("SUBTRACT", b.math("MULTIPLY", h2y, b.math("DIVIDE", h1x, r_h1)),
                                 b.math("MULTIPLY", h2x, b.math("DIVIDE", h1y, r_h1))))
    side = b.switch("FLOAT", b.cmp("FLOAT", "EQUAL", side, 0.0), side, 1.0)

    # ---- solver: pull toward the curve, keep lengths rigid, keep bends within limits (Jacobi, momentum-conserving)
    b.sec(5200, 3000)
    ri2 = b.n("GeometryNodeRepeatInput", label="Rig Solver"); ro2 = b.n("GeometryNodeRepeatOutput"); ri2.pair_with_output(ro2)
    b.put(ri2.inputs["Iterations"], G("Solver Iterations")); b.put(ri2.inputs["Item_0"], jg)
    K = b.math("MAXIMUM", G("Solver Iterations"), 1.0)
    k = ri2.outputs["Iteration"]
    alpha = b.switch("FLOAT", b.cmp("FLOAT", "GREATER_EQUAL", k, b.math("MULTIPLY", K, 0.9)),
                     b.math("MAXIMUM", 0.05, b.math("MULTIPLY", 0.5, b.math("SUBTRACT", 1.0, b.math("DIVIDE", k, b.math("MULTIPLY", K, 0.7))))),
                     0.0)
    nxt = b.imath("FLOORED_MODULO", b.imath("ADD", idx_f, 1), M)
    prv = b.imath("FLOORED_MODULO", b.imath("SUBTRACT", idx_f, 1), M)
    geo = ri2.outputs["Item_0"]
    prox = b.n("GeometryNodeProximity", target_element="EDGES"); b.put(prox.inputs["Target"], prox_mesh)
    sp_ = b.n("GeometryNodeSetPosition"); b.put(sp_.inputs["Geometry"], geo)
    b.put(sp_.inputs["Offset"], b.scl(b.sub(prox.outputs["Position"], pos_f), alpha))
    geo = sp_.outputs[0]
    interior = b.boolm("AND", b.cmp("INT", "GREATER_THAN", idx_f, 0), b.cmp("INT", "LESS_THAN", idx_f, total_n))
    n_f = b.named("FLOAT_VECTOR", "_cs_n")["Attribute"]
    bi, bo = G("Bend In"), G("Bend Out")
    for _ in range(2):
        b.sec(b.x + 900, 3000)
        # rigid lengths; the clasp segment may stretch between closed and fully open
        Pn = b.sidx(geo, "FLOAT_VECTOR", pos_f, nxt)
        dv = b.sub(Pn, pos_f); dl = b.len(dv)
        tgt = b.switch("FLOAT", b.named("BOOLEAN", "_cs_cl")["Attribute"], b.named("FLOAT", "_cs_L")["Attribute"], b.clampf(dl, d0, dmax))
        c = b.switch("VECTOR", b.named("BOOLEAN", "_cs_ok")["Attribute"], (0.0, 0.0, 0.0),
                     b.scl(b.nrm(dv), b.math("MULTIPLY", 0.5, b.math("SUBTRACT", dl, tgt))))
        g1 = b.store(geo, "FLOAT_VECTOR", "_cs_c", c)
        c_f = b.named("FLOAT_VECTOR", "_cs_c")["Attribute"]
        sp_ = b.n("GeometryNodeSetPosition"); b.put(sp_.inputs["Geometry"], g1)
        b.put(sp_.inputs["Offset"], b.sub(c_f, b.sidx(g1, "FLOAT_VECTOR", c_f, prv)))
        geo = sp_.outputs[0]
        # bend limits around the hinge axis
        b.sec(b.x + 900, 3000)
        Pp, Pn = b.sidx(geo, "FLOAT_VECTOR", pos_f, prv), b.sidx(geo, "FLOAT_VECTOR", pos_f, nxt)
        e1, e2 = b.sub(pos_f, Pp), b.sub(Pn, pos_f)
        th = b.math("ARCTAN2", b.dot(b.crs(e1, e2), n_f), b.dot(e1, e2))
        ex = b.switch("FLOAT", b.cmp("FLOAT", "GREATER_THAN", th, bi),
                      b.switch("FLOAT", b.cmp("FLOAT", "LESS_THAN", th, b.math("MULTIPLY", bo, -1.0)), 0.0, b.math("ADD", th, bo)),
                      b.math("SUBTRACT", th, bi))
        ex = b.switch("FLOAT", interior, 0.0, ex)
        half = b.math("MULTIPLY", ex, 0.5)
        dn = b.sub(b.add(pos_f, b.vrot(e2, n_f, b.math("MULTIPLY", half, -1.0))), Pn)
        dp = b.sub(b.sub(pos_f, b.vrot(e1, n_f, half)), Pp)
        g1 = b.store(b.store(geo, "FLOAT_VECTOR", "_cs_dn", dn), "FLOAT_VECTOR", "_cs_dp", dp)
        dn_f, dp_f = b.named("FLOAT_VECTOR", "_cs_dn")["Attribute"], b.named("FLOAT_VECTOR", "_cs_dp")["Attribute"]
        off = b.add(b.scl(b.add(b.sidx(g1, "FLOAT_VECTOR", dn_f, prv), b.sidx(g1, "FLOAT_VECTOR", dp_f, nxt)), 1.0 / 3.0),
                    b.scl(b.add(dn_f, dp_f), -1.0 / 6.0))
        sp_ = b.n("GeometryNodeSetPosition"); b.put(sp_.inputs["Geometry"], g1); b.put(sp_.inputs["Offset"], off)
        geo = sp_.outputs[0]
    b.put(ro2.inputs["Item_0"], geo)

    # ---- exact pass. In the bracelet plane each segment has a heading; joint bends (heading changes) are clamped to
    # the limits and summed back (prefix sums), segments get their exact length. One extra "curl" angle phi, added to
    # every bend, is solved (Newton) so the clasp span lands within [closed, fully open]: the loop always closes.
    b.sec(b.x + 900, 3000)
    sol = ro2.outputs["Item_0"]
    n_e = b.switch("VECTOR", turn_ok, b.sidx(sol, "FLOAT_VECTOR", n_f, 0), n_glob)
    helper = b.switch("VECTOR", b.cmp("FLOAT", "LESS_THAN", b.math("ABSOLUTE", b.sep(n_e)["X"]), 0.9), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0))
    e1_ = b.nrm(b.crs(n_e, helper)); e2_ = b.crs(n_e, e1_)
    dirn = b.nrm(b.sub(b.sidx(sol, "FLOAT_VECTOR", pos_f, nxt), pos_f))
    el = b.math("ARCSINE", b.clampf(b.dot(dirn, n_e), -1.0, 1.0))
    psi = b.math("ARCTAN2", b.dot(dirn, e2_), b.dot(dirn, e1_))
    g_ = b.store(b.store(sol, "FLOAT", "_cs_psi", psi), "FLOAT", "_cs_el", el)
    psi_f = b.named("FLOAT", "_cs_psi")["Attribute"]
    dpsi = b.math("SUBTRACT", psi_f, b.sidx(g_, "FLOAT", psi_f, b.imath("MAXIMUM", b.imath("SUBTRACT", idx_f, 1), 0)))
    beta = b.math("ARCTAN2", b.math("SINE", dpsi), b.math("COSINE", dpsi))
    seg_in = b.boolm("AND", b.cmp("INT", "GREATER_THAN", idx_f, 0), b.cmp("INT", "LESS_THAN", idx_f, total_n))
    g_ = b.store(g_, "FLOAT", "_cs_beta", b.switch("FLOAT", seg_in, 0.0, beta))
    beta_f, el_f = b.named("FLOAT", "_cs_beta")["Attribute"], b.named("FLOAT", "_cs_el")["Attribute"]
    psi0 = b.sidx(g_, "FLOAT", psi_f, 0)
    P0 = b.sidx(g_, "FLOAT_VECTOR", pos_f, 0)
    is_seg = b.cmp("INT", "LESS_THAN", idx_f, total_n)

    def fk(phi):
        """Joint offsets from J0 (Trailing) and J(M-1) - J0 (Total) for curl phi; bends clamped to the limits."""
        bc = b.switch("FLOAT", seg_in, 0.0, b.clampf(b.math("ADD", beta_f, phi), b.math("MULTIPLY", bo, -1.0), bi))
        acc1 = b.n("GeometryNodeAccumulateField", data_type="FLOAT", domain="POINT"); b.put(acc1.inputs["Value"], bc)
        ps = b.math("ADD", psi0, acc1.outputs["Leading"])
        ce = b.math("COSINE", el_f)
        d_ = b.add(b.scl(b.add(b.scl(e1_, b.math("COSINE", ps)), b.scl(e2_, b.math("SINE", ps))), ce), b.scl(n_e, b.math("SINE", el_f)))
        v_ = b.switch("VECTOR", is_seg, (0.0, 0.0, 0.0), b.scl(d_, b.named("FLOAT", "_cs_L")["Attribute"]))
        acc = b.n("GeometryNodeAccumulateField", data_type="FLOAT_VECTOR", domain="POINT"); b.put(acc.inputs["Value"], v_)
        return acc.outputs["Trailing"], acc.outputs["Total"]

    span = lambda phi: b.single(g_, b.len(fk(phi)[1]))
    span0 = span(0.0)
    target = b.clampf(span0, d0, dmax)
    rn = b.n("GeometryNodeRepeatInput", label="Close Loop"); rno = b.n("GeometryNodeRepeatOutput"); rn.pair_with_output(rno)
    rno.repeat_items.clear(); rno.repeat_items.new("FLOAT", "Phi")
    b.put(rn.inputs["Iterations"], b.switch("INT", clasp_on, 0, 10))
    ph = rn.outputs["Phi"]
    f0 = b.math("SUBTRACT", span(ph), target)
    fd = b.math("DIVIDE", b.math("SUBTRACT", span(b.math("ADD", ph, 1e-4)), span(ph)), 1e-4)
    step = b.switch("FLOAT", b.cmp("FLOAT", "LESS_THAN", b.math("ABSOLUTE", fd), 1e-9), b.math("DIVIDE", f0, fd), 0.0)
    ph_new = b.clampf(b.math("SUBTRACT", ph, b.clampf(step, -0.05, 0.05)), -0.5, 0.5)
    b.put(rno.inputs[0], ph_new)
    phi_final = rno.outputs[0]
    spx = b.n("GeometryNodeSetPosition", label="Exact Joints"); b.put(spx.inputs["Geometry"], g_)
    b.put(spx.inputs["Position"], b.add(P0, fk(phi_final)[0]))
    joints = spx.outputs[0]

    # ---- clasp: span A -> B from the solve, blades folded analytically (circle-circle intersection)
    b.sec(b.x + 900, 3000)
    A_w = b.sidx(joints, "FLOAT_VECTOR", pos_f, total_n); B_w = b.sidx(joints, "FLOAT_VECTOR", pos_f, 0)
    n_w = frame_at(b.scl(b.add(A_w, B_w), 0.5))[2]
    d_w = dist(A_w, B_w); u_w = b.nrm(b.sub(B_w, A_w)); i_w = b.crs(n_w, u_w)
    qx, qy = b.math("ADD", d_w, lx), ly                                    # H1 in the chord frame
    rq = b.math("MAXIMUM", b.math("SQRT", b.math("ADD", b.math("MULTIPLY", qx, qx), b.math("MULTIPLY", qy, qy))), 1e-9)
    rc = b.clampf(rq, b.math("ADD", b.math("ABSOLUTE", b.math("SUBTRACT", L1, L2)), 1e-7), b.math("MULTIPLY", L12, 0.999999))
    aa = b.math("DIVIDE", b.math("ADD", b.math("SUBTRACT", b.math("MULTIPLY", L1, L1), b.math("MULTIPLY", L2, L2)), b.math("MULTIPLY", rc, rc)),
                b.math("MULTIPLY", rc, 2.0))
    hh = b.math("SQRT", b.math("MAXIMUM", b.math("SUBTRACT", b.math("MULTIPLY", L1, L1), b.math("MULTIPLY", aa, aa)), 0.0))
    exx, exy = b.math("DIVIDE", qx, rq), b.math("DIVIDE", qy, rq)
    sh = b.math("MULTIPLY", side, hh)
    px_ = b.math("SUBTRACT", b.math("MULTIPLY", aa, exx), b.math("MULTIPLY", sh, exy))   # a*ex + s*h*(-ey, ex)
    py_ = b.math("ADD", b.math("MULTIPLY", aa, exy), b.math("MULTIPLY", sh, exx))
    W = lambda x_, y_: b.add(A_w, b.add(b.scl(u_w, x_), b.scl(i_w, y_)))
    H2_w, H1_w = W(px_, py_), W(qx, qy)

    def place(part, p0r, p1r, p0w, p1w):
        rr = b.n("FunctionNodeAxesToRotation", primary_axis="X", secondary_axis="Z")
        b.put(rr.inputs["Primary Axis"], b.nrm(b.sub(p1r, p0r))); b.put(rr.inputs["Secondary Axis"], n_r)
        rw = b.n("FunctionNodeAxesToRotation", primary_axis="X", secondary_axis="Z")
        b.put(rw.inputs["Primary Axis"], b.nrm(b.sub(p1w, p0w))); b.put(rw.inputs["Secondary Axis"], n_w)
        inv = b.n("FunctionNodeInvertRotation"); b.put(inv.inputs[0], rr.outputs[0])
        R = b.n("FunctionNodeRotateRotation", rotation_space="GLOBAL")
        b.put(R.inputs["Rotation"], inv.outputs[0]); b.put(R.inputs["Rotate By"], rw.outputs[0])
        rv_ = b.n("FunctionNodeRotateVector"); b.put(rv_.inputs["Vector"], p0r); b.put(rv_.inputs["Rotation"], R.outputs[0])
        g2i_ = b.n("GeometryNodeGeometryToInstance"); b.put(g2i_.inputs[0], part)
        rot_ = b.n("GeometryNodeRotateInstances"); b.put(rot_.inputs["Instances"], g2i_.outputs[0])
        b.put(rot_.inputs["Rotation"], R.outputs[0]); rot_.inputs["Local Space"].default_value = False
        tr_ = b.n("GeometryNodeTranslateInstances"); b.put(tr_.inputs["Instances"], rot_.outputs[0])
        b.put(tr_.inputs["Translation"], b.sub(p0w, rv_.outputs[0])); tr_.inputs["Local Space"].default_value = False
        return tr_.outputs[0]

    cj = b.n("GeometryNodeJoinGeometry", label="Clasp")
    # shared rest pins: both parts of a hinge turn about the SAME point, so a slightly off pivot never opens a gap
    for g in (place(cparts["Clasp Body"][0], H1_r, B_r, H1_w, B_w),
              place(cparts["Clasp Inner Blade"][0], H2_r, H1_r, H2_w, H1_w),
              place(cparts["Clasp Outer Blade"][0], A_r, H2_r, A_w, H2_w)):
        b.put(cj.inputs[0], g)
    clasp_has_piv = b.boolm("AND", b.boolm("AND", cparts["Clasp Body"][1]["Has Pivots"], cparts["Clasp Inner Blade"][1]["Has Pivots"]),
                            cparts["Clasp Outer Blade"][1]["Has Pivots"])
    clasp_geo = b.switch("GEOMETRY", b.boolm("AND", clasp_on, clasp_has_piv), None, cj.outputs[0])

    # ---- place each piece: Start pivot on joint i, pointing at joint i+1
    x0 = b.x + 900
    t = b.named("INT", "_cs_t", x=x0, y=-300)["Attribute"]; fF = b.named("FLOAT", "_cs_f", x=x0, y=-450)["Attribute"]
    sc = b.named("FLOAT_VECTOR", "_cs_scale", x=x0, y=-600)["Attribute"]
    Sv = b.named("FLOAT_VECTOR", "_cs_S", x=x0, y=-750)["Attribute"]; Ev = b.named("FLOAT_VECTOR", "_cs_E", x=x0, y=-900)["Attribute"]
    b.sec(x0 - 300, 1500)
    PaP = b.sidx(joints, "FLOAT_VECTOR", pos_f, idx_f)
    PnP = b.sidx(joints, "FLOAT_VECTOR", pos_f, b.imath("ADD", idx_f, 1))
    Tm, Nm, _ax = frame_at(b.scl(b.add(PaP, PnP), 0.5))
    chord_v = b.sub(PnP, PaP)
    short = b.cmp("FLOAT", "LESS_THAN", b.len(chord_v), 1e-7)
    u = b.switch("VECTOR", short, b.nrm(chord_v), Tm, label="Chain Direction")
    PbP = b.add(PaP, b.scl(u, b.named("FLOAT", "_cs_pitch")["Attribute"]))
    a2r = b.n("FunctionNodeAxesToRotation", x0 + 1200, 0, primary_axis="X", secondary_axis="Z")
    # piece convention (same as the clasp): local X along the chain, local Z along the pins (bracelet width),
    # so local +Y faces the inside of the bracelet
    b.put(a2r.inputs["Primary Axis"], u); b.put(a2r.inputs["Secondary Axis"], _ax)
    R_curve = a2r.outputs[0]
    v_loc = b.vmath("MULTIPLY", b.vmath("SUBTRACT", Ev, Sv, x=x0 + 400, y=-800), sc, x=x0 + 600, y=-800)
    v_short = b.cmp("FLOAT", "LESS_THAN", b.vmath("LENGTH", v_loc, x=x0 + 800, y=-700, out="Value"), 1e-7, x=x0 + 1000, y=-700)
    vhat = b.switch("VECTOR", v_short, b.vmath("NORMALIZE", v_loc, x=x0 + 800, y=-850), (1.0, 0.0, 0.0), x=x0 + 1200, y=-800)
    vz = b.sep(vhat, x=x0 + 1200, y=-950)
    near_z = b.cmp("FLOAT", "GREATER_THAN", b.math("ABSOLUTE", vz["Z"], x=x0 + 1400, y=-950), 0.99, x=x0 + 1600, y=-950)
    sec = b.switch("VECTOR", near_z, (0.0, 0.0, 1.0), (0.0, 1.0, 0.0), x=x0 + 1800, y=-950)
    rl = b.n("FunctionNodeAxesToRotation", x0 + 1800, -800, primary_axis="X", secondary_axis="Z")
    b.put(rl.inputs["Primary Axis"], vhat); b.put(rl.inputs["Secondary Axis"], sec)
    inv_rl = b.n("FunctionNodeInvertRotation", x0 + 2000, -800); b.put(inv_rl.inputs[0], rl.outputs[0])
    eul = b.index_switch("VECTOR", t, [b.vmath("SCALE", G("Copy Rotation"), scale=fF, x=x0 + 1400, y=-400),
                                       G("Start Cap Rotation"), G("End Cap Rotation")], x=x0 + 1600, y=-400)
    e2r = b.n("FunctionNodeEulerToRotation", x0 + 1800, -400); b.put(e2r.inputs[0], eul)
    r1 = b.n("FunctionNodeRotateRotation", x0 + 2000, -200, rotation_space="LOCAL")
    b.put(r1.inputs["Rotation"], R_curve); b.put(r1.inputs["Rotate By"], e2r.outputs[0])
    rt = b.n("FunctionNodeRotateRotation", x0 + 2200, -300, rotation_space="LOCAL")
    b.put(rt.inputs["Rotation"], r1.outputs[0]); b.put(rt.inputs["Rotate By"], inv_rl.outputs[0])
    R_total = rt.outputs[0]
    off = b.index_switch("VECTOR", t, [b.vmath("SCALE", G("Copy Offset"), scale=fF, x=x0 + 1400, y=200),
                                       G("Start Cap Offset"), G("End Cap Offset")], x=x0 + 1600, y=200)
    rv = b.n("FunctionNodeRotateVector", x0 + 1800, 200); b.put(rv.inputs["Vector"], off); b.put(rv.inputs["Rotation"], R_curve)
    rs = b.n("FunctionNodeRotateVector", x0 + 2400, -500)
    b.put(rs.inputs["Vector"], b.vmath("MULTIPLY", Sv, sc, x=x0 + 2200, y=-550)); b.put(rs.inputs["Rotation"], R_total)
    T = b.vmath("SUBTRACT", b.vmath("ADD", PaP, rv.outputs[0], x=x0 + 2400, y=200), rs.outputs[0], x=x0 + 2600, y=100,
                label="Piece Origin")
    setp = b.n("GeometryNodeSetPosition", x0 + 2800, 300); b.put(setp.inputs["Geometry"], pts); b.put(setp.inputs["Position"], T)

    g2i = b.n("GeometryNodeGeometryToInstance", x0 + 2800, 600)
    for s in reversed(sources): b.put(g2i.inputs[0], s)  # latest link is first -> index 0 = link
    iop = b.n("GeometryNodeInstanceOnPoints", x0 + 3100, 300)
    b.put(iop.inputs["Points"], setp.outputs[0]); b.put(iop.inputs["Instance"], g2i.outputs[0])
    iop.inputs["Pick Instance"].default_value = True
    b.put(iop.inputs["Instance Index"], t); b.put(iop.inputs["Rotation"], R_total); b.put(iop.inputs["Scale"], sc)
    with_clasp = b.n("GeometryNodeJoinGeometry", x0 + 3200, 600)
    b.put(with_clasp.inputs[0], clasp_geo); b.put(with_clasp.inputs[0], iop.outputs[0])  # latest first -> chain, clasp
    real = b.n("GeometryNodeRealizeInstances", x0 + 3300, 450); b.put(real.inputs[0], with_clasp.outputs[0])
    chain = b.switch("GEOMETRY", G("Realize Instances"), with_clasp.outputs[0], real.outputs[0], x=x0 + 3500, y=300)

    # ---- pivot markers (viewport only): every joint + the two outer clasp anchors
    mean_pitch = b.n("GeometryNodeAttributeStatistic", x0 + 2800, -1200, data_type="FLOAT", domain="POINT")
    b.put(mean_pitch.inputs["Geometry"], pts); b.put(mean_pitch.inputs["Attribute"], b.named("FLOAT", "_cs_pitch", x=x0 + 2600, y=-1200)["Attribute"])
    rad = b.math("MAXIMUM", b.math("MULTIPLY", mean_pitch.outputs["Mean"], 0.12, x=x0 + 3000, y=-1200), 0.0003, x=x0 + 3200, y=-1200)
    ico = b.n("GeometryNodeMeshIcoSphere", x0 + 3400, -1200); b.put(ico.inputs["Radius"], rad); ico.inputs["Subdivisions"].default_value = 2
    marks = []
    for k, P in enumerate((PaP, PbP)):
        sp = b.n("GeometryNodeSetPosition", x0 + 3400, -900 - 150 * k); b.put(sp.inputs["Geometry"], pts); b.put(sp.inputs["Position"], P)
        ip = b.n("GeometryNodeInstanceOnPoints", x0 + 3600, -900 - 150 * k)
        b.put(ip.inputs["Points"], sp.outputs[0]); b.put(ip.inputs["Instance"], ico.outputs["Mesh"])
        marks.append(ip.outputs[0])
    # clasp blade hinges
    hp = b.n("GeometryNodePoints", x0 + 3200, -1400); hp.inputs["Count"].default_value = 2
    b.put(hp.inputs["Position"], b.switch("VECTOR", b.cmp("INT", "EQUAL", idx_f, 0), H2_w, H1_w, x=x0 + 3000, y=-1400))
    ihp = b.n("GeometryNodeInstanceOnPoints", x0 + 3600, -1400)
    b.put(ihp.inputs["Points"], hp.outputs[0]); b.put(ihp.inputs["Instance"], ico.outputs["Mesh"])
    marks.append(b.switch("GEOMETRY", b.boolm("AND", clasp_on, clasp_has_piv, x=x0 + 3600, y=-1550), None, ihp.outputs[0], x=x0 + 3700, y=-1500))
    mj = b.n("GeometryNodeJoinGeometry", x0 + 3800, -900)
    for m in marks: b.put(mj.inputs[0], m)
    mr = b.n("GeometryNodeRealizeInstances", x0 + 4000, -900); b.put(mr.inputs[0], mj.outputs[0])
    isv = b.n("GeometryNodeIsViewport", x0 + 3800, -700)
    show = b.boolm("AND", G("Show Pivots"), isv.outputs[0], x=x0 + 4000, y=-700)
    markers = b.switch("GEOMETRY", show, None, mr.outputs[0], x=x0 + 4200, y=-800)

    out = b.n("GeometryNodeJoinGeometry", x0 + 4400, 0)
    b.put(out.inputs[0], chain); b.put(out.inputs[0], markers)
    clean = b.n("GeometryNodeRemoveAttribute", x0 + 4600, 0); b.put(clean.inputs["Geometry"], out.outputs[0])
    clean.inputs["Pattern Mode"].default_value = "Wildcard"; clean.inputs["Name"].default_value = "_cs_*"
    geo = clean.outputs[0]

    # ---- warnings (each gates the output so it is evaluated)
    need_piv = b.cmp("INT", "EQUAL", mode, 0, x=x0 + 4400, y=500)
    msgs = [
        (no_curve, "Pick a Curve Object (an un-beveled curve)"),
        (b.boolm("AND", need_piv, b.boolm("NOT", pv[0]["Has Pivots"], x=x0 + 4400, y=650), x=x0 + 4600, y=650),
         "Link has no pivots: Edit Mode > select the joint vertices > right-click > ChainSmith > Set Start / End Pivot"),
        (b.boolm("AND", b.boolm("AND", need_piv, G("Start Cap"), x=x0 + 4400, y=800),
                 b.boolm("NOT", pv[1]["Has Pivots"], x=x0 + 4400, y=900), x=x0 + 4600, y=800),
         "Start cap has no pivots (or no object picked)"),
        (b.boolm("AND", b.boolm("AND", need_piv, G("End Cap"), x=x0 + 4400, y=1000),
                 b.boolm("NOT", pv[2]["Has Pivots"], x=x0 + 4400, y=1100), x=x0 + 4600, y=1000),
         "End cap has no pivots (or no object picked)"),
        (b.boolm("AND", clasp_on, b.boolm("NOT", clasp_has_piv, x=x0 + 4400, y=1300), x=x0 + 4600, y=1200),
         "Clasp: pick Body, Inner Blade and Outer Blade, and set two pivots (its pins) on each part"),
        (b.boolm("AND", b.boolm("AND", clasp_on, clasp_has_piv, x=x0 + 4400, y=1450), clasp_apart, x=x0 + 4600, y=1400),
         "Clasp parts must sit closed and assembled (shared hinge pins at the same spot)"),
    ]
    for k, (cond, msg) in enumerate(msgs):
        w = b.n("GeometryNodeWarning", x0 + 4800, 300 + 150 * k); b.put(w.inputs["Show"], cond); w.inputs["Message"].default_value = msg
        geo = b.switch("GEOMETRY", w.outputs[0], geo, geo, x=x0 + 5000 + 200 * k, y=0)
    b.put(go.inputs["Geometry"], geo)
    return ng


def main():
    a = sys.argv[sys.argv.index("--") + 1:]
    out = a[0]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    ng = build_main()
    ng["chainsmith_version"] = GROUP_VERSION
    groups = [ng] + [g for g in bpy.data.node_groups if g.name.startswith(".CS ")]
    for g in groups: g.use_fake_user = True
    if os.path.exists(out): os.remove(out)
    bpy.data.libraries.write(out, set(groups), fake_user=True, compress=True)
    print("BUILT", GROUP, "->", out, sorted(g.name for g in groups))


if __name__ == "__main__":
    main()
