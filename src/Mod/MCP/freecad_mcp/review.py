# SPDX-License-Identifier: LGPL-2.1-or-later

"""Review comments: "X" balloons a person pins on the drawing for the assistant to fix.

A comment is an App::FeaturePython object saved with the document. It records the point
that was clicked, the object under it, the text, its status (Aberto, Corrigido, Reaberto)
and a history of every change. In the 3D view it is a coloured dot with "X<n>: text", the
same size on screen at any zoom; it has no shape, so it never goes into sheets or exports.

This module must stay importable without the GUI: documents store the proxy classes by
module and class name and load them back in FreeCADCmd as well.
"""

import datetime
import getpass

import FreeCAD as App

TYPE = "ReviewComment"
GROUP_NAME = "Revisao"
OPEN, FIXED, REOPENED = "Aberto", "Corrigido", "Reaberto"
STATUSES = [OPEN, FIXED, REOPENED]
COLORS = {OPEN: (0.85, 0.10, 0.10), FIXED: (0.10, 0.60, 0.20), REOPENED: (0.95, 0.55, 0.00)}


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def default_author():
    try:
        return getpass.getuser()
    except Exception:
        return "usuário"


def is_comment(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", None) == TYPE


class ReviewComment:
    """Proxy of a review comment."""

    def __init__(self, obj):
        self.Type = TYPE
        obj.Proxy = self
        group = "Comentário"
        obj.addProperty("App::PropertyInteger", "Number", group, "Número do comentário (X1, X2...)")
        obj.addProperty("App::PropertyString", "Text", group, "O que deve ser corrigido")
        obj.addProperty("App::PropertyEnumeration", "Status", group, "Situação do comentário")
        obj.Status = STATUSES
        obj.addProperty("App::PropertyVector", "Position", group, "Ponto marcado na planta")
        obj.addProperty("App::PropertyLinkGlobal", "Target", group, "Objeto em que se clicou")
        obj.addProperty("App::PropertyString", "Author", group, "Quem fez o comentário")
        obj.addProperty("App::PropertyStringList", "History", group, "Histórico de mudanças")
        obj.addProperty("App::PropertyLength", "Radius", group,
                        "Raio do entorno considerado em volta do ponto")
        obj.addProperty("App::PropertyStringList", "Surroundings", group,
                        "O que havia em volta do ponto quando o comentário foi feito")

    def execute(self, obj):
        pass

    def onDocumentRestored(self, obj):
        self.Type = TYPE

    def dumps(self):
        return None

    def loads(self, state):
        self.Type = TYPE

    __getstate__ = dumps

    def __setstate__(self, state):
        self.loads(state)


def comment_group(doc):
    grp = doc.getObject(GROUP_NAME)
    if grp is None:
        grp = doc.addObject("App::DocumentObjectGroup", GROUP_NAME)
        grp.Label = "Revisão"
    return grp


def comments(doc):
    return sorted((o for o in doc.Objects if is_comment(o)), key=lambda o: o.Number)


def find_comment(doc, number):
    for obj in comments(doc):
        if obj.Number == int(number):
            return obj
    return None


def meaningful_target(obj):
    """The object a click refers to: a whole symbol rather than one line of it."""
    if obj is None or is_comment(obj):
        return None
    parent = obj.getParentGroup() if hasattr(obj, "getParentGroup") else None
    if parent is not None and parent.Name.startswith("Simbolo"):
        return parent
    return obj


def _anchor(obj):
    """A point for objects without a shape (Draft texts, dimensions)."""
    for prop in ("Position", "Placement"):
        value = getattr(obj, prop, None)
        if isinstance(value, App.Vector):
            return value
        if isinstance(value, App.Placement):
            return value.Base
    return None


MANY_SUBSHAPES = 64  # above this, measure only the pieces whose bounding box is near the point
_pieces_cache = {}  # (document, object) -> (shape hash, pieces, their bounds) of large shapes


def _box_distance(bounds, point):
    """Distance from `point` to a box (xmin, xmax, ymin, ymax, zmin, zmax); 0 inside it."""
    xmin, xmax, ymin, ymax, zmin, zmax = bounds
    dx = max(xmin - point.x, 0.0, point.x - xmax)
    dy = max(ymin - point.y, 0.0, point.y - ymax)
    dz = max(zmin - point.z, 0.0, point.z - zmax)
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _bounds(box):
    return (box.XMin, box.XMax, box.YMin, box.YMax, box.ZMin, box.ZMax)


def _pieces(obj, shape):
    """Faces (or edges, or vertices) of a large shape with their bounds, or None for a small one.

    Listing the pieces of a big compound (an imported PDF layer has thousands of strokes) is
    the slow part, so it is kept until the shape changes."""
    key = (obj.Document.Name, obj.Name)
    try:
        code = shape.hashCode()
    except Exception:
        code = None
    cached = _pieces_cache.get(key)
    if cached is not None and code is not None and cached[0] == code:
        return cached[1]
    pieces = shape.Faces or shape.Edges or shape.Vertexes
    entry = [(piece, _bounds(piece.BoundBox)) for piece in pieces] if len(pieces) > MANY_SUBSHAPES else None
    if code is not None:
        _pieces_cache[key] = (code, entry)
    return entry


def _shape_distance(obj, shape, point, vertex, radius):
    """Distance from `point` to `shape`, or None when it is surely farther than `radius`."""
    if _box_distance(_bounds(shape.BoundBox), point) > radius:
        return None
    pieces = _pieces(obj, shape)
    if pieces is None:
        return shape.distToShape(vertex)[0]
    near = [piece for piece, bounds in pieces if _box_distance(bounds, point) <= radius]
    return min((piece.distToShape(vertex)[0] for piece in near), default=None)


def surroundings(doc, point, radius, limit=25, progress=None):
    """Objects within `radius` of `point`, nearest first: [(distance, object)].

    Lines of a symbol count as the whole symbol; containers (layers, groups, levels) are
    skipped, their members are listed instead. `progress(done, total)`, if given, is called
    as the objects are examined (to keep a progress bar moving).
    """
    import Part

    vertex = Part.Vertex(point)
    best = {}
    objects = doc.Objects
    for index, obj in enumerate(objects):
        if progress is not None and index % 20 == 0:
            progress(index, len(objects))
        if is_comment(obj) or obj.TypeId in ("App::DocumentObjectGroup", "App::LinkGroup") \
                or obj.TypeId.startswith("TechDraw::") \
                or getattr(getattr(obj, "Proxy", None), "Type", None) in ("Layer", "LayerContainer", "BuildingPart"):
            continue
        shape = getattr(obj, "Shape", None)
        if shape is not None and not shape.isNull():
            try:
                dist = _shape_distance(obj, shape, point, vertex, radius)
            except Exception:
                continue
            if dist is None:
                continue
        else:
            anchor = _anchor(obj)
            if anchor is None:
                continue
            dist = (anchor - point).Length
        if dist > radius:
            continue
        key = meaningful_target(obj)
        if key is None:
            continue
        if key.Name not in best or dist < best[key.Name][0]:
            best[key.Name] = (dist, key)
    return sorted(best.values(), key=lambda pair: pair[0])[:limit]


def _describe_near(pair):
    dist, obj = pair
    if obj.Name.startswith("Simbolo"):
        kind = "Símbolo"  # an electrical symbol or conduit, described by its label
    else:
        kind = getattr(getattr(obj, "Proxy", None), "Type", None) or obj.TypeId.split("::")[-1]
    return {"name": obj.Name, "label": obj.Label, "type": kind, "distance_mm": round(dist, 1)}


def _label(obj):
    short = obj.Text if len(obj.Text) <= 40 else obj.Text[:37] + "..."
    return f"X{obj.Number} - {short}"


def make_comment(doc, position, text, target=None, author=None, radius=1000.0, progress=None):
    """Create comment X<n> at `position` (a Vector) about `target`, recording what is within
    `radius` mm around it (`progress` as in surroundings)."""
    number = max((c.Number for c in comments(doc)), default=0) + 1
    obj = doc.addObject("App::FeaturePython", "Comentario")
    ReviewComment(obj)
    obj.Number = number
    obj.Text = text
    obj.Status = OPEN
    obj.Position = position
    obj.Target = meaningful_target(target)
    obj.Author = author or default_author()
    obj.History = [f"{_now()} {obj.Author}: aberto - {text}"]
    obj.Radius = radius
    obj.Surroundings = [f"{n['label']} ({n['type']}) a {n['distance_mm']:.0f} mm"
                        for n in map(_describe_near, surroundings(doc, position, radius, progress=progress))]
    obj.Label = _label(obj)
    if App.GuiUp:
        from .review_gui import ViewProviderReviewComment

        ViewProviderReviewComment(obj.ViewObject)
    comment_group(doc).addObject(obj)
    return obj


def set_status(obj, status, note="", who=None):
    if status not in STATUSES:
        raise ValueError(f"Status must be one of {STATUSES}")
    obj.Status = status
    entry = f"{_now()} {who or default_author()}: {status.lower()}"
    if note:
        entry += f" - {note}"
    obj.History = list(obj.History) + [entry]


def set_text(obj, text, who=None):
    obj.Text = text
    obj.Label = _label(obj)
    obj.History = list(obj.History) + [f"{_now()} {who or default_author()}: texto alterado - {text}"]


def describe_comment(obj, radius=None):
    """The comment, the object under the click, what was around it then and what is around now."""
    radius = float(radius) if radius else (obj.Radius.Value or 1000.0)
    info = {"number": obj.Number, "name": obj.Name, "text": obj.Text, "status": obj.Status,
            "position": [round(obj.Position.x, 1), round(obj.Position.y, 1), round(obj.Position.z, 1)],
            "author": obj.Author, "history": list(obj.History), "target": None,
            "radius_mm": radius,
            "surroundings_when_created": list(obj.Surroundings),
            "surroundings_now": [_describe_near(p) for p in surroundings(obj.Document, obj.Position, radius)]}
    target = obj.Target
    if target is not None:
        info["target"] = {"name": target.Name, "label": target.Label, "type": target.TypeId}
    return info
