# SPDX-License-Identifier: LGPL-2.1-or-later

"""Turns symbol primitives into shapes, and into SVG icons.

Texts become faces too (outlines of the osifont characters, the technical font that ships
with TechDraw), so they print at their real size on sheets and need no separate objects.
"""

import math
import os

import FreeCAD as App
import Part

from .symbols import TEXT_HEIGHT

FONT = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Resources", "fonts", "osifont-lgpl3fe.ttf")


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


def text_shape(text, height, pos, align="left"):
    """Faces of `text`, `height` mm high, baseline at `pos`, aligned left/center/right. Always upright."""
    if not text or not os.path.isfile(FONT):
        return None
    faces = []
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
    if not faces:
        return None
    shape = Part.makeCompound(faces)
    box = shape.BoundBox
    dx = {"left": -box.XMin, "center": -(box.XMin + box.XMax) / 2, "right": -box.XMax}[align]
    shape.translate(App.Vector(pos.x + dx, pos.y, pos.z))
    return shape


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


def build_shape(prims, frame):
    """Compound of the primitives placed with `frame`. Filled parts are faces, the rest edges."""
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
        elif kind in ("text", "label"):
            if kind == "text":
                pos, align, text = frame.pt(prim[1]), prim[3], prim[2]
            else:
                pos, align = _label_position(frame, prim[1], prim[3], prim[4])
                text = prim[2]
            shape = text_shape(text, TEXT_HEIGHT * frame.scale, pos, align)
            if shape is not None:
                shapes.append(shape)
        else:
            raise ValueError(f"Unknown primitive {kind!r}")
    return Part.makeCompound(shapes)


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
