# SPDX-License-Identifier: LGPL-2.1-or-later

"""3D model of the installation, built from the plan and kept up to date.

From the routing of the plan (network.py), each conduit run becomes a tube at its level: above
the suspended ceiling (forro) for ceiling and wall runs, in the floor screed for floor runs, in
the ground for buried runs (the service entrance). From the run, a drop goes down the wall (or
up from the floor) to each item's box: 4x2 boxes in the walls at the item height, octagonal 4x4
boxes on the ceiling for the lights, the panel box sized by its ways. Risers join the floors.
Bends have a fixed radius (BEND diameters), so the lengths match the bill of materials. Each
run takes the colour of its main circuit (a feeder first, then the largest section).

The service entrance ("padrão de entrada") is a concrete pole with the meter box; its buried
run to the panel is drawn like any conduit with installation "enterrado".

Everything is one Part feature (Instalacao3D) outside the installation group, so the plan
sheets do not draw it. Its isometric page is drawn here too, as an SVG (iso_svg): tubes as lines
of their circuit's colour, boxes as filled outlines, walls in light grey. A TechDraw view of the
solids would compute hidden lines for every tube, which takes tens of seconds on a house.
"""

import colorsys
import math

import FreeCAD as App

from . import calc, model, network
from .symbols import SYMBOLS

T_MODEL3D = model.T_MODEL3D
NAME = "Instalacao3D"

FORRO_GAP = 100.0      # mm: conduits run this far above the ceiling (forro)
FLOOR_DEPTH = 30.0     # mm: floor runs, in the screed below the finished floor
WALL_INSET = 0.0       # mm: extra depth of the boxes into the wall (their face is flush with it)
BEND = 6.0             # bend radius, in conduit diameters
BOX_42 = (57.0, 101.6, 50.0)   # 4x2 wall box: width, height, depth (mm)
BOX_44 = (101.6, 101.6, 50.0)  # 4x4 box
PLATE = (76.0, 122.0, 6.0)     # cover plate of a 4x2 box
POLE = (150.0, 7000.0)         # service entrance pole: section, height (mm)
METER_BOX = (350.0, 500.0, 160.0)  # meter box: width, height, depth (mm)

GREY = (0.35, 0.35, 0.35)
BOX_COLOR = (0.30, 0.30, 0.33)
CEILING_BOX_COLOR = (0.95, 0.55, 0.10)
PLATE_COLOR = (0.95, 0.95, 0.95)
LAMP_COLOR = (1.0, 0.85, 0.25)
PANEL_COLOR = (0.80, 0.80, 0.82)
CONCRETE = (0.62, 0.62, 0.60)
CEILING_COLOR = (0.96, 0.96, 0.94, 0.8)  # with its transparency: the conduits above show through
FORRO_THICKNESS = 10.0


# Kelly's colours of maximum contrast (without white and black), and a few more: circuits up to
# 20 never share a colour; above that the list repeats with a darker shade.
PALETTE = ["#f3c300", "#875692", "#f38400", "#a1caf1", "#be0032", "#c2b280", "#008856", "#e68fac",
           "#0067a5", "#f99379", "#604e97", "#f6a600", "#b3446c", "#dcd300", "#882d17", "#8db600",
           "#654522", "#e25822", "#2b3d26", "#00a3a3"]


def circuit_color(number):
    """A colour per circuit number, distinct from the other circuits' (up to 20)."""
    n = int(number)
    text = PALETTE[(n - 1) % len(PALETTE)]
    rgb = tuple(int(text[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    if (n - 1) // len(PALETTE) % 2:
        h, s, v = colorsys.rgb_to_hsv(*rgb)
        rgb = colorsys.hsv_to_rgb(h, s, v * 0.6)
    return rgb


def _section(c):
    return float(c.Section or c.SectionSuggested or 2.5)


def _diameter(text, default=20.0):
    try:
        return float(str(text).replace(",", ".")) if text else default
    except ValueError:
        return default


class _Model:
    """Collects the solids and their colours."""

    def __init__(self, doc, only=None, floor=None, ceiling=False):
        import Part

        self.Part = Part
        self.doc = doc
        self.only = {int(n) for n in only} if only else None  # circuit numbers shown (None: all)
        self.floor = floor  # elevation of the only floor shown (None: all)
        self.with_ceiling = ceiling  # a translucent forro under each room's ceiling
        self.net = network.network(doc)
        self.ceiling = self.net.ceiling
        self.solids = []      # (solid, colour)
        self.segments = []    # [p, q, diameter, colour]: tubes before the bends are cut in
        self._walls = None    # vertical faces of the BIM walls
        self.flat = []        # for the isometric page: ("line", [points], diameter, colour),
        #                       ("poly", [points], colour), ("circle", centre, radius, colour)

    def on_floor(self, z):
        return self.floor is None or abs(float(z) - self.floor) < model.FLOOR_TOLERANCE

    def shown(self, circuit):
        return self.only is None or (circuit is not None and circuit.Number in self.only)

    def panel_shown(self, p):
        """A panel shows when one of the circuits shown starts there or feeds it."""
        if self.only is None:
            return True
        names = {p.Label.lower(), (p.Letter or "").lower()} - {""}
        return any(c.Number in self.only and ((c.Panel or "").lower() in names or
                                              (getattr(c, "Feeds", "") or "").lower() in names)
                   for c in model.circuits(self.doc))

    def item_shown(self, p):
        if self.only is None:
            return True
        if p.Symbol in network.PANELS:
            return self.panel_shown(p)
        if p.Symbol == "padrao_entrada" or p.Symbol in network.RISERS:
            return False
        circuit = self.net.switch_circuit.get(p.Name) if network.is_switch(p.Symbol) else p.Circuit
        return self.shown(circuit)

    # -- levels ----------------------------------------------------------------------------------

    def level(self, node):
        x, y, z = self.net.nodes[node]
        cls = self.net.node_cls[node]
        if cls == "piso":
            return App.Vector(x, y, z - FLOOR_DEPTH)
        if cls == "enterrado":
            return App.Vector(x, y, z - network.BURIED_MM)
        return App.Vector(x, y, z + self.ceiling + FORRO_GAP)

    # -- runs ------------------------------------------------------------------------------------

    def edge_style(self, e):
        ed = self.net.edges[e]
        content = self.net.conductors(e)
        if self.only is not None:
            content = [(c, marks) for c, marks in content if c.Number in self.only]
            if not content:
                return None  # none of the circuits shown passes here
        sections = [_section(c) for c, marks in content for _ in marks]
        conduit = self.doc.getObject(ed["conduit"]) if ed.get("conduit") else None
        if conduit is not None and (conduit.Diameter or conduit.DiameterSuggested):
            diameter = _diameter(conduit.Diameter or conduit.DiameterSuggested)
        else:
            diameter = float(calc.suggest_conduit(sections)) if sections else 20.0
        if not content:
            return diameter, GREY
        main = max(content, key=lambda t: (bool(getattr(t[0], "Feeds", "")), _section(t[0]), -t[0].Number))[0]
        return diameter, circuit_color(main.Number)

    def runs(self):
        for e, ed in enumerate(self.net.edges):
            kind = ed["installation"]
            if kind == "caixa":
                continue  # floor and ceiling runs joined at an item: its drops do it
            if not (self.on_floor(self.net.nodes[ed["u"]][2]) or
                    (kind == "prumada" and self.on_floor(self.net.nodes[ed["v"]][2]))):
                continue
            style = self.edge_style(e)
            if style is None:
                continue
            diameter, color = style
            p, q = self.level(ed["u"]), self.level(ed["v"])
            if kind == "prumada":
                low, high = (p, q) if p.z <= q.z else (q, p)
                corner = App.Vector(low.x, low.y, high.z)
                self.segments.append([low, corner, diameter, color])
                if (high - corner).Length > 1:
                    self.segments.append([corner, high, diameter, color])
            else:
                self.segments.append([p, q, diameter, color])

    # -- items -----------------------------------------------------------------------------------

    def drops(self):
        """From each run level reaching an item, a drop (or rise) to the face of its box."""
        sections, own = {}, {}
        for p, c, marks, _ in self.net.vertical_runs():
            sections.setdefault(p.Name, []).extend([_section(c)] * len(marks))
            own.setdefault(p.Name, c)
        for p in model.points(self.doc):
            if not self.item_shown(p) or not self.on_floor(p.Position.z):
                continue
            box = self.item(p)
            node = self.net.attached.get(p.Name)
            if node is None or box is None:
                continue
            centre, half = box
            secs = sections.get(p.Name, [])
            diameter = float(calc.suggest_conduit(secs)) if secs else 20.0
            circuit = own.get(p.Name)
            special = p.Symbol in network.PANELS or p.Symbol == "padrao_entrada"
            color = circuit_color(circuit.Number) if circuit is not None and not special else GREY
            levels = [node] + [other for e, other in self.net.adjacent.get(node, [])
                               if self.net.edges[e]["installation"] == "caixa"]
            for n in levels:
                start = self.level(n)
                entry = centre + App.Vector(0, 0, half if start.z > centre.z else -half)
                above = App.Vector(entry.x, entry.y, start.z)
                if (above - start).Length > 1:
                    self.segments.append([start, above, diameter, color])
                if (entry - above).Length > 1:
                    self.segments.append([above, entry, diameter, color])

    def _panel_size(self, p):
        ways = sum(1 for c in model.circuits(self.doc) if c.Panel and c.Panel.lower() in
                   (p.Label.lower(), (p.Letter or "").lower()))
        ways = ways + calc.spare_ways(ways) + 2  # with the main breaker and the RCD/DPS
        rows = 1 if ways <= 24 else 2
        width = max(300.0, min(ways, 24) * 18.0 + 120.0)
        return width, 300.0 + 150.0 * rows, 100.0

    def wall_faces(self):
        if self._walls is None:
            self._walls = []
            for o in self.doc.Objects:
                if getattr(o, "IfcType", "") != "Wall":
                    continue
                shape = getattr(o, "Shape", None)
                if shape is None or shape.isNull():
                    continue
                for f in shape.Faces:
                    try:
                        n = f.normalAt(*f.Surface.parameter(f.CenterOfMass))
                    except Exception:
                        continue
                    if abs(n.z) < 0.05 and "Plane" in type(f.Surface).__name__:
                        self._walls.append(f)
        return self._walls

    def wall_at(self, point, reach):
        """(foot on the face, normal out of the wall toward `point`) of the wall face nearest to
        `point` within `reach` mm, at its height; None if there is none."""
        vertex = self.Part.Vertex(point)
        best = None
        for f in self.wall_faces():
            box = f.BoundBox
            if not (box.ZMin - 1 <= point.z <= box.ZMax + 1):
                continue
            if box.XMin - reach > point.x or box.XMax + reach < point.x or \
                    box.YMin - reach > point.y or box.YMax + reach < point.y:
                continue
            d, pairs, _ = f.distToShape(vertex)
            if d <= reach and (best is None or d < best[0]):
                foot = pairs[0][0]
                n = f.normalAt(*f.Surface.parameter(foot))
                n = App.Vector(n.x, n.y, 0)
                if n.Length < 1e-9:
                    continue
                n.normalize()
                best = (d, foot, n)
        if best is None:
            return None
        d, foot, n = best
        if (point - foot).dot(n) < -1e-6:
            n = -n
        return App.Vector(foot.x, foot.y, point.z), n

    def mount(self, p, z, fallback_dir):
        """Where a wall-mounted item's box goes: (face point, normal into the room) of the wall
        nearest to it, or its own position and direction when no BIM wall is near."""
        reach = max(400.0, 10.0 * model.scale_of(self.doc))
        found = self.wall_at(App.Vector(p.Position.x, p.Position.y, z), reach)
        if found is not None:
            return found
        return App.Vector(p.Position.x, p.Position.y, z), fallback_dir

    def _frame(self, p):
        d = App.Rotation(App.Vector(0, 0, 1), float(p.Rotation.Value)).multVec(App.Vector(1, 0, 0))
        return d, App.Vector(-d.y, d.x, 0)

    def item(self, p):
        """Draw the box (and fixture) of an item; returns (centre, half height) of the box where
        its conduits enter, or None for items without one (risers)."""
        sym = SYMBOLS[p.Symbol]
        base = App.Vector(p.Position)
        floor = float(p.Position.z)
        h = float(p.Height.Value)
        d, n = self._frame(p)
        if p.Symbol in network.RISERS:
            return None
        if p.Symbol == "padrao_entrada":
            return self.entrance(p)
        if p.Symbol in network.PANELS:
            w, bh, depth = self._panel_size(p)
            z = floor + (h or 1500.0)
            face, normal = self.mount(p, z, App.Vector(-n.x, -n.y, 0))
            centre = face - normal * (depth / 2)  # flush with the wall face, into the wall
            self.box(centre, App.Vector(-normal.y, normal.x, 0), w, depth, bh, PANEL_COLOR)
            return centre, bh / 2
        if sym.category == "Iluminação" and not sym.wall:
            # octagonal 4x4 box on the ceiling (above the forro) and the lamp below it
            top = floor + self.ceiling
            centre = App.Vector(base.x, base.y, top + BOX_44[2] / 2)
            self.octagon(centre, BOX_44[0], BOX_44[2], CEILING_BOX_COLOR)
            self.sphere(App.Vector(base.x, base.y, top - 60.0), 60.0, LAMP_COLOR)
            return centre, BOX_44[2] / 2
        if p.Symbol == "tomada_piso":
            centre = App.Vector(base.x, base.y, floor - BOX_44[2] / 2)
            self.box(centre, d, BOX_44[0], BOX_44[0], BOX_44[2], BOX_COLOR)
            return centre, BOX_44[2] / 2
        width, height, depth = BOX_44 if p.Symbol in ("caixa_passagem", "motor") else BOX_42
        z = floor + (h if h > 0 else 300.0)
        if sym.wall or sym.beside:
            # in the nearest wall: the box behind its face, the plate on it
            face, normal = self.mount(p, z, d)
            along = App.Vector(-normal.y, normal.x, 0)
            centre = face - normal * (depth / 2 + WALL_INSET)
            self.box(centre, along, width, depth, height, BOX_COLOR)
            if p.Symbol != "caixa_passagem":
                plate = face + normal * (PLATE[2] / 2)
                self.box(plate, along, PLATE[0], PLATE[2], PLATE[1], PLATE_COLOR)
        else:
            centre = App.Vector(base.x, base.y, z)
            self.box(centre, d, width, depth, height, BOX_COLOR)
        return centre, height / 2

    def entrance(self, p):
        """Service entrance: concrete pole with the meter box on its room side."""
        d, n = self._frame(p)
        floor = float(p.Position.z)
        h = float(p.Height.Value) or 1500.0
        side, height = POLE
        buried = 1000.0  # part of the pole in the ground
        self.box(App.Vector(p.Position.x, p.Position.y, floor - buried + height / 2), n, side, side, height, CONCRETE)
        w, bh, depth = METER_BOX
        centre = App.Vector(p.Position.x, p.Position.y, floor + h) + d * (side / 2 + depth / 2)
        self.box(centre, n, w, depth, bh, PANEL_COLOR)
        return centre, bh / 2

    # -- solids ----------------------------------------------------------------------------------

    def box(self, centre, along, width, depth, height, color):
        """Box `width` along `along`, `depth` across it, `height` up, centred on `centre`."""
        solid = self.Part.makeBox(width, depth, height)
        solid.translate(App.Vector(-width / 2, -depth / 2, -height / 2))
        solid.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), math.degrees(math.atan2(along.y, along.x)))
        solid.translate(centre)
        self.solids.append((solid, color))
        self.flat.append(("poly", [v.Point for v in solid.Vertexes], color))

    def octagon(self, centre, size, height, color):
        r = size / 2 / math.cos(math.pi / 8)
        pts = [App.Vector(centre.x + r * math.cos(math.pi / 8 + k * math.pi / 4),
                          centre.y + r * math.sin(math.pi / 8 + k * math.pi / 4), centre.z - height / 2)
               for k in range(8)]
        face = self.Part.Face(self.Part.makePolygon(pts + [pts[0]]))
        solid = face.extrude(App.Vector(0, 0, height))
        self.solids.append((solid, color))
        self.flat.append(("poly", [v.Point for v in solid.Vertexes], color))

    def sphere(self, centre, radius, color):
        self.solids.append((self.Part.makeSphere(radius, centre), color))
        self.flat.append(("circle", centre, radius, color))

    def tubes(self):
        """Tubes of the segments, with a fixed-radius bend where exactly two meet."""
        Part = self.Part
        segs = [s for s in self.segments if (s[1] - s[0]).Length > 1e-6]

        def key(v):
            return (round(v.x, 0), round(v.y, 0), round(v.z, 0))

        ends = {}
        for i, s in enumerate(segs):
            ends.setdefault(key(s[0]), []).append((i, 0))
            ends.setdefault(key(s[1]), []).append((i, 1))
        trimmed = [[App.Vector(s[0]), App.Vector(s[1])] for s in segs]
        arcs = []
        for k, users in ends.items():
            if len(users) != 2:
                if len(users) > 2:  # a junction: a fitting
                    i = users[0][0]
                    r = max(segs[j][2] for j, _ in users) / 2 * 1.25
                    v = segs[i][users[0][1]]
                    self.sphere(v, r, segs[i][3])
                continue
            (i, ei), (j, ej) = users
            v = segs[i][ei]
            a = segs[i][1 - ei] - v
            b = segs[j][1 - ej] - v
            la, lb = a.Length, b.Length
            if la < 1e-6 or lb < 1e-6:
                continue
            a, b = App.Vector(a).normalize(), App.Vector(b).normalize()
            phi = math.acos(max(-1.0, min(1.0, a.dot(b))))
            if phi > math.pi - 1e-3 or phi < 1e-3:
                continue  # straight on, or doubling back on itself
            diameter = max(segs[i][2], segs[j][2])
            radius = BEND * diameter
            t = radius * math.tan((math.pi - phi) / 2)
            t = min(t, 0.45 * la, 0.45 * lb)
            if t < 1e-3:
                continue
            radius = t / math.tan((math.pi - phi) / 2)
            pa, pb = v + a * t, v + b * t
            bis = App.Vector(a + b).normalize()
            centre = v + bis * (radius / math.sin(phi / 2))
            mid = centre - bis * radius
            trimmed[i][ei] = pa
            trimmed[j][ej] = pb
            arcs.append((pa, mid, pb, segs[i][2] / 2, segs[i][3], App.Vector(v - pa).normalize()))
        for (p, q), s in zip(trimmed, segs):
            axis = q - p
            if axis.Length < 1e-3:
                continue
            self.solids.append((Part.makeCylinder(s[2] / 2, axis.Length, p, axis), s[3]))
            self.flat.append(("line", [p, q], s[2], s[3]))
        for pa, mid, pb, r, color, tangent in arcs:
            arc = Part.Arc(pa, mid, pb).toShape()
            self.flat.append(("line", arc.discretize(8), 2 * r, color))
            try:
                path = Part.Wire([arc])
                profile = Part.Wire([Part.makeCircle(r, pa, tangent)])
                solid = path.makePipeShell([profile], True, True)
                self.solids.append((solid, color))
            except Exception:
                self.solids.append((Part.makeSphere(r, mid), color))  # keep the run closed

    def ceilings(self):
        """The forro of each room: its floor outline at the ceiling height, translucent (the
        conduits run above it)."""
        for o in self.doc.Objects:
            if getattr(o, "IfcType", "") != "Space":
                continue
            shape = getattr(o, "Shape", None)
            if shape is None or shape.isNull() or not self.on_floor(shape.BoundBox.ZMin):
                continue
            bottom = [f for f in shape.Faces if abs(f.BoundBox.ZMax - shape.BoundBox.ZMin) < 1e-3]
            for face in bottom:
                forro = face.copy()
                forro.translate(App.Vector(0, 0, self.ceiling))
                self.solids.append((forro.extrude(App.Vector(0, 0, FORRO_THICKNESS)), CEILING_COLOR))

    def build(self):
        self.runs()
        self.drops()
        self.tubes()
        if self.with_ceiling:
            self.ceilings()
        return self.solids


def build(doc, built=None):
    """(shape, face colours) of the 3D installation."""
    import Part

    m = built or _Model(doc)
    solids = m.solids if built is not None else m.build()
    if not solids:
        return Part.Shape(), []
    colors = []
    for solid, color in solids:
        colors += [tuple(color)] * len(solid.Faces)
    return Part.makeCompound([s for s, _ in solids]), colors


# --- isometric page -----------------------------------------------------------------------------

ISO_RIGHT = App.Vector(1, 1, 0).normalize()
ISO_UP = App.Vector(-1, 1, 2).normalize()
ISO_TOWARD = App.Vector(1, -1, 1).normalize()  # from the drawing toward the viewer (front, right, top)
WALL_GREY = "#b0b0b0"
STANDARD_SCALES = [10, 20, 25, 50, 75, 100, 125, 200, 250, 500, 1000]


def _hex(color):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c * 255)))) for c in color[:3])


def _hull(points):
    pts = sorted(set((round(x, 3), round(y, 3)) for x, y in points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _wall_lines(doc):
    """Straight edges of the walls, for context on the isometric page."""
    out = []
    for o in doc.Objects:
        if getattr(o, "IfcType", "") != "Wall":
            continue
        shape = getattr(o, "Shape", None)
        if shape is None or shape.isNull():
            continue
        for e in shape.Edges:
            if len(e.Vertexes) == 2 and "Line" in type(e.Curve).__name__:
                out.append([e.Vertexes[0].Point, e.Vertexes[1].Point])
    return out


def iso_svg(doc, built, area):
    """SVG of the isometric view of the installation, fitted to `area` (width, height in paper mm)
    at a standard scale. Returns (svg, width, height, scale)."""
    def proj(p):
        return p.dot(ISO_RIGHT), p.dot(ISO_UP)

    items = []  # (depth, svg element builder)
    walls = _wall_lines(doc)
    pts = []
    for line in walls:
        pts += [proj(p) for p in line]
    for item in built.flat:
        if item[0] in ("line", "poly"):
            pts += [proj(p) for p in item[1]]
        else:
            x, y = proj(item[1])
            pts += [(x - item[2], y - item[2]), (x + item[2], y + item[2])]
    if not pts:
        return "", 0.0, 0.0, 1.0
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    scale = next((n for n in STANDARD_SCALES if (x1 - x0) / n <= area[0] and (y1 - y0) / n <= area[1]),
                 STANDARD_SCALES[-1])
    margin = 2.0

    def xy(p):
        x, y = proj(p)
        return (x - x0) / scale + margin, (y1 - y) / scale + margin

    def fmt(points):
        return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)

    for line in walls:
        depth = sum(p.dot(ISO_TOWARD) for p in line) / 2 - 1e9  # walls behind everything
        items.append((depth, f'<polyline points="{fmt([xy(p) for p in line])}" fill="none" stroke="{WALL_GREY}" '
                             f'stroke-width="0.13"/>'))
    for item in built.flat:
        if item[0] == "line":
            _, points, diameter, color = item
            depth = sum(p.dot(ISO_TOWARD) for p in points) / len(points)
            width = max(0.45, diameter / scale * 1.5)
            items.append((depth, f'<polyline points="{fmt([xy(p) for p in points])}" fill="none" '
                                 f'stroke="{_hex(color)}" stroke-width="{width:.2f}" stroke-linecap="round" '
                                 f'stroke-linejoin="round"/>'))
        elif item[0] == "poly":
            _, points, color = item
            depth = sum(p.dot(ISO_TOWARD) for p in points) / len(points)
            hull = _hull([xy(p) for p in points])
            items.append((depth, f'<polygon points="{fmt(hull)}" fill="{_hex(color)}" stroke="#202020" '
                                 f'stroke-width="0.13"/>'))
        else:
            _, centre, radius, color = item
            x, y = xy(centre)
            items.append((centre.dot(ISO_TOWARD), f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{max(radius / scale, 0.4):.2f}" '
                                                  f'fill="{_hex(color)}" stroke="#202020" stroke-width="0.1"/>'))
    items.sort(key=lambda t: t[0])
    width = (x1 - x0) / scale + 2 * margin
    height = (y1 - y0) / scale + 2 * margin
    body = "\n".join(e for _, e in items)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}mm" height="{height:.1f}mm" '
           f'viewBox="0 0 {width:.1f} {height:.1f}">\n{body}\n</svg>')
    return svg, width, height, scale


class Installation3D(model._Proxy):
    Type = T_MODEL3D

    def __init__(self, obj):
        obj.Proxy = self
        g = "Instalação 3D"
        obj.addProperty("App::PropertyLink", "Schedule", g, "Quadro de cargas (calcula depois dos circuitos)")
        obj.setEditorMode("Schedule", 2)
        obj.addProperty("App::PropertyColorList", "FaceColors", g, "Cor de cada face (por circuito)")
        obj.setEditorMode("FaceColors", 2)
        obj.addProperty("App::PropertyLink", "View", g, "Vista isométrica na folha")
        obj.addProperty("App::PropertyString", "IsoScale", g, "Escala da vista isométrica")
        obj.setEditorMode("IsoScale", 1)
        self._visible(obj)

    @staticmethod
    def _visible(obj):
        if "VisibleCircuits" not in obj.PropertiesList:
            obj.addProperty("App::PropertyIntegerList", "VisibleCircuits", "Instalação 3D",
                            "Circuitos mostrados (vazio: todos)")
        if "VisibleFloor" not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", "VisibleFloor", "Instalação 3D",
                            "Cota (mm) do único andar mostrado, no passeio (vazio: todos)")
        if "ShowCeiling" not in obj.PropertiesList:
            obj.addProperty("App::PropertyBool", "ShowCeiling", "Instalação 3D",
                            "Forro translúcido no teto de cada cômodo")

    def onDocumentRestored(self, obj):
        self._visible(obj)
        if "View" not in obj.PropertiesList:
            obj.addProperty("App::PropertyLink", "View", "Instalação 3D", "Vista isométrica na folha")
        if "IsoScale" not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", "IsoScale", "Instalação 3D", "Escala da vista isométrica")
            obj.setEditorMode("IsoScale", 1)

    def execute(self, obj):
        only = list(getattr(obj, "VisibleCircuits", []) or [])
        floor = getattr(obj, "VisibleFloor", "")
        try:
            floor = float(floor) if floor else None
        except ValueError:
            floor = None
        built = _Model(obj.Document, only, floor, bool(getattr(obj, "ShowCeiling", False)))
        built.build()
        shape, colors = build(obj.Document, built)
        obj.Shape = shape
        obj.FaceColors = [tuple(c) + (0.0,) if len(c) == 3 else c for c in colors]
        view = getattr(obj, "View", None)
        if view is None or floor is not None:
            return  # the isometric page shows every floor: leave it while walking on one
        from . import outputs, sheets

        page = next((o for o in view.InList if o.TypeId == "TechDraw::DrawPage"), None)
        size = (page.Template.Width.Value, page.Template.Height.Value) if page is not None else (420.0, 297.0)
        x0, y0, x1, y1 = sheets.drawing_area(size)
        svg, width, height, scale = iso_svg(obj.Document, built, (x1 - x0, y1 - y0))
        obj.IsoScale = f"1:{scale:g}"
        if view.Symbol != svg:
            view.Symbol = svg
        outputs.fit_view(view, width, height)
        if page is not None:
            title = getattr(page, "Label", "").replace("Folha - ", "")
            if only:
                title += " - circuito" + ("s " if len(only) > 1 else " ") + ", ".join(str(n) for n in sorted(only))
            sheets.fill_title_block(page.Template, title, obj.IsoScale)
        view.recompute()
        view.purgeTouched()


def show_circuits(doc, numbers=None):
    """Show only the circuits `numbers` in the 3D installation (and its isometric page); None or
    an empty list shows them all."""
    obj = model3d(doc)
    wanted = sorted({int(n) for n in numbers or []})
    known = {c.Number for c in model.circuits(doc)}
    unknown = [n for n in wanted if n not in known]
    if unknown:
        raise ValueError(f"Circuito(s) inexistente(s): {unknown}")
    if list(obj.VisibleCircuits) != wanted:
        obj.VisibleCircuits = wanted
    obj.touch()
    doc.recompute()
    return obj


def model3d(doc, create=True):
    for obj in model.items(doc, T_MODEL3D):
        return obj
    if not create:
        return None
    obj = doc.addObject("Part::FeaturePython", NAME)
    Installation3D(obj)
    obj.Label = "Instalação elétrica 3D"
    obj.Schedule = model.schedule(doc)
    model._vp(obj, "ViewProvider3D")
    return obj
