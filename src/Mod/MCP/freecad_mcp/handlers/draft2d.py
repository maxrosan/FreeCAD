# SPDX-License-Identifier: LGPL-2.1-or-later

"""2D drawing commands built on the Draft workbench.

All coordinates are real-world sizes in mm (or strings with units) in the
XY plane; the drawing scale only matters for sheets and PDF conversion.
"""

import math

import FreeCAD as App

from .. import live
from ..registry import CommandError, command
from ..units import angle, color, length, point, points
from .common import active_document, describe, get_object, recompute, settings

# Annotation scale used when the document has no drawing scale yet.
DEFAULT_ANNOTATION_SCALE = 50.0


def _draft():
    import Draft

    return Draft


def _layer_type(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", None)


def get_or_create_layer(doc, name, line_color=None, line_width=None):
    for obj in doc.Objects:
        if _layer_type(obj) == "Layer" and obj.Label == name:
            break
    else:
        kwargs = {"name": name}
        if line_color is not None:
            kwargs["line_color"] = color(line_color)
        if line_width is not None:
            kwargs["line_width"] = float(line_width)
        obj = _draft().make_layer(**kwargs)
        obj.Label = name
        return obj
    if App.GuiUp and obj.ViewObject:
        if line_color is not None:
            obj.ViewObject.LineColor = color(line_color)
        if line_width is not None:
            obj.ViewObject.LineWidth = float(line_width)
    return obj


def _apply_style(obj, style):
    if not style:
        return
    if not (App.GuiUp and obj.ViewObject):
        return
    vobj = obj.ViewObject
    if "color" in style:
        if hasattr(vobj, "LineColor"):
            vobj.LineColor = color(style["color"])
        if hasattr(vobj, "TextColor"):
            vobj.TextColor = color(style["color"])
    if "line_width" in style and hasattr(vobj, "LineWidth"):
        vobj.LineWidth = float(style["line_width"])
    if "draw_style" in style and hasattr(vobj, "DrawStyle"):
        vobj.DrawStyle = style["draw_style"]
    if "font_size" in style and hasattr(vobj, "FontSize"):
        vobj.FontSize = length(style["font_size"], "font_size")
        if hasattr(vobj, "ScaleMultiplier"):
            vobj.ScaleMultiplier = 1.0  # font_size is a real size


def annotation_scale(doc):
    obj = settings(doc, create=False)
    if obj is not None and obj.ScaleDenominator > 1:
        return obj.ScaleDenominator
    return DEFAULT_ANNOTATION_SCALE


def _scale_annotation(doc, obj, real_height):
    """Draft text and dimension sizes are paper sizes; enlarge them by the drawing scale.

    A text given a real height (mm in the model) keeps it instead.
    """
    if not (App.GuiUp and obj.ViewObject and hasattr(obj.ViewObject, "ScaleMultiplier")):
        return
    if real_height is not None:
        obj.ViewObject.FontSize = real_height
        obj.ViewObject.ScaleMultiplier = 1.0
    else:
        obj.ViewObject.ScaleMultiplier = annotation_scale(doc)


def _finish(doc, obj, layer=None, style=None, label=None, real_height=None):
    if label:
        obj.Label = label
    if layer:
        lay = get_or_create_layer(doc, layer)
        lay.Proxy.addObject(lay, obj)
    _scale_annotation(doc, obj, real_height)
    _apply_style(obj, style)
    return obj


def _xy_rotation(deg):
    return App.Rotation(App.Vector(0, 0, 1), deg)


# Each builder creates one object from JSON parameters; the commands and
# draw_batch share them.


def _line(doc, p1, p2, **_):
    return _draft().make_line(point(p1, "p1"), point(p2, "p2"))


def _polyline(doc, points_, closed=False, filled=False, **_):
    pts = points(points_)
    if len(pts) < 2:
        raise CommandError("A polyline needs at least 2 points")
    return _draft().make_wire(pts, closed=bool(closed), face=bool(filled))


def _rectangle(doc, origin, width, height, rotation=0, filled=False, **_):
    placement = App.Placement(point(origin, "origin"), _xy_rotation(angle(rotation)))
    return _draft().make_rectangle(length(width, "width"), length(height, "height"), placement=placement,
                                   face=bool(filled))


def _circle(doc, center, radius, filled=False, **_):
    placement = App.Placement(point(center, "center"), App.Rotation())
    return _draft().make_circle(length(radius, "radius"), placement=placement, face=bool(filled))


def _arc(doc, center, radius, start_angle, end_angle, **_):
    placement = App.Placement(point(center, "center"), App.Rotation())
    return _draft().make_circle(length(radius, "radius"), placement=placement,
                                startangle=angle(start_angle), endangle=angle(end_angle))


def _arc_3points(doc, p1, p2, p3, **_):
    return _draft().make_arc_3_points([point(p1, "p1"), point(p2, "p2"), point(p3, "p3")])


def _text(doc, position, text, rotation=0, **_):
    # The height is applied by _finish, as a real size.
    placement = App.Placement(point(position, "position"), _xy_rotation(angle(rotation)))
    lines = text if isinstance(text, list) else str(text).split("\n")
    return _draft().make_text(lines, placement=placement)


def _dimension(doc, p1, p2, offset=None, dim_point=None, **_):
    a, b = point(p1, "p1"), point(p2, "p2")
    if dim_point is not None:
        dim_line = point(dim_point, "dim_point")
    else:
        # Offset perpendicular to p1->p2, to the left when looking from p1 to p2.
        d = b - a
        if d.Length == 0:
            raise CommandError("p1 and p2 are the same point")
        normal = App.Vector(-d.y, d.x, 0).normalize()
        mid = (a + b) * 0.5
        dim_line = mid + normal * (length(offset, "offset") if offset is not None else 500.0)
    return _draft().make_linear_dimension(a, b, dim_line)


_BUILDERS = {
    "line": _line,
    "polyline": _polyline,
    "rectangle": _rectangle,
    "circle": _circle,
    "arc": _arc,
    "arc_3points": _arc_3points,
    "text": _text,
    "dimension": _dimension,
}


def _build(doc, kind, params):
    if kind not in _BUILDERS:
        raise CommandError(f"Unknown item type {kind!r}; use one of {sorted(_BUILDERS)}")
    params = dict(params)
    if kind == "polyline":
        params["points_"] = params.pop("points")
    layer = params.pop("layer", None)
    style = params.pop("style", None)
    label = params.pop("label", None)
    height = params.get("height") if kind == "text" else None
    obj = _BUILDERS[kind](doc, **params)
    if obj is None:
        raise CommandError(f"Draft could not create the {kind}")
    return _finish(doc, obj, layer, style, label, length(height, "height") if height is not None else None)


def _single(kind, params):
    doc = active_document()
    obj = _build(doc, kind, params)
    recompute(doc)
    return describe(obj)


@command("draw_line", transaction=True)
def draw_line(p1, p2, layer=None, style=None, label=None):
    return _single("line", locals())


@command("draw_polyline", transaction=True)
def draw_polyline(points, closed=False, filled=False, layer=None, style=None, label=None):
    return _single("polyline", locals())


@command("draw_rectangle", transaction=True)
def draw_rectangle(origin, width, height, rotation=0, filled=False, layer=None, style=None, label=None):
    return _single("rectangle", locals())


@command("draw_circle", transaction=True)
def draw_circle(center, radius, filled=False, layer=None, style=None, label=None):
    return _single("circle", locals())


@command("draw_arc", transaction=True)
def draw_arc(center, radius, start_angle, end_angle, layer=None, style=None, label=None):
    return _single("arc", locals())


@command("draw_arc_3points", transaction=True)
def draw_arc_3points(p1, p2, p3, layer=None, style=None, label=None):
    return _single("arc_3points", locals())


@command("draw_text", transaction=True)
def draw_text(position, text, height=None, rotation=0, layer=None, style=None, label=None):
    return _single("text", locals())


@command("draw_dimension", transaction=True)
def draw_dimension(p1, p2, offset=None, dim_point=None, layer=None, style=None, label=None):
    return _single("dimension", locals())


@command("draw_batch", transaction=True)
def draw_batch(items, layer=None, style=None, compact=True):
    """Create many items in one call and one undo step.

    Each item is ``{"type": "line"|"polyline"|..., <parameters of that type>}``.
    ``layer`` and ``style`` are defaults for items that do not set their own.
    """
    doc = active_document()
    created, errors = [], []
    for i, item in enumerate(items):
        params = dict(item)
        kind = params.pop("type", None)
        if layer and "layer" not in params:
            params["layer"] = layer
        if style and "style" not in params:
            params["style"] = style
        try:
            created.append(_build(doc, kind, params))
        except Exception as e:
            errors.append({"index": i, "type": kind, "message": f"{type(e).__name__}: {e}"})
        live.pulse(f"desenhando ({i + 1} de {len(items)})")
    recompute(doc)
    reply = {"created": len(created), "errors": errors}
    if compact:
        reply["names"] = [o.Name for o in created]
    else:
        reply["objects"] = [describe(o) for o in created]
    return reply


@command("import_reference_geometry", transaction=True)
def import_reference_geometry(segments=(), polylines=(), layer="Reference", label=None, style=None):
    """Draw many segments/polylines as one non-editable compound (fast, for tracing over).

    ``segments`` is a list of [[x1, y1], [x2, y2]]; ``polylines`` a list of point lists.
    """
    import Part

    doc = active_document()
    edges = []
    for seg in segments:
        a, b = point(seg[0]), point(seg[1])
        if (b - a).Length > 1e-6:
            edges.append(Part.LineSegment(a, b).toShape())
    for pl in polylines:
        pts = points(pl)
        for a, b in zip(pts, pts[1:]):
            if (b - a).Length > 1e-6:
                edges.append(Part.LineSegment(a, b).toShape())
    if not edges:
        raise CommandError("No geometry to import")
    obj = doc.addObject("Part::Feature", "Reference")
    obj.Shape = Part.makeCompound(edges)
    _finish(doc, obj, layer, style, label or layer)
    recompute(doc)
    info = describe(obj)
    info["edges"] = len(edges)
    return info


@command("create_layer", transaction=True)
def create_layer(name, color=None, line_width=None):
    doc = active_document()
    layer = get_or_create_layer(doc, name, color, line_width)
    return {"name": layer.Name, "label": layer.Label}


@command("move_to_layer", transaction=True)
def move_to_layer(names, layer):
    doc = active_document(create=False)
    lay = get_or_create_layer(doc, layer)
    for name in names:
        lay.Proxy.addObject(lay, get_object(doc, name))
    return {"layer": lay.Label, "count": len(names)}


@command("set_style", transaction=True)
def set_style(names, style):
    doc = active_document(create=False)
    for name in names:
        _apply_style(get_object(doc, name), style)
    return {"count": len(names)}


@command("transform_objects", transaction=True)
def transform_objects(names, move=None, rotate=0, center=None, scale=None, copy=False):
    """Move, rotate (degrees, around ``center`` in XY) and/or scale objects."""
    Draft = _draft()
    doc = active_document(create=False)
    objs = [get_object(doc, n) for n in names]
    if copy:
        objs = Draft.copy(objs) if len(objs) > 1 else [Draft.copy(objs[0])]
    if rotate:
        c = point(center, "center") if center is not None else App.Vector(0, 0, 0)
        Draft.rotate(objs, angle(rotate), c, App.Vector(0, 0, 1))
    if scale:
        c = point(center, "center") if center is not None else App.Vector(0, 0, 0)
        factor = float(scale)
        Draft.scale(objs, App.Vector(factor, factor, 1), c, copy=False)
    if move:
        Draft.move(objs, point(move, "move"))
    recompute(doc)
    return {"objects": [describe(o) for o in objs]}


@command("measure_distance")
def measure_distance(p1, p2):
    a, b = point(p1, "p1"), point(p2, "p2")
    d = b - a
    return {"distance_mm": round(d.Length, 3), "dx": round(d.x, 3), "dy": round(d.y, 3),
            "angle_deg": round(math.degrees(math.atan2(d.y, d.x)), 4)}
