"""Plain-Python prototype of the ChainSmith rig solver (validated before encoding it in Geometry Nodes).

Joints J0..J(M-1) form a closed loop:
  J0 = clasp body pin B = start of the first piece, J(M-1) = clasp outer-blade pin A = end of the last piece,
  segment i (Ji -> Ji+1) is rigid with length L[i]; the closing segment A -> B is the folding clasp,
  whose span can vary between d_closed (folded) and d_max (blades straight).
Each iteration (Jacobi, so it maps to parallel field evaluation):
  1. pull every joint toward the nearest curve point (strength fades to 0)
  2. rigid lengths (and the clasp span range)
  3. bend limits per joint (signed angle around the hinge axis; + = toward the inside of the bracelet)
Then an exact pass: forward kinematics from J0 with the exact lengths, and the clasp solved analytically.
Run: python reference/rig_prototype.py
"""
import math

def add(a, b): return (a[0]+b[0], a[1]+b[1], a[2]+b[2])
def sub(a, b): return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def mul(a, s): return (a[0]*s, a[1]*s, a[2]*s)
def dot(a, b): return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]
def cross(a, b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def length(a): return math.sqrt(dot(a, a))
def norm(a):
    l = length(a); return mul(a, 1/l) if l > 1e-12 else (1.0, 0.0, 0.0)
def rot(v, n, ang):  # rotate v around unit axis n (Rodrigues)
    c, s = math.cos(ang), math.sin(ang)
    return add(add(mul(v, c), mul(cross(n, v), s)), mul(n, dot(n, v)*(1-c)))

# ---------------------------------------------------------------- curve helpers (dense polyline)
def make_curve(fn, n=720, cyclic=True):
    pts = [fn(i/n if cyclic else i/(n-1)) for i in range(n)]
    return pts, cyclic

def nearest(curve, p):
    pts, cyc = curve; best, bd = None, 1e9
    m = len(pts) if cyc else len(pts)-1
    for i in range(m):
        a, b = pts[i], pts[(i+1) % len(pts)]
        ab = sub(b, a); t = max(0, min(1, dot(sub(p, a), ab)/max(dot(ab, ab), 1e-18)))
        q = add(a, mul(ab, t)); d = length(sub(p, q))
        if d < bd: bd, best = d, q
    return best

def arc_sample(curve, s):
    pts, cyc = curve
    segs = [(pts[i], pts[(i+1) % len(pts)]) for i in range(len(pts) if cyc else len(pts)-1)]
    tot = sum(length(sub(b, a)) for a, b in segs)
    if cyc: s %= tot
    for a, b in segs:
        l = length(sub(b, a))
        if s <= l: return add(a, mul(sub(b, a), s/l))
        s -= l
    a, b = segs[-1]; return add(b, mul(norm(sub(b, a)), s))

def chord_step(curve, s0, d):
    p0 = arc_sample(curve, s0); s = s0 + d
    for _ in range(8): s += d - length(sub(arc_sample(curve, s), p0))
    return s

# ---------------------------------------------------------------- clasp (rest pose, typical fold-over clasp pin layout)
A_r, H2_r, H1_r, B_r = (-0.01068, -0.03167, 0), (0.0258, -0.02196, 0), (-0.00747, -0.03129, 0), (0.00346, -0.03231, 0)

def clasp_rest():
    u = norm(sub(B_r, A_r)); d0 = length(sub(B_r, A_r))
    side = sub(H2_r, A_r); side = sub(side, mul(u, dot(side, u)))
    i = norm(side)  # inside = where the blades are
    to2 = lambda p: (dot(sub(p, A_r), u), dot(sub(p, A_r), i))
    h1, h2, bb = to2(H1_r), to2(H2_r), to2(B_r)
    L1, L2, L3 = length(sub(H2_r, A_r)), length(sub(H1_r, H2_r)), length(sub(B_r, H1_r))
    lock = ((h1[0]-bb[0])/L3, (h1[1]-bb[1])/L3)  # direction B->H1 in the chord frame (kept while opening)
    reach = L1 + L2
    # d_max: |A - H1| == reach with H1 = (d,0) + L3*lock
    cx, cy = lock[0]*L3, lock[1]*L3
    dmax = -cx + math.sqrt(reach**2 - cy**2)
    bend = 1 if (h2[0]*h1[1] - h2[1]*h1[0]) < 0 else -1  # which side H2 sits relative to A->H1
    return dict(d0=d0, dmax=dmax, L=(L1, L2, L3), lock=lock, h2=h2, bend=bend)

def solve_clasp(C, A, Bp, n):
    """A, Bp: world pins, n: hinge axis. Returns world H2, H1 and the span error (0 when reachable)."""
    d = length(sub(Bp, A)); u = norm(sub(Bp, A)); i = cross(n, u)
    L1, L2, L3 = C["L"]
    h1 = (d + L3*C["lock"][0], L3*C["lock"][1])
    r = math.hypot(*h1); err = max(0.0, r - (L1+L2))
    # circle-circle intersection (A,L1) & (H1,L2), pick the rest side
    r_c = min(r, L1+L2-1e-12)
    a = (L1*L1 - L2*L2 + r_c*r_c)/(2*r_c); h = math.sqrt(max(L1*L1 - a*a, 0))
    ex = (h1[0]/r, h1[1]/r); ey = (-ex[1], ex[0])
    s = C["bend"]
    cand = [(a*ex[0] + sg*h*ey[0], a*ex[1] + sg*h*ey[1]) for sg in (1, -1)]
    rest_side = 1 if (C["h2"][0]*h1[1] - C["h2"][1]*h1[0]) < 0 else -1
    pick = min(cand, key=lambda c: 0 if ((c[0]*h1[1]-c[1]*h1[0]) < 0) == (rest_side == 1) else 1)
    W = lambda q: add(A, add(mul(u, q[0]), mul(i, q[1])))
    return W(pick), W(h1), err

# ---------------------------------------------------------------- solver
def solve(curve, L, clasp, n_axis, s0=0.0, bend_in=math.radians(35), bend_out=math.radians(8), K=300, sub_it=2):
    M = len(L) + 1
    s = [s0]
    for l in L: s.append(chord_step(curve, s[-1], l))
    P = [arc_sample(curve, x) for x in s]
    C = clasp
    def seg_len(i):  # target length of segment i -> i+1 (cyclic); None for open chain end
        if i < M-1: return L[i]
        return "clasp" if C else None
    for k in range(K):
        alpha = 0.0 if k >= 0.9*K else max(0.05, 0.5*(1 - k/(0.7*K)))
        P = [add(p, mul(sub(nearest(curve, p), p), alpha)) for p in P]
        for _ in range(sub_it):
            D = [(0.0, 0.0, 0.0)]*M
            for i in range(M):
                tl = seg_len(i)
                if tl is None: continue
                j = (i+1) % M; d = sub(P[j], P[i]); dl = length(d)
                tgt = tl if tl != "clasp" else min(max(dl, C["d0"]), C["dmax"])
                corr = mul(norm(d), 0.5*(dl - tgt))
                D[i] = add(D[i], corr); D[j] = sub(D[j], corr)
            P = [add(P[i], D[i]) for i in range(M)]
            # bend limits
            D = [(0.0, 0.0, 0.0)]*M
            for i in range(M):
                if i == 0 or i == M-1: continue  # clasp joints: the clasp geometry limits them
                pi_, pn = P[(i-1) % M], P[(i+1) % M]
                e1, e2 = sub(P[i], pi_), sub(pn, P[i])
                th = math.atan2(dot(cross(e1, e2), n_axis), dot(e1, e2))
                ex = th - bend_in if th > bend_in else (th + bend_out if th < -bend_out else 0.0)
                if ex == 0.0: continue
                dn = sub(add(P[i], rot(e2, n_axis, -ex/2)), pn)
                dp = sub(sub(P[i], rot(e1, n_axis, ex/2)), pi_)
                # momentum-conserving: the joint moves against its neighbours (equal masses), so no drift
                D[(i+1) % M] = add(D[(i+1) % M], mul(dn, 2/3))
                D[(i-1) % M] = add(D[(i-1) % M], mul(dp, 2/3))
                D[i] = sub(D[i], mul(add(dn, dp), 1/3))
            P = [add(P[i], mul(D[i], 0.5)) for i in range(M)]
    # exact pass
    J = [P[0]]
    for i in range(M-1): J.append(add(J[-1], mul(norm(sub(P[i+1], J[-1])), L[i])))
    out = dict(J=J)
    if C:
        H2, H1, err = solve_clasp(C, J[-1], J[0], n_axis)
        out.update(H2=H2, H1=H1, clasp_err=err, span=length(sub(J[0], J[-1])))
    # metrics
    out["dev"] = max(length(sub(nearest(curve, p), p)) for p in J)
    ths = []
    for i in range(1, M-1):
        e1, e2 = sub(J[i], J[i-1]), sub(J[i+1], J[i])
        ths.append(math.degrees(math.atan2(dot(cross(e1, e2), n_axis), dot(e1, e2))))
    out["bend_range"] = (round(min(ths), 2), round(max(ths), 2))
    return out

if __name__ == "__main__":
    C = clasp_rest()
    print("clasp: d_closed %.4f  d_max %.4f  L %s" % (C["d0"], C["dmax"], [round(x, 4) for x in C["L"]]))
    pitch, nlink = 0.009, 18
    L = [pitch]*nlink
    loop = pitch*nlink + C["d0"]
    Z = (0, 0, 1)
    for label, R in (("exact fit (closed clasp)", loop/(2*math.pi)), ("curve 15% bigger (clasp opens)", 1.15*loop/(2*math.pi)),
                     ("curve 60% bigger (beyond reach)", 1.6*loop/(2*math.pi)), ("curve 10% smaller", 0.9*loop/(2*math.pi))):
        cv = make_curve(lambda t, R=R: (R*math.cos(2*math.pi*t), R*math.sin(2*math.pi*t), 0))
        r = solve(cv, L, C, Z)
        print("%-34s span %.4f (closed %.4f, max %.4f) clasp_err %.2e  max dev from curve %.4f  bends %s"
              % (label, r["span"], C["d0"], C["dmax"], r["clasp_err"], r["dev"], r["bend_range"]))
    # sliding test: move one curve point far from the clasp -> joints near the clasp should barely move
    base = make_curve(lambda t: (0.03*math.cos(2*math.pi*t), 0.03*math.sin(2*math.pi*t), 0))
    bump = make_curve(lambda t: (0.03*math.cos(2*math.pi*t), (0.03 + 0.006*math.exp(-((t-0.5)/0.08)**2))*math.sin(2*math.pi*t), 0))
    r0, r1 = solve(base, L, C, Z), solve(bump, L, C, Z)
    print("bump far from clasp: B moved %.5f, A moved %.5f" % (length(sub(r0["J"][0], r1["J"][0])), length(sub(r0["J"][-1], r1["J"][-1]))))
