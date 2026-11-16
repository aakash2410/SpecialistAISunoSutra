"""Dimensioned drawing of every laser-cut part (for people, not the laser). Units: mm.
Matches make_laser.py. Writes drawing.html (A3 landscape) and drawing_png.html for a PNG."""
FS = 7.5
o = []
a = o.append

def arrow(x, y, dx, dy):
    # small filled arrowhead at (x, y) pointing along (dx, dy)
    import math
    L = math.hypot(dx, dy); ux, uy = dx / L, dy / L; px, py = -uy, ux
    s = 3.2
    a(f'<polygon points="{x},{y} {x-ux*s+px*s*0.35},{y-uy*s+py*s*0.35} {x-ux*s-px*s*0.35},{y-uy*s-py*s*0.35}" fill="#1E5EA8"/>')

def hdim(x1, x2, y, text, ext=None):
    if ext is not None:
        for x in (x1, x2):
            a(f'<line x1="{x}" y1="{ext}" x2="{x}" y2="{y + (2 if y > ext else -2)}" stroke="#1E5EA8" stroke-width="0.3"/>')
    a(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="#1E5EA8" stroke-width="0.4"/>')
    arrow(x1, y, -1, 0); arrow(x2, y, 1, 0)
    a(f'<text x="{(x1+x2)/2}" y="{y-1.8}" font-size="{FS}" text-anchor="middle" fill="#1E5EA8">{text}</text>')

def vdim(y1, y2, x, text, ext=None):
    if ext is not None:
        for y in (y1, y2):
            a(f'<line x1="{ext}" y1="{y}" x2="{x + (2 if x > ext else -2)}" y2="{y}" stroke="#1E5EA8" stroke-width="0.3"/>')
    a(f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" stroke="#1E5EA8" stroke-width="0.4"/>')
    arrow(x, y1, 0, -1); arrow(x, y2, 0, 1)
    tx = x + 2 if x > (ext if ext is not None else x - 1) else x - 2
    anchor = "start" if tx > x else "end"
    a(f'<text x="{tx}" y="{(y1+y2)/2+2.5}" font-size="{FS}" text-anchor="{anchor}" fill="#1E5EA8">{text}</text>')

def box(x, y, w, h, sw=0.8, dash=False, col="#17222D"):
    d = ' stroke-dasharray="3 2"' if dash else ''
    a(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{col}" stroke-width="{sw}"{d}/>')

def hole(x, y, w, h):
    a(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#FBE3DA" stroke="#C4471C" stroke-width="0.5"/>')

def circ(cx, cy, d):
    a(f'<circle cx="{cx}" cy="{cy}" r="{d/2}" fill="#FBE3DA" stroke="#C4471C" stroke-width="0.5"/>')

def text(x, y, t, size=FS, anchor="start", weight=400, col="#17222D"):
    a(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{col}">{t}</text>')

def title(x, y, t, sub):
    text(x, y, t, 10, weight=600)
    text(x, y + 9, sub, 6.5, col="#5A6B7B")

# ------------------------------------------------------------ header
text(30, 24, "Specialist case: laser-cut parts", 15, weight=600)
text(30, 36, "3 mm MDF or acrylic. All sizes in mm. Outside size 191 × 224 × 86. Drawn at the same scale throughout. "
     "Walls are shown from outside. Jointed edges have 3 mm finger joints; sizes are overall. Cut file: specialist_case_3mm.dxf", 7, col="#5A6B7B")

# ------------------------------------------------------------ base
X, Y = 40, 70
title(X, Y - 14, "Base ×1", "Front edge at the bottom. Fingers on all four edges. Blue dashed = score only")
box(X, Y, 191, 224, 1)
for x0, x1, y0, y1, n in ((49, 139.5, 35, 138.5, "Jetson"), (49, 165, 146, 217, "Power bank"), (145, 185, 43, 123, "Speaker"), (4, 44, 4, 44, "Camera")):
    box(X + x0, Y + 224 - y1, x1 - x0, y1 - y0, 0.5, True, "#1E5EA8")
    text(X + (x0 + x1) / 2, Y + 224 - (y0 + y1) / 2 + 2.5, n, 6.5, "middle", col="#1E5EA8")
for s in (33, 103, 173):
    hole(X + 45, Y + 224 - s - 20, 3, 20)
for s in (68, 108, 148):
    hole(X + s, Y + 224 - 144, 20, 3)
hdim(X, X + 191, Y + 224 + 12, "191", ext=Y + 224)
vdim(Y, Y + 224, X - 10, "224", ext=X)
text(X, Y + 224 + 24, "Slots 3 × 20: at 45 from the left, 33 / 103 / 173 from the front edge (channel partition).", 6.5)
text(X, Y + 224 + 32, "Slots 20 × 3: 141 from the front edge, at 68 / 108 / 148 from the left (back partition).", 6.5)

# ------------------------------------------------------------ lid
X, Y = 290, 70
title(X, Y - 14, "Lid ×1", "Seen from above, front edge at the bottom. Plain edges")
box(X, Y, 191, 224, 1)
circ(X + 100, Y + 224 - 67, 50)
text(X + 100, Y + 224 - 67 + 2.5, "Ø50", 7, "middle", col="#C4471C")
hdim(X, X + 100, Y + 224 - 67 + 34, "100", ext=None)
vdim(Y + 224 - 67, Y + 224, X + 160, "67", ext=None)
hdim(X, X + 191, Y + 224 + 12, "191", ext=Y + 224)
vdim(Y, Y + 224, X + 191 + 10, "224", ext=X + 191)

# ------------------------------------------------------------ front
X, Y = 560, 70
title(X, Y - 14, "Front wall ×1", "Seen from outside. Fingers on the sides and bottom, top plain")
box(X, Y, 191, 83, 1)
hole(X + 74, Y + 83 - 67, 60, 46)
text(X + 104, Y + 83 - 44 + 2.5, "Screen 60 × 46", 6.5, "middle", col="#C4471C")
circ(X + 24, Y + 83 - 43, 16); circ(X + 10, Y + 83 - 43, 4); circ(X + 38, Y + 83 - 43, 4)
hdim(X, X + 74, Y + 74, "74", ext=None)
vdim(Y + 83 - 21, Y + 83, X + 191 + 10, "21", ext=X + 134)
vdim(Y + 83 - 67, Y + 83 - 21, X + 191 + 10, "46", ext=X + 134)
hdim(X, X + 191, Y + 83 + 12, "191", ext=Y + 83)
vdim(Y, Y + 83, X - 10, "83", ext=X)
text(X, Y + 83 + 24, "Camera Ø16, centre 24 from the left, 43 up. Mics 2 × Ø4, centres 10 and 38 from the left, 43 up.", 6.5)

# ------------------------------------------------------------ back
X, Y = 560, 225
title(X, Y - 14, "Back wall ×1", "Seen from outside. Same edges as the front")
box(X, Y, 191, 83, 1)
hole(X + 155, Y + 83 - 22, 26, 12)
hdim(X, X + 155, Y + 83 - 28, "155", ext=None)
vdim(Y + 83 - 10, Y + 83, X + 191 + 10, "10", ext=X + 181)
hdim(X, X + 191, Y + 83 + 12, "191", ext=Y + 83)
text(X, Y + 83 + 24, "Mains plug slot 26 × 12.", 6.5)

# ------------------------------------------------------------ right side
X, Y = 40, 370
title(X, Y - 14, "Right wall ×1", "Seen from outside, front edge on the left. Fingers on front, back and bottom")
box(X, Y, 224, 83, 1)
for Yo in range(48, 112, 9):
    for Zo in (12, 21, 30, 39):
        circ(X + Yo, Y + 83 - Zo, 5)
hdim(X, X + 224, Y + 83 + 12, "224", ext=Y + 83)
vdim(Y, Y + 83, X - 10, "83", ext=X)
text(X, Y + 83 + 24, "Speaker grille: 32 holes Ø5 on a 9 mm grid, 8 across × 4 high.", 6.5)
text(X, Y + 83 + 32, "First hole centre 48 from the front edge and 12 up.", 6.5)

# ------------------------------------------------------------ left side
X, Y = 300, 370
title(X, Y - 14, "Left wall ×1", "Seen from outside, front edge on the right. Same edges as the right wall")
box(X, Y, 224, 83, 1)
for Zo in (20, 30, 40, 50, 60):
    hole(X + 224 - 115, Y + 83 - Zo - 2, 70, 4)
hdim(X, X + 224, Y + 83 + 12, "224", ext=Y + 83)
text(X, Y + 83 + 24, "Vents: 5 slots 70 × 4, from 45 to 115 from the front edge,", 6.5)
text(X, Y + 83 + 32, "centres 20 / 30 / 40 / 50 / 60 up.", 6.5)

# ------------------------------------------------------------ channel partition
X, Y = 40, 525
title(X, Y - 14, "Channel partition ×1", "Front end on the left. Three 20 × 3 tabs under the bottom edge")
box(X, Y, 218, 70, 1)
for s in (30, 100, 170):
    box(X + s, Y + 70, 20, 3, 0.8)
hole(X + 35, Y + 70 - 42, 98, 39)
text(X + 84, Y + 70 - 22, "Jetson ports 98 × 39", 6.5, "middle", col="#C4471C")
hole(X + 163, Y + 70 - 33, 30, 30)
text(X + 178, Y + 70 - 15, "Power", 6.5, "middle", col="#C4471C")
hdim(X, X + 218, Y + 70 + 12, "218", ext=Y + 73)
vdim(Y, Y + 70, X - 10, "70", ext=X)
text(X, Y + 70 + 24, "Ports cut-out: 35 from the front end, 3 up.", 6.5)
text(X, Y + 70 + 32, "Power hole 30 × 30: 163 from the front end, 3 up.", 6.5)
text(X, Y + 70 + 40, "Tabs at 30 / 100 / 170 from the front end.", 6.5)

# ------------------------------------------------------------ back partition
X, Y = 300, 525
title(X, Y - 14, "Back partition ×1", "Left end on the left. Three 20 × 3 tabs")
box(X, Y, 140, 70, 1)
for s in (20, 60, 100):
    box(X + s, Y + 70, 20, 3, 0.8)
hdim(X, X + 140, Y + 70 + 12, "140", ext=Y + 73)
vdim(Y, Y + 70, X + 150, "70", ext=X + 140)
text(X, Y + 70 + 24, "Tabs at 20 / 60 / 100 from the left end.", 6.5)

# ------------------------------------------------------------ screen pocket
X, Y = 560, 370
title(X, Y - 14, "Screen frame ×2", "Opening centred, 4 mm border")
box(X, Y, 96, 60, 1); hole(X + 4, Y + 4, 88, 52)
text(X + 48, Y + 32, "88 × 52", 6.5, "middle", col="#C4471C")
hdim(X, X + 96, Y + 60 + 10, "96", ext=Y + 60)
vdim(Y, Y + 60, X - 8, "60", ext=X)
X2 = 680
title(X2, Y - 14, "Back plate ×1", "Header slot on the left")
box(X2, Y, 96, 60, 1); hole(X2 + 6, Y + 8, 10, 44)
hdim(X2, X2 + 96, Y + 60 + 10, "96", ext=Y + 60)
text(X2 + 20, Y + 32, "Slot 10 × 44,", 6.5)
text(X2 + 20, Y + 40, "6 from left, 8 from top", 6.5)

# ------------------------------------------------------------ lid strips
X, Y = 560, 480
title(X, Y - 14, "Lid strips ×4", "Glued under the lid, 1 mm in from the walls")
for i, L in enumerate((198, 198, 181, 181)):
    box(X, Y + i * 14, L, 8, 0.8)
    text(X + L + 4, Y + i * 14 + 6.5, f"{L} × 8", 6.5)

svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 840 645" font-family="Helvetica, Arial, sans-serif">' \
      '<rect width="840" height="645" fill="#fff"/>' + "".join(o) + '</svg>'
open("drawing.svg", "w").write(svg)
open("drawing.html", "w").write('<!doctype html><meta charset="utf-8"><style>@page{size:420mm 322.5mm;margin:0}html,body{margin:0}svg{width:420mm;height:322.5mm;display:block}</style>' + svg)
open("drawing_png.html", "w").write('<!doctype html><meta charset="utf-8"><style>html,body{margin:0}svg{width:2520px;height:1935px;display:block}</style>' + svg)
print("ok")
