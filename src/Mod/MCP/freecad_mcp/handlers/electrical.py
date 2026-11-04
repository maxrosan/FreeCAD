# SPDX-License-Identifier: LGPL-2.1-or-later

"""Electrical symbols for building plans (Brazilian NBR 5444 convention) and conduits.

Symbols are defined as data, in paper millimetres, with the insertion point at the
origin. Wall-mounted symbols have the wall at x = 0 and point along +X into the room;
``rotation`` turns them. On insertion every size is multiplied by the drawing scale
(1:N), so a symbol has the same size on every sheet printed at that scale.

NBR 5444 was cancelled by ABNT in 2014 without a replacement, but its symbols are still
the ones used on Brazilian electrical plans.
"""

import math

import FreeCAD as App

from ..registry import CommandError, command
from ..units import angle, length, point
from .common import active_document, get_object, recompute
from .draft2d import _draft, annotation_scale, get_or_create_layer

TEXT = 2.0  # paper height of the texts (mm)
BLACK = (0.0, 0.0, 0.0)

# --- primitives ---------------------------------------------------------------
# ("line", (x1, y1), (x2, y2))
# ("poly", [(x, y), ...], closed, filled)
# ("circle", (cx, cy), r, filled)
# ("text", (x, y), text, align)   align: "left" | "center" | "right"
# ("label", (x, y), text, line, mode)  text placed after rotating the symbol, so it never lands
#     on the wall: mode "tip" puts it past the tip of a wall symbol, on the room side; mode
#     "side" beside a box (above/below when horizontal, to the right when vertical);
#     line 0 is the first line of text, 1 the second.


def _wall(stem):
    """Wall mark (a short thick bar at the wall) and the stem to the symbol."""
    return [("poly", [(-0.5, -1.5), (0, -1.5), (0, 1.5), (-0.5, 1.5)], True, True),
            ("line", (0, 0), (stem, 0))]


def _triangle(x0, filled):
    """Socket triangle, base on x = x0, pointing along +X."""
    tri = [(x0, -1.3), (x0 + 2.4, 0), (x0, 1.3)]
    prims = [("poly", tri, True, filled == "full")]
    if filled == "half":
        prims.append(("poly", [(x0, -1.3), (x0 + 2.4, 0), (x0, 0)], True, True))
    return prims


def _tip_labels(p, tip):
    """Power and circuit of a wall symbol, past its tip."""
    lines = [str(p["potencia"])] if p.get("potencia") else []
    if p.get("circuito"):
        lines.append(f"-{p['circuito']}-")
    if p.get("letra"):
        lines.append(str(p["letra"]))
    return [("label", tip, text, i, "tip") for i, text in enumerate(lines)]


def _socket(filled):
    def build(p):
        return _wall(2.0) + _triangle(2.0, filled) + _tip_labels(p, (4.4, 0))

    return build


def _floor_socket(p):
    prims = [("poly", [(-1.8, -1.8), (1.8, -1.8), (1.8, 1.8), (-1.8, 1.8)], True, False)]
    prims += [("poly", [(-1.0, -1.1), (1.2, 0), (-1.0, 1.1)], True, False)]
    if p.get("potencia"):
        prims.append(("text", (2.2, 1.2), str(p["potencia"]), "left"))
    if p.get("circuito"):
        prims.append(("text", (2.2, -2.0), f"-{p['circuito']}-", "left"))
    return prims


R_SW = 1.2  # switch circle radius


def _switch(kind, sections):
    def build(p):
        letters = list(p.get("letra") or "abc"[:sections])
        if kind == "paralelo":
            prims = [("circle", (0, 0), R_SW, True)]
        elif kind == "intermediario":
            half = [(0, -R_SW)] + [(-R_SW * math.sin(math.radians(a)), -R_SW * math.cos(math.radians(a)))
                                   for a in range(0, 181, 15)]
            prims = [("circle", (0, 0), R_SW, False), ("poly", half, True, True)]
        else:
            prims = [("circle", (0, 0), R_SW, False)]
            if sections == 2:
                prims.append(("line", (0, -R_SW), (0, R_SW)))
            elif sections == 3:
                for a in (90, 210, 330):
                    prims.append(("line", (0, 0), (R_SW * math.cos(math.radians(a)),
                                                   R_SW * math.sin(math.radians(a)))))
        places = [((1.4, 1.4), "left"), ((-1.4, 1.4), "right"), ((0, -3.6), "center")]
        if len(letters) >= 2:
            places = [((-1.4, 1.4), "right"), ((1.4, 1.4), "left"), ((0, -3.6), "center")]
        for letter, (pos, align) in zip(letters, places):
            prims.append(("text", pos, letter, align))
        return prims

    return build


def _light_labels(p, r, dx=0.0):
    prims = []
    if p.get("letra"):
        prims.append(("text", (dx + r * 0.75, r + 0.4), str(p["letra"]), "left"))
    if p.get("circuito"):
        prims.append(("text", (dx - r - 0.6, -r - 2.4), f"-{p['circuito']}-", "right"))
    if p.get("potencia"):
        prims.append(("text", (dx + r * 0.4, -r - 2.4), str(p["potencia"]), "left"))
    return prims


def _ceiling_light(embedded):
    def build(p):
        prims = [("circle", (0, 0), 2.5, False)]
        if embedded:
            prims.append(("circle", (0, 0), 1.8, False))
        return prims + _light_labels(p, 2.5)

    return build


def _wall_light(p):
    return _wall(1.5) + [("circle", (3.5, 0), 2.0, False)] + _tip_labels(p, (5.5, 0))


def _fluorescent(p):
    prims = [("poly", [(-3.5, -0.9), (3.5, -0.9), (3.5, 0.9), (-3.5, 0.9)], True, False),
             ("circle", (0, 0), 0.6, False)]
    return prims + _light_labels(p, 0.9)


def _panel(kind):
    def build(p):
        box = [(-3.5, -1.2), (3.5, -1.2), (3.5, 1.2), (-3.5, 1.2)]
        if kind == "terminal":
            prims = [("poly", box, True, True)]
        else:  # geral: half filled along the diagonal
            prims = [("poly", box, True, False), ("poly", [(-3.5, 1.2), (3.5, -1.2), (-3.5, -1.2)], True, True)]
        prims.append(("label", (0, 0), p.get("letra") or ("QD" if kind == "terminal" else "QG"), 0, "side"))
        if p.get("potencia"):
            prims.append(("label", (0, 0), str(p["potencia"]), 1, "side"))
        return prims

    return build


def _junction_box(p):
    return [("circle", (0, 0), 1.6, False), ("text", (0, -0.7), "P", "center")]


def _bell_button(p):
    return [("circle", (0, 0), 1.2, False), ("circle", (0, 0), 0.35, True)]


def _arrow(tip, tail):
    """Filled arrow head at `tip`, pointing away from `tail`."""
    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    n = math.hypot(dx, dy)
    ux, uy = dx / n, dy / n
    base = (tip[0] - ux * 1.2, tip[1] - uy * 1.2)
    return ("poly", [tip, (base[0] - uy * 0.45, base[1] + ux * 0.45),
                     (base[0] + uy * 0.45, base[1] - ux * 0.45)], True, True)


def _riser(up):
    def build(p):
        end = (3.0, 1.5)
        start = (0.45, 0.22)
        prims = [("circle", (0, 0), 0.5, False), ("line", start, end)]
        prims.append(_arrow(end, start) if up else _arrow((0.9, 0.45), end))
        if p.get("circuito"):
            prims.append(("text", (3.2, 1.2), f"-{p['circuito']}-", "left"))
        return prims

    return build


SYMBOLS = {
    "tomada_baixa": ("Tomada na parede, baixa (300 mm do piso)", _socket("empty")),
    "tomada_media": ("Tomada na parede, a meia altura (1300 mm do piso)", _socket("half")),
    "tomada_alta": ("Tomada na parede, alta (2000 mm do piso)", _socket("full")),
    "tomada_piso": ("Tomada no piso", _floor_socket),
    "interruptor_simples": ("Interruptor simples de uma seção (uma tecla)", _switch("simples", 1)),
    "interruptor_simples_2": ("Interruptor simples de duas seções", _switch("simples", 2)),
    "interruptor_simples_3": ("Interruptor simples de três seções", _switch("simples", 3)),
    "interruptor_paralelo": ("Interruptor paralelo (three-way)", _switch("paralelo", 1)),
    "interruptor_intermediario": ("Interruptor intermediário (four-way)", _switch("intermediario", 1)),
    "ponto_luz_teto": ("Ponto de luz incandescente no teto", _ceiling_light(False)),
    "ponto_luz_teto_embutido": ("Ponto de luz no teto, embutido", _ceiling_light(True)),
    "ponto_luz_parede": ("Ponto de luz na parede (arandela)", _wall_light),
    "luminaria_fluorescente": ("Ponto de luz fluorescente no teto", _fluorescent),
    "quadro_terminal": ("Quadro terminal de luz e força (QD)", _panel("terminal")),
    "quadro_geral": ("Quadro geral de luz e força (QG)", _panel("geral")),
    "caixa_passagem": ("Caixa de passagem na parede", _junction_box),
    "botao_campainha": ("Botão de campainha na parede", _bell_button),
    "circuito_sobe": ("Circuito que sobe", _riser(True)),
    "circuito_desce": ("Circuito que desce", _riser(False)),
}

PARAMS_DOC = ("letra: ponto de comando (a, b...; em interruptores de várias seções, 'ab' ou 'abc'); "
              "circuito: número do circuito (aparece entre traços); potencia: texto, ex. '300VA', '2x100W'")


# --- building -------------------------------------------------------------------


def _fill_black(obj):
    """Black fill; for Draft objects and layers the fill is the ShapeAppearance material."""
    if not (App.GuiUp and obj.ViewObject):
        return
    vobj = obj.ViewObject
    if hasattr(vobj, "ShapeAppearance"):
        material = App.Material()
        material.DiffuseColor = BLACK
        vobj.ShapeAppearance = (material,)
    elif hasattr(vobj, "ShapeColor"):
        vobj.ShapeColor = BLACK


class _Placer:
    """Turns paper coordinates of a symbol into model objects."""

    def __init__(self, doc, origin, rotation, scale, layer):
        self.doc = doc
        self.origin = origin
        self.rot = App.Rotation(App.Vector(0, 0, 1), rotation)
        self.rotation = rotation
        self.scale = scale
        self.layer = get_or_create_layer(doc, layer, line_color=BLACK) if layer else None
        if self.layer is not None:
            _fill_black(self.layer)  # layers impose their fill on their objects
        self.objects = []

    def pt(self, xy):
        return self.origin + self.rot.multVec(App.Vector(xy[0] * self.scale, xy[1] * self.scale, 0))

    def _add(self, obj, filled=False):
        if App.GuiUp and obj.ViewObject:
            vobj = obj.ViewObject
            if hasattr(vobj, "LineColor"):
                vobj.LineColor = BLACK
            if filled:
                _fill_black(obj)
        if self.layer is not None:
            self.layer.Proxy.addObject(self.layer, obj)
        self.objects.append(obj)
        return obj

    def build(self, prims):
        Draft = _draft()
        for prim in prims:
            kind = prim[0]
            if kind == "line":
                self._add(Draft.make_line(self.pt(prim[1]), self.pt(prim[2])))
            elif kind == "poly":
                pts = [self.pt(q) for q in prim[1]]
                self._add(Draft.make_wire(pts, closed=prim[2], face=prim[3]), filled=prim[3])
            elif kind == "circle":
                pl = App.Placement(self.pt(prim[1]), App.Rotation())
                self._add(Draft.make_circle(prim[2] * self.scale, placement=pl, face=prim[3]), filled=prim[3])
            elif kind == "text":
                self._text(self.pt(prim[1]), prim[2], prim[3])
            elif kind == "label":
                self._label(*prim[1:])
            else:
                raise CommandError(f"Unknown primitive {kind!r}")

    def _label(self, xy, text, line, mode):
        """Text placed in page directions around a rotated symbol (see the primitives above)."""
        d = self.rot.multVec(App.Vector(1, 0, 0))
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
        pos = self.pt(xy) + App.Vector(off[0] * self.scale, off[1] * self.scale, 0)
        self._text(pos, text, align)

    def _text(self, pos, text, align):
        Draft = _draft()
        # Texts stay upright whatever the symbol rotation.
        obj = Draft.make_text([text], placement=App.Placement(pos, App.Rotation()))
        if App.GuiUp and obj.ViewObject:
            vobj = obj.ViewObject
            vobj.FontSize = TEXT * self.scale
            if hasattr(vobj, "ScaleMultiplier"):
                vobj.ScaleMultiplier = 1.0
            if hasattr(vobj, "Justification"):
                vobj.Justification = {"left": "Left", "center": "Center", "right": "Right"}[align]
        self._add(obj)

    def group(self, label):
        grp = self.doc.addObject("App::DocumentObjectGroup", "Simbolo")
        grp.Label = label
        for obj in self.objects:
            grp.addObject(obj)
        return grp


def _scale(doc, scale):
    return float(scale) if scale else annotation_scale(doc)


@command("list_electrical_symbols")
def list_electrical_symbols():
    return {"symbols": {name: desc for name, (desc, _) in SYMBOLS.items()}, "parameters": PARAMS_DOC,
            "convention": "NBR 5444 (cancelada em 2014, ainda usada como padrão nos projetos)"}


@command("insert_electrical_symbol", transaction=True)
def insert_electrical_symbol(symbol, position, rotation=0, letra=None, circuito=None, potencia=None,
                             scale=None, layer="Elétrica", label=None):
    """Insert an NBR 5444 symbol at `position` (mm). Wall symbols: `position` is on the wall face
    and `rotation` points them into the room (0 = toward +X)."""
    if symbol not in SYMBOLS:
        raise CommandError(f"Unknown symbol {symbol!r}; use one of {sorted(SYMBOLS)}")
    doc = active_document()
    desc, build = SYMBOLS[symbol]
    placer = _Placer(doc, point(position, "position"), angle(rotation), _scale(doc, scale), layer)
    placer.build(build({"letra": letra, "circuito": circuito, "potencia": potencia}))
    details = " ".join(str(v) for v in (potencia, f"-{circuito}-" if circuito else None, letra) if v)
    grp = placer.group(label or f"{desc} {details}".strip())
    recompute(doc)
    return {"group": grp.Name, "label": grp.Label, "objects": len(placer.objects),
            "scale": f"1:{placer.scale:g}"}


# --- conduits and conductors ------------------------------------------------------

# Conductor marks across a conduit, in paper mm, for a conduit along +X (y up = left side).
CONDUCTORS = {
    "fase": [("line", (0, -1.2), (0, 1.2))],
    "neutro": [("line", (0, -1.2), (0, 1.2)), ("line", (0, 1.2), (-0.8, 1.2))],
    "retorno": [("line", (0, 0), (0, 1.2))],
    "terra": [("line", (0, 0), (0, 1.2)), ("line", (-0.6, 1.2), (0.6, 1.2))],
}


@command("draw_conduit", transaction=True)
def draw_conduit(points, installation="teto", diameter=None, conductors=None, circuit=None, gauge=None,
                 marks_at=0.5, scale=None, layer="Elétrica", label=None):
    """Conduit along `points` (mm): solid when in the ceiling or wall ("teto", "parede"), dashed when
    in the floor ("piso"). `conductors` (e.g. ["fase", "neutro", "terra"]) are marked across the
    conduit at `marks_at` (0..1 of its length), with the circuit number and the gauge (mm²)."""
    from .draft2d import _apply_style

    if installation not in ("teto", "parede", "piso"):
        raise CommandError("installation must be 'teto', 'parede' or 'piso'")
    doc = active_document()
    s = _scale(doc, scale)
    pts = [point(p, "points") for p in points]
    if len(pts) < 2:
        raise CommandError("A conduit needs at least 2 points")
    Draft = _draft()
    placer = _Placer(doc, App.Vector(), 0, s, layer)
    wire = placer._add(Draft.make_wire(pts, closed=False, face=False))
    if installation == "piso":
        _apply_style(wire, {"draw_style": "Dashed"})

    # where the marks go: the point at `marks_at` of the length, and the direction there
    lengths = [(b - a).Length for a, b in zip(pts, pts[1:])]
    target = sum(lengths) * min(max(float(marks_at), 0.0), 1.0)
    for (a, b), seg in zip(zip(pts, pts[1:]), lengths):
        if target <= seg:
            break
        target -= seg
    else:
        target = seg  # rounding past the end: the end of the last segment
    direction = (b - a).normalize()
    at = a + direction * target
    marks = _Placer(doc, at, math.degrees(math.atan2(direction.y, direction.x)), s, layer)
    kinds = list(conductors or [])
    for k in kinds:
        if k not in CONDUCTORS:
            raise CommandError(f"Unknown conductor {k!r}; use {sorted(CONDUCTORS)}")
    pitch = 1.3
    x0 = -pitch * (len(kinds) - 1) / 2
    for i, k in enumerate(kinds):
        marks.build([(p[0], (p[1][0] + x0 + i * pitch, p[1][1]), (p[2][0] + x0 + i * pitch, p[2][1]))
                      for p in CONDUCTORS[k]])
    texts = []
    if circuit is not None and kinds:
        texts.append(("text", (0, 1.9), str(circuit), "center"))
    if gauge is not None and kinds:
        texts.append(("text", (0, -3.4), f"{gauge}".replace(".", ","), "center"))
    if diameter is not None:
        if kinds:  # left of the marks, on the gauge line
            texts.append(("text", (x0 - pitch - 0.5, -3.4), f"ø{diameter}", "right"))
        else:
            texts.append(("text", (0, 1.0), f"ø{diameter}", "center"))
    marks.build(texts)
    placer.objects += marks.objects
    lbl = label or f"Eletroduto ({installation})" + (f" {'+'.join(kinds)}" if kinds else "")
    grp = placer.group(lbl)
    recompute(doc)
    return {"group": grp.Name, "label": grp.Label, "length_mm": round(sum(lengths), 1),
            "objects": len(placer.objects), "scale": f"1:{s:g}"}
