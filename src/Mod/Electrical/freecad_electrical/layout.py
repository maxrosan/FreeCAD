# SPDX-License-Identifier: LGPL-2.1-or-later

"""Where the movable texts of a plan go, so they overlap as little as possible.

Each item has a block of texts (power, circuit) that can sit in a few places around its symbol,
and each conduit has its conductor marks, which can slide along it. Floor by floor, the items
are placed first, avoiding the symbols, the conduits and the texts already placed; then the
conduit marks, avoiding the symbols, the other conduits, the item texts and the marks already
placed. A place that overlaps nothing wins; otherwise the one that overlaps the least.

The result is cached per document and recomputed when the items, conduits or circuits change.
"""

import FreeCAD as App

from . import geometry, model

# Places tried for the conductor marks on each straight piece of a conduit (fraction of the
# piece), best first. The longest pieces are tried first.
ALONG_PIECE = (0.5, 0.35, 0.65, 0.25, 0.75)


def fractions(points):
    """Places tried for the marks of a conduit, as fractions of its whole length."""
    lengths = [(b - a).Length for a, b in zip(points, points[1:])]
    total = sum(lengths)
    if total <= 0:
        return [0.5]
    starts = [sum(lengths[:i]) for i in range(len(lengths))]
    order = sorted(range(len(lengths)), key=lambda i: -lengths[i])
    out = []
    for t in ALONG_PIECE:
        for i in order:
            if lengths[i] > 0:
                out.append((starts[i] + t * lengths[i]) / total)
    return out

_CACHE = {}


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _crosses(box, a, b):
    """Does the segment a-b pass through the box? (Liang-Barsky clipping.)"""
    x0, y0, x1, y1 = box
    dx, dy = b.x - a.x, b.y - a.y
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a.x - x0), (dx, x1 - a.x), (-dy, a.y - y0), (dy, y1 - a.y)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _score(box, symbols, texts, segments, own=None):
    """How badly a text box overlaps: symbols and texts by the share of the box they cover,
    conduits by the number that cross it."""
    pad = 0.15 * (box[3] - box[1])  # keep a little air around texts
    box = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
    area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-9)
    score = sum(_overlap(box, g) for g in symbols) / area
    score += 2.0 * sum(_overlap(box, t) for t in texts) / area
    score += sum(1.0 for name, a, b in segments if name != own and _crosses(box, a, b))
    return score


def _room_at_ends(points, fraction, half):
    """Penalty for marks that do not fit on their straight piece of conduit (they would run past
    a bend or onto the items at the ends)."""
    lengths = [(b - a).Length for a, b in zip(points, points[1:])]
    target = sum(lengths) * fraction
    for seg in lengths:
        if target <= seg:
            room = min(target, seg - target)
            return 0.0 if room >= half else 3.0 * (half - room) / max(half, 1e-9)
        target -= seg
    return 3.0


ARCH_LINES = ("Wall", "Structure")  # BIM objects whose outlines the texts keep off
ROOM_TEXT = 2.5  # paper mm of the room names on the sheets (DrawViewDraft FontSize / 2)
ROOM_LINES = 1.5  # line pitch of the room names on the sheets, in text heights (sheets.py)
MOVED = "ElectricalTextPosition"  # where the layout put a room name (it leaves it if moved by hand)


def _kind(obj):
    try:
        from draftutils.utils import get_type

        return get_type(obj)
    except Exception:
        return None


def _outline(obj):
    """Plan lines of a wall: the edges of its shape at its lowest level."""
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return []
    z = shape.BoundBox.ZMin
    out = []
    for e in shape.Edges:
        a, b = e.Vertexes[0].Point, e.Vertexes[-1].Point
        if abs(a.z - z) < 1 and abs(b.z - z) < 1 and (b - a).Length > 1:
            out.append((a, b))
    return out


class Layout:
    def __init__(self, doc):
        self.slots = {}  # item name -> place of its block of texts
        self.marks = {}  # conduit name -> fraction of its length where the marks go
        self.rooms = {}  # space name -> where its name goes (None: the middle of the room)
        self.used = set()  # items and conduits drawn with this layout
        floors = [z for z, _ in model.floors(doc)] or [0.0]

        def floor_of(z):
            found = floors[0]
            for f in floors:
                if z >= f - model.FLOOR_TOLERANCE:
                    found = f
            return found

        groups = {}

        def group(z):
            return groups.setdefault(floor_of(z), {"points": [], "conduits": [], "walls": [], "spaces": []})

        for p in model.points(doc):
            group(p.Position.z)["points"].append(p)
        for c in model.conduits(doc):
            if len(c.Points) >= 2:
                group(c.Points[0].z)["conduits"].append(c)
        for o in doc.Objects:
            kind = _kind(o)
            if kind in ARCH_LINES or kind == "Space":
                shape = getattr(o, "Shape", None)
                if shape is None or shape.isNull():
                    continue
                group(shape.BoundBox.ZMin)["walls" if kind in ARCH_LINES else "spaces"].append(o)
        scale = model.scale_of(doc)
        for g in groups.values():
            self._floor(g, scale)

    def _floor(self, g, scale):
        pts, cons = g["points"], g["conduits"]
        segments = [(c.Name, a, b) for c in cons for a, b in zip(c.Points, c.Points[1:])]
        segments += [("~", a, b) for w in g["walls"] for a, b in _outline(w)]
        symbols, texts, blocks = [], [], []
        for p in sorted(pts, key=lambda o: o.Name):
            prims, frame = model.point_parts(p)
            box, fixed, candidates = geometry.layout(prims, frame)
            if box is not None:
                symbols.append(box)
            texts += fixed
            if candidates:
                blocks.append((p.Name, candidates))
        for name, candidates in blocks:
            scores = [_score(box, symbols, texts, segments) + 0.01 * i for i, (_, box) in enumerate(candidates)]
            best = scores.index(min(scores))
            self.slots[name] = best
            texts.append(candidates[best][1])
        for c in sorted(cons, key=lambda o: o.Name):
            tried = fractions(list(c.Points)) if getattr(c, "AutoMarks", False) else [c.MarksAt]
            best = None
            options = [(t, flip) for t in tried for flip in (False, True)]
            for i, (t, flip) in enumerate(options):
                prims, frame, _, half = model.conduit_marks(c, t, flip)
                if not prims:
                    break
                ticks, fixed, _ = geometry.layout(prims, frame)
                score = sum(_score(b, symbols, texts, segments, c.Name) for b in fixed)
                if ticks is not None:
                    score += _score(ticks, symbols, texts, [])
                score += _room_at_ends(list(c.Points), t, half) + 0.005 * i
                if best is None or score < best[0]:
                    best = (score, (t, flip), fixed)
                if score < 0.005 * (i + 1):
                    break  # nothing in the way
            if best is not None:
                self.marks[c.Name] = best[1]
                texts += best[2]
        for space in g["spaces"]:
            self._room_name(space, scale, symbols, texts, segments)

    def _room_name(self, space, scale, symbols, texts, segments):
        """Move the name of a room off the electrical items, when it sits on one."""
        vobj = getattr(space, "ViewObject", None)
        proxy = getattr(vobj, "Proxy", None)
        if vobj is None or not hasattr(vobj, "TextPosition") or not hasattr(proxy, "text1"):
            return
        first = [t for t in proxy.text1.string.getValues() if t]
        others = [t for t in proxy.text2.string.getValues() if t]
        if not first and not others:
            return
        h = ROOM_TEXT * scale
        k = vobj.FirstLine.Value / vobj.FontSize.Value if vobj.FontSize.Value else 1.0
        pitch = ROOM_LINES * h
        w = max([geometry.text_width(t, h * k) for t in first] + [geometry.text_width(t, h) for t in others]) * 1.1
        centre = space.Shape.CenterOfMass
        z = space.Shape.BoundBox.ZMin

        def box(x, y):  # the first line sits above (x, y), the others from (x, y) down
            top = y + (pitch * k + h * k if first else h)
            return (x - w / 2, y - max(len(others) - 1, 0) * pitch - 0.3 * h, x + w / 2, top)

        step = 1.6 * h
        side = w / 2 + 2 * h
        offsets = [(dx, dy * step) for dy in (0, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6) for dx in (0, side, -side)]
        offsets.sort(key=lambda o: abs(o[0]) / side * 2.5 + abs(o[1]) / step)
        room = space.Shape.BoundBox
        best = None
        for i, (dx, dy) in enumerate(offsets):
            b = box(centre.x + dx, centre.y + dy)
            if b[0] < room.XMin or b[2] > room.XMax or b[1] < room.YMin or b[3] > room.YMax:
                continue
            score = _score(b, symbols, texts, segments) + 0.01 * i
            if best is None or score < best[0]:
                best = (score, i, App.Vector(centre.x + dx, centre.y + dy, z))
        if best is None:
            return
        place = None if best[1] == 0 else best[2]
        self.rooms[space.Name] = place
        _place_room_name(space, place)
        x, y = (centre.x, centre.y) if place is None else (place.x, place.y)
        texts.append(box(x, y))


def _place_room_name(space, place):
    """Set the room name position, unless someone moved it by hand since the layout did."""
    vobj = space.ViewObject
    current = vobj.TextPosition
    ours = getattr(space, MOVED, None)
    if current.Length > 1e-9 and (ours is None or (current - ours).Length > 1e-6):
        return  # placed by hand
    target = App.Vector() if place is None else space.Placement.inverse().multVec(place)
    if (current - target).Length > 1e-6:
        vobj.TextPosition = target
    if MOVED not in space.PropertiesList:
        space.addProperty("App::PropertyVector", MOVED, "Elétrica", "Posição do nome dada pela instalação elétrica")
        space.setEditorMode(MOVED, 2)
    if (getattr(space, MOVED) - target).Length > 1e-6:
        setattr(space, MOVED, target)


def _signature(doc):
    sig = [model.scale_of(doc)]
    for p in model.points(doc):
        prims, frame = model.point_parts(p)
        sig.append((p.Name, repr(prims), tuple(p.Position), round(p.Rotation.Value, 6), frame.scale))
    conduits = model.conduits(doc)
    if any(getattr(c, "Automatic", False) for c in conduits):
        from . import network

        sig.append(id(network.network(doc)))
    for c in conduits:
        sig.append((c.Name, tuple(tuple(v) for v in c.Points), c.Installation, c.Automatic,
                    getattr(c, "AutoMarks", False), c.MarksAt, tuple(x.Name for x in c.Circuits if x is not None),
                    tuple(c.ExtraConductors), c.Diameter, c.Scale))
    for c in model.circuits(doc):
        sig.append((c.Name, c.Number, c.Section, c.SectionSuggested, c.Connection))
    for o in doc.Objects:
        if _kind(o) in ARCH_LINES or _kind(o) == "Space":
            shape = getattr(o, "Shape", None)
            if shape is not None and not shape.isNull():
                sig.append((o.Name, tuple(round(v, 3) for v in (shape.BoundBox.XMin, shape.BoundBox.YMin,
                                                               shape.BoundBox.XMax, shape.BoundBox.YMax,
                                                               shape.BoundBox.ZMin)), o.Label))
    return repr(sig)


def layout(doc):
    key = (doc.Name, doc.Uid)
    sig = _signature(doc)
    hit = _CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    result = Layout(doc)
    _CACHE[key] = (sig, result)
    return result


def slot_of(item):
    """Place of the block of texts of an item (index of geometry.block_candidates)."""
    try:
        result = layout(item.Document)
        result.used.add(item.Name)
        return result.slots.get(item.Name, 0)
    except Exception as e:
        App.Console.PrintWarning(f"Elétrica: textos sem arranjo ({e})\n")
        return 0


def marks_of(conduit):
    """(fraction of its length, texts on the other side) where the marks of a conduit go."""
    if not getattr(conduit, "AutoMarks", False):
        return conduit.MarksAt, False
    try:
        result = layout(conduit.Document)
        result.used.add(conduit.Name)
        return result.marks.get(conduit.Name, (conduit.MarksAt, False))
    except Exception as e:
        App.Console.PrintWarning(f"Elétrica: marcas sem arranjo ({e})\n")
        return conduit.MarksAt, False


def stale(doc):
    """Items and conduits drawn with an older layout (something they do not depend on moved,
    a wall for instance): they need a recompute."""
    current = layout(doc)
    out = [o for o in model.points(doc) if o.Name not in current.used]
    return out + [c for c in model.conduits(doc) if getattr(c, "AutoMarks", False) and c.Name not in current.used]


def forget(doc=None):
    if doc is None:
        _CACHE.clear()
    else:
        _CACHE.pop((doc.Name, doc.Uid), None)
