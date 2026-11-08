# SPDX-License-Identifier: LGPL-2.1-or-later

"""Turns symbol primitives into shapes, and into SVG icons.

Texts become faces (outlines of the osifont characters, the technical font that ships with
TechDraw), so they print at their real size on sheets and need no separate objects. They are
kept apart from the lines of the symbol: drawn with the outline of the lines around each glyph,
small text looks bold, so the 3D view and the sheets fill them without an outline.

Blocks of texts (power and circuit of an item) can sit in several places around their symbol;
the layout module picks the place that overlaps the least.
"""

import math
import os

import FreeCAD as App
import Part

from .symbols import TEXT_HEIGHT

FONT = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Resources", "fonts", "osifont-lgpl3fe.ttf")

LINE_PITCH = 2.8  # paper mm between the lines of a block of text
GAP = 0.8         # paper mm between a symbol and its texts


class Frame:
    """Paper coordinates of a symbol -> model coordinates (mm)."""

    def __init__(self, origin, rotation, scale):
        self.origin = origin
        self.rotation = rotation
        self.rot = App.Rotation(App.Vector(0, 0, 1), rotation)
        self.scale = scale

    def pt(self, xy):
        return self.origin + self.rot.multVec(App.Vector(xy[0] * self.scale, xy[1] * self.scale, 0))

    @property
    def direction(self):
        return self.rot.multVec(App.Vector(1, 0, 0))


# --- texts --------------------------------------------------------------------------------------

_GLYPHS = {}


def _glyphs(text, height):
    """Faces of `text` at the origin (baseline y = 0), cached."""
    key = (str(text), round(height, 6))
    if key in _GLYPHS:
        return _GLYPHS[key]
    faces = []
    if text and os.path.isfile(FONT):
        deflection = height * 0.01
        for wires in Part.makeWireString(str(text), FONT, height, 0):
            if not wires:
                continue
            # Glyph outlines are splines; thin polygons draw the same and export cleanly to sheets.
            polygons = []
            for wire in wires:
                pts = wire.discretize(Deflection=deflection)
                if len(pts) >= 3:
                    if (pts[0] - pts[-1]).Length > 1e-9:
                        pts.append(pts[0])
                    polygons.append(Part.makePolygon(pts))
            if not polygons:
                continue
            try:
                faces.append(Part.makeFace(polygons, "Part::FaceMakerBullseye"))
            except Exception:
                faces.extend(polygons)  # a glyph that does not close: keep its outline
    shape = Part.makeCompound(faces) if faces else None
    _GLYPHS[key] = shape
    return shape


def text_extent(text, height):
    """(xmin, xmax, ymin, ymax) of `text` drawn at the origin; zeros when it draws nothing."""
    shape = _glyphs(text, height)
    if shape is None:
        return 0.0, 0.0, 0.0, 0.0
    b = shape.BoundBox
    return b.XMin, b.XMax, b.YMin, b.YMax


def text_width(text, height):
    xmin, xmax, _, _ = text_extent(text, height)
    return xmax - xmin


def _align_dx(text, height, align):
    xmin, xmax, _, _ = text_extent(text, height)
    return {"left": -xmin, "center": -(xmin + xmax) / 2, "right": -xmax}[align]


def text_shape(text, height, pos, align="left"):
    """Faces of `text`, `height` mm high, baseline at `pos`, aligned left/center/right. Always upright."""
    shape = _glyphs(text, height)
    if shape is None:
        return None
    shape = shape.copy()
    shape.translate(App.Vector(pos.x + _align_dx(text, height, align), pos.y, pos.z))
    return shape


def text_box(text, height, pos, align="left"):
    """Page box (x0, y0, x1, y1) of a text placed like text_shape."""
    xmin, xmax, ymin, ymax = text_extent(text, height)
    dx = pos.x + _align_dx(text, height, align)
    return xmin + dx, pos.y + min(ymin, 0.0), xmax + dx, pos.y + max(ymax, height * 0.7)


def _label_position(frame, xy, line, mode):
    """Text placed in page directions around a rotated symbol (see symbols.py)."""
    d = frame.direction
    horizontal = abs(d.x) >= 0.5
    if mode == "tip":
        if horizontal:  # past the tip, on the room side
            align = "left" if d.x > 0 else "right"
            off = (0.6 if d.x > 0 else -0.6, 0.6 - 2.8 * line)
        else:  # right of the tip, going down into the room
            align = "left"
            off = (1.8, (0.4 if d.y > 0 else -1.0) - 2.8 * line)
    else:  # "side" of a box
        if horizontal:
            align, off = "center", (0, 1.8 if line == 0 else -3.8)
        else:
            align, off = "left", (2.0, 0.3 - 2.8 * line)
    pos = frame.pt(xy) + App.Vector(off[0] * frame.scale, off[1] * frame.scale, 0)
    return pos, align


def _fixed_texts(prims, frame):
    """[(text, pos, align)] of the texts that stay where the symbol puts them."""
    out = []
    th = TEXT_HEIGHT * frame.scale
    for prim in prims:
        if prim[0] == "side" and prim[2]:
            # ("side", (x, 0), text, clearance, sign): upright text beside a line along +X of the
            # frame, on its left (sign 1) or right (sign -1), `clearance` paper mm away.
            _, (x, _), text, clearance, sign = prim
            n = frame.rot.multVec(App.Vector(0, sign, 0))
            w = text_width(text, th)
            ext = abs(n.x) * w / 2 + abs(n.y) * th * 0.6
            c = frame.pt((x, 0)) + n * (clearance * frame.scale + ext)
            out.append((text, App.Vector(c.x, c.y - th / 2, c.z), "center"))
        elif prim[0] == "text" and prim[2]:
            out.append((prim[2], frame.pt(prim[1]), prim[3]))
        elif prim[0] == "label" and prim[2]:
            pos, align = _label_position(frame, prim[1], prim[3], prim[4])
            out.append((prim[2], pos, align))
    return out


# --- blocks of texts that can move ---------------------------------------------------------------


def _block(lines, s, center, align, z):
    """[(text, pos, align)] and box of `lines` stacked around `center` (page x, y)."""
    th = TEXT_HEIGHT * s
    w = max(text_width(t, th) for t in lines)
    h = th + (len(lines) - 1) * LINE_PITCH * s
    cx, cy = center
    x = {"left": cx - w / 2, "right": cx + w / 2, "center": cx}[align]
    texts = [(t, App.Vector(x, cy + h / 2 - th - i * LINE_PITCH * s, z), align) for i, t in enumerate(lines)]
    return texts, (cx - w / 2, cy - h / 2 - 0.25 * th, cx + w / 2, cy + h / 2)


def block_candidates(frame, prim):
    """Places a block of texts can take around its symbol, the usual one first: [(texts, box)].
    ("block", tip, lines, "tip"): wall symbol whose tip is at `tip`;
    ("block", centre, lines, "round", r): round symbol of radius r."""
    _, anchor, lines, mode = prim[:4]
    lines = [t for t in lines if t]
    if not lines:
        return []
    s = frame.scale
    th = TEXT_HEIGHT * s
    w = max(text_width(t, th) for t in lines)
    h = th + (len(lines) - 1) * LINE_PITCH * s
    z = frame.origin.z
    out = []

    def add(c, align):
        out.append(_block(lines, s, (c.x, c.y), align, z))

    def toward(vx):
        return "left" if vx > 0.3 else "right" if vx < -0.3 else "center"

    if mode == "tip":
        d = frame.direction
        n = App.Vector(-d.y, d.x, 0)
        tip, wall = frame.pt(anchor), frame.pt((0, 0))
        ext_d = abs(d.x) * w / 2 + abs(d.y) * h / 2
        ext_n = abs(n.x) * w / 2 + abs(n.y) * h / 2
        add(tip + d * (GAP * s + ext_d), toward(d.x))  # past the tip, into the room
        for sign in (1, -1):  # beside the symbol, off the wall
            side = n * sign
            add(wall + d * (0.8 * s + ext_d) + side * (1.8 * s + ext_n), toward(side.x) if abs(side.x) > 0.3
                else toward(d.x))
        for sign in (1, -1):  # past the tip, to one side
            add(tip + d * (GAP * s + ext_d) + n * sign * (1.6 * s + ext_n), toward(d.x))
    else:
        r = prim[4] * s
        p = frame.pt(anchor)
        dx, dy = r + GAP * s + w / 2, r * 0.3 + h / 2
        for sx in (1, -1):  # below, to the right / left
            add(p + App.Vector(sx * dx, -dy, 0), "left" if sx > 0 else "right")
        add(p + App.Vector(0, -(r + GAP * s + h / 2), 0), "center")
        add(p + App.Vector(0, r + GAP * s + h / 2, 0), "center")
        for sx in (-1, 1):  # above
            add(p + App.Vector(sx * dx, dy, 0), "left" if sx > 0 else "right")
    return out


# --- shapes -------------------------------------------------------------------------------------


def geometry_box(prims, frame):
    """Page box (x0, y0, x1, y1) of the lines and areas of a symbol (texts left out), or None."""
    pts = []
    s = frame.scale
    for prim in prims:
        if prim[0] == "line":
            pts += [frame.pt(prim[1]), frame.pt(prim[2])]
        elif prim[0] == "poly":
            pts += [frame.pt(q) for q in prim[1]]
        elif prim[0] == "circle":
            c, r = frame.pt(prim[1]), prim[2] * s
            pts += [c + App.Vector(-r, -r, 0), c + App.Vector(r, r, 0)]
    if not pts:
        return None
    return min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts)


def layout(prims, frame):
    """What a symbol puts on the page: (geometry box, boxes of its fixed texts, candidate places
    of its block of texts [(texts, box)] or [])."""
    th = TEXT_HEIGHT * frame.scale
    fixed = [text_box(t, th, pos, align) for t, pos, align in _fixed_texts(prims, frame)]
    blocks = [block_candidates(frame, prim) for prim in prims if prim[0] == "block"]
    blocks = [b for b in blocks if b]
    return geometry_box(prims, frame), fixed, (blocks[0] if blocks else [])


def build_shape(prims, frame, slot=0):
    """(geometry, labels): compound of the lines and areas placed with `frame`, and compound of
    the texts as faces (None without texts). A block of texts takes its candidate place `slot`."""
    shapes = []
    for prim in prims:
        kind = prim[0]
        if kind == "line":
            a, b = frame.pt(prim[1]), frame.pt(prim[2])
            if (b - a).Length > 1e-9:
                shapes.append(Part.LineSegment(a, b).toShape())
        elif kind == "poly":
            pts = [frame.pt(q) for q in prim[1]]
            closed, filled = prim[2], prim[3]
            if closed:
                pts.append(pts[0])
            wire = Part.makePolygon(pts)
            shapes.append(Part.Face(wire) if filled and closed else wire)
        elif kind == "circle":
            circle = Part.makeCircle(prim[2] * frame.scale, frame.pt(prim[1]))
            shapes.append(Part.Face(Part.Wire(circle)) if prim[3] else circle)
        elif kind not in ("text", "label", "block", "side"):
            raise ValueError(f"Unknown primitive {kind!r}")
    texts = _fixed_texts(prims, frame)
    for prim in prims:
        if prim[0] == "block":
            cands = block_candidates(frame, prim)
            if cands:
                texts += cands[min(max(int(slot), 0), len(cands) - 1)][0]
    th = TEXT_HEIGHT * frame.scale
    labels = [shape for shape in (text_shape(t, th, pos, align) for t, pos, align in texts) if shape is not None]
    return Part.makeCompound(shapes), (Part.makeCompound(labels) if labels else None)


def svg_icon(prims, size=64):
    """A 64 px SVG of the symbol's lines and fills (texts left out), for menus and toolbars."""
    pts = []
    for prim in prims:
        if prim[0] == "line":
            pts += [prim[1], prim[2]]
        elif prim[0] == "poly":
            pts += list(prim[1])
        elif prim[0] == "circle":
            (cx, cy), r = prim[1], prim[2]
            pts += [(cx - r, cy - r), (cx + r, cy + r)]
    if not pts:
        return None
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    span = max(x1 - x0, y1 - y0, 1e-6)
    k = (size - 8) / span

    def sx(x):
        return 4 + (x - x0) * k + (size - 8 - (x1 - x0) * k) / 2

    def sy(y):
        return size - (4 + (y - y0) * k + (size - 8 - (y1 - y0) * k) / 2)

    stroke = max(1.5, 0.06 * k)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">']
    for prim in prims:
        if prim[0] == "line":
            (ax, ay), (bx, by) = prim[1], prim[2]
            out.append(f'<line x1="{sx(ax):.2f}" y1="{sy(ay):.2f}" x2="{sx(bx):.2f}" y2="{sy(by):.2f}" '
                       f'stroke="black" stroke-width="{stroke:.2f}"/>')
        elif prim[0] == "poly":
            d = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in prim[1])
            tag = "polygon" if prim[2] else "polyline"
            fill = "black" if prim[3] else "none"
            out.append(f'<{tag} points="{d}" fill="{fill}" stroke="black" stroke-width="{stroke:.2f}"/>')
        elif prim[0] == "circle":
            (cx, cy), r = prim[1], prim[2]
            fill = "black" if prim[3] else "none"
            out.append(f'<circle cx="{sx(cx):.2f}" cy="{sy(cy):.2f}" r="{r * k:.2f}" fill="{fill}" '
                       f'stroke="black" stroke-width="{stroke:.2f}"/>')
    out.append("</svg>")
    return "\n".join(out)


def angle_of(vector):
    return math.degrees(math.atan2(vector.y, vector.x))
