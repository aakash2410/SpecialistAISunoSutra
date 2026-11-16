"""Laser cut file for The Specialist's demo case: 3 mm MDF or acrylic, finger-jointed.

Measured: power bank 116 x 71 x 30 mm, speaker 80 x 40 x 40 mm (user, 14 Nov 2026).
Spec: Jetson Orin Nano dev kit 103 x 90.5 x 34.8 mm (NVIDIA).
Screen (Waveshare 2.8inch Resistive Touch LCD drawing): board 79.85 x 50.8, glass 69.2 x 50.0,
picture area 58.3 x 43.8, 5.4 thick; the frames were drawn for an assumed 86 x 50 board.
Assumed: camera case 40 x 40 mm;
ports up to 40 mm high; fan centre from the photo. Holes for assumed parts are 2 mm generous.
Outside size 191 x 224 x 86 mm. Writes specialist_case_3mm.dxf and .svg (mm).
"""
import math, sys

# Screen window, seen from outside: left edge from the front's left outside edge, bottom edge
# from the outside bottom. Change these from the measured screen, then run
#   python3 make_laser.py --front-only
# to get a DXF with only the front panel for a recut.
WIN_X, WIN_Z, WIN_W, WIN_H = 74, 21, 60, 46
FRONT_ONLY = "--front-only" in sys.argv
for a in sys.argv[1:]:          # --window=X,Z,W,H overrides the window for a recut
    if a.startswith("--window="):
        WIN_X, WIN_Z, WIN_W, WIN_H = (float(v) for v in a.split("=", 1)[1].split(","))

T = 3.0
CUT, SCORE = [], []          # polylines (closed point lists) per layer
CIRCLES = []                  # (cx, cy, r)

def n_for(L):
    n = max(3, round(L / 15))
    return n if n % 2 else n + 1

def edge(p0, p1, kind, tab_first):
    (x0, y0), (x1, y1) = p0, p1
    L = math.hypot(x1 - x0, y1 - y0); dx, dy = (x1 - x0) / L, (y1 - y0) / L
    nx, ny = -dy, dx
    if kind == 'P':
        return [p0, p1]
    n, pts = n_for(L), []
    for i in range(n):
        off = 0 if ((i % 2 == 0) == tab_first) else T
        for s in (L * i / n, L * (i + 1) / n):
            pts.append((x0 + dx * s + nx * off, y0 + dy * s + ny * off))
    return pts

def panel(ox, oy, w, h, edges):
    c = [(0, 0), (w, 0), (w, h), (0, h)]
    segs = [edge(c[i], c[(i + 1) % 4], k, tf) for i, (k, tf) in enumerate(edges)]
    # Meet each pair of neighbouring edges at the corner of their offset lines (top and
    # bottom edges are horizontal, left and right vertical), so no corner is cut diagonally.
    for i in range(4):
        a, b = segs[i], segs[(i + 1) % 4]
        corner = (b[0][0], a[-1][1]) if i in (0, 2) else (a[-1][0], b[0][1])
        a[-1] = corner
        b[0] = corner
    pts = [p for s in segs for p in s]
    clean = []
    for p in pts:
        if not clean or abs(p[0] - clean[-1][0]) > 1e-6 or abs(p[1] - clean[-1][1]) > 1e-6:
            clean.append(p)
    CUT.append([(ox + x, oy + y) for x, y in clean])

def poly(ox, oy, pts, layer=CUT):
    layer.append([(ox + x, oy + y) for x, y in pts])

def rect(ox, oy, x, y, w, h, layer=CUT):
    poly(ox, oy, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], layer)

def circ(ox, oy, cx, cy, d):
    CIRCLES.append((ox + cx, oy + cy, d / 2))

def tabbed(ox, oy, w, h, tabs):
    """Partition w x h with 20 mm tabs of depth T under its bottom edge, at the given x starts."""
    pts = [(0, 0), (w, 0), (w, h)]
    for s in sorted(tabs, reverse=True):
        pts += [(s + 20, h), (s + 20, h + T), (s, h + T), (s, h)]
    pts += [(0, h)]
    poly(ox, oy, pts)

W, D, HW = 191, 224, 83          # outer width, depth, wall height (lid adds 3)
J, P = 'J', 'P'

# Base, front edge at the bottom of the sheet
bx, by = 3, 3
panel(bx, by, W, D, [(J, False)] * 4)
yb = lambda Yo: D - Yo
for X0, X1, Y0, Y1 in ((49, 139.5, 35, 138.5), (49, 165, 146, 217), (145, 185, 43, 123), (4, 44, 4, 44)):
    rect(bx, by, X0, yb(Y1), X1 - X0, Y1 - Y0, SCORE)
for s in (30, 100, 170):                       # channel partition slots
    rect(bx, by, 45, yb(s + 3 + 20), T, 20)
for s in (20, 60, 100):                        # back partition slots
    rect(bx, by, 48 + s, yb(144), 20, T)

# Lid, fan opening
lx, ly = 200, 3
panel(lx, ly, W, D, [(P, True)] * 4)
circ(lx, ly, 100, D - 67, 50)

# Front, seen from outside
fx, fy = 397, 3
panel(fx, fy, W, HW, [(P, True), (J, True), (J, True), (J, True)])
zf = lambda Zo: HW - Zo
rect(fx, fy, WIN_X, zf(WIN_Z + WIN_H), WIN_W, WIN_H)   # screen window (21 to 67 up as first cut)
circ(fx, fy, 24, zf(43), 16)                   # camera lens
circ(fx, fy, 10, zf(43), 4); circ(fx, fy, 38, zf(43), 4)   # microphones
# Back, seen from outside (mirrored)
kx, ky = 397, 92
panel(kx, ky, W, HW, [(P, True), (J, True), (J, True), (J, True)])
rect(kx, ky, W - 36, zf(22), 26, 12)           # mains plug slot into the channel

# Right side, turned: front edge at the top, bottom edge on the right
rx, ry = 594, 3
panel(rx, ry, HW, D, [(J, False), (J, True), (J, False), (P, True)])
for Yo in range(48, 112, 9):
    for Zo in (12, 21, 30, 39):
        circ(rx, ry, HW - Zo, Yo, 5)
# Left side, turned: front edge at the top, bottom edge on the left
sx, sy = 683, 3
panel(sx, sy, HW, D, [(J, False), (P, True), (J, False), (J, True)])
for Zo in (20, 30, 40, 50, 60):
    rect(sx, sy, Zo - 2, 45, 4, 70)

# Channel partition: 218 long, 70 tall, tabs into the base
cx, cy = 3, 236
tabbed(cx, cy, 218, 70, (30, 100, 170))
rect(cx, cy, 35, 70 - 42, 98, 39)              # Jetson port cut-out
rect(cx, cy, 163, 70 - 33, 30, 30)             # power cable from the bank
# Back partition: 140 long
px, py = 227, 236
tabbed(px, py, 140, 70, (20, 60, 100))

# Screen pocket: two spacer frames and a back plate, 96 x 60
for i in range(2):
    qx = 373 + 102 * i
    rect(qx, 236, 0, 0, 96, 60)
    rect(qx, 236, 4, 4, 88, 52)
rect(577, 236, 0, 0, 96, 60)
rect(577, 236, 6, 8, 10, 44)                   # header slot

# Lid locating strips
for i, L in enumerate((198, 198, 181, 181)):
    rect(679, 236 + 12 * i, 0, 0, L, 8)

# ---------------------------------------------------------------- write
def write_svg(path):
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="900mm" height="400mm" viewBox="0 0 900 400">']
    for layer, col in ((CUT, "#FF0000"), (SCORE, "#0000FF")):
        for pts in layer:
            d = "M" + " L".join(f"{x:.3f} {y:.3f}" for x, y in pts) + " Z"
            out.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="0.1"/>')
    for x, y, r in CIRCLES:
        out.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{r:.3f}" fill="none" stroke="#FF0000" stroke-width="0.1"/>')
    out.append("</svg>")
    open(path, "w").write("\n".join(out))

def write_dxf(path):
    o = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", "4", "0", "ENDSEC",
         "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", "2",
         "0", "LAYER", "2", "CUT", "70", "0", "62", "1",
         "0", "LAYER", "2", "SCORE", "70", "0", "62", "5",
         "0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    flip = lambda y: 400 - y     # DXF y runs up
    for layer, name in ((CUT, "CUT"), (SCORE, "SCORE")):
        for pts in layer:   # one closed polyline per shape, so laser software sees whole parts
            o += ["0", "POLYLINE", "8", name, "66", "1", "10", "0", "20", "0", "30", "0", "70", "1"]
            for x, y in pts:
                o += ["0", "VERTEX", "8", name, "10", f"{x:.3f}", "20", f"{flip(y):.3f}", "30", "0"]
            o += ["0", "SEQEND", "8", name]
    for x, y, r in CIRCLES:
        o += ["0", "CIRCLE", "8", "CUT", "10", f"{x:.3f}", "20", f"{flip(y):.3f}", "30", "0", "40", f"{r:.3f}"]
    o += ["0", "ENDSEC", "0", "EOF"]
    open(path, "w").write("\n".join(o) + "\n")

if FRONT_ONLY:   # keep only shapes inside the front panel's area of the sheet
    inside = lambda x, y: fx - 1 <= x <= fx + W + 1 and fy - 1 <= y <= fy + HW + 1
    CUT[:] = [p for p in CUT if all(inside(x, y) for x, y in p)]
    SCORE[:] = []
    CIRCLES[:] = [c for c in CIRCLES if inside(c[0], c[1])]
    write_svg("specialist_front_3mm.svg")
    write_dxf("specialist_front_3mm.dxf")
else:
    write_svg("specialist_case_3mm.svg")
    write_dxf("specialist_case_3mm.dxf")
xs = [x for l in (CUT, SCORE) for p in l for x, _ in p]; ys = [y for l in (CUT, SCORE) for p in l for _, y in p]
print("extent", round(min(xs)), round(max(xs)), round(min(ys)), round(max(ys)), "cut polys", len(CUT), "circles", len(CIRCLES))
