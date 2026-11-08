# SPDX-License-Identifier: LGPL-2.1-or-later

"""Electrical symbols for building plans (Brazilian NBR 5444 convention), as data.

Symbols are lists of primitives in paper millimetres with the insertion point at the
origin. Wall-mounted symbols have the wall at x = 0 and point along +X into the room.
When a symbol is drawn, every size is multiplied by the drawing scale (1:N), so it has
the same size on every sheet printed at that scale.

NBR 5444 was cancelled by ABNT in 2014 without a replacement, but its symbols are still
the ones used on Brazilian electrical plans.

Primitives:
  ("line", (x1, y1), (x2, y2))
  ("poly", [(x, y), ...], closed, filled)
  ("circle", (cx, cy), r, filled)
  ("text", (x, y), text, align)          align: "left" | "center" | "right"
  ("label", (x, y), text, line, mode)    text placed after rotating the symbol, so it never
      lands on the wall: mode "tip" puts it past the tip of a wall symbol, on the room side;
      "side" beside a box (above/below when horizontal, to the right when vertical);
      line 0 is the first line of text, 1 the second.
  ("block", (x, y), [lines], mode[, r])  texts that may move to avoid overlaps (see
      geometry.block_candidates): mode "tip" for a wall symbol whose tip is at (x, y), "round" for
      a symbol of radius r centred at (x, y).
"""

import math

TEXT_HEIGHT = 2.0  # paper height of the symbol texts (mm)


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


def _lines(p, letter=False):
    """Power, circuit (and letter) of an item, one per line."""
    lines = [str(p["potencia"])] if p.get("potencia") else []
    if p.get("circuito"):
        lines.append(f"-{p['circuito']}-")
    if letter and p.get("letra"):
        lines.append(str(p["letra"]))
    return lines


def _tip_labels(p, tip):
    """Power and circuit of a wall symbol: a block of texts, past its tip unless that overlaps."""
    lines = _lines(p, letter=True)
    return [("block", tip, lines, "tip")] if lines else []


def _socket(filled):
    def build(p):
        return _wall(2.0) + _triangle(2.0, filled) + _tip_labels(p, (4.4, 0))

    return build


def _floor_socket(p):
    prims = [("poly", [(-1.8, -1.8), (1.8, -1.8), (1.8, 1.8), (-1.8, 1.8)], True, False)]
    prims += [("poly", [(-1.0, -1.1), (1.2, 0), (-1.0, 1.1)], True, False)]
    lines = _lines(p)
    return prims + ([("block", (0, 0), lines, "round", 1.8)] if lines else [])


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


def _light_labels(p, r, half_width=None):
    prims = []
    if p.get("letra"):
        prims.append(("text", (r * 0.75, r + 0.4), str(p["letra"]), "left"))
    lines = _lines(p)
    if lines:
        prims.append(("block", (0, 0), lines, "round", half_width or r))
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
    return prims + _light_labels(p, 0.9, 3.5)


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


def _motor(p):
    prims = [("circle", (0, 0), 2.2, False), ("text", (0, -1.0), "M", "center")]
    lines = _lines(p)
    return prims + ([("block", (0, 0), lines, "round", 2.2)] if lines else [])


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


class Symbol:
    """A symbol and what an item of that type carries by default."""

    def __init__(self, label, build, category, wall=False, power=0.0, unit="VA", kind=None, height=0.0):
        self.label = label
        self.build = build
        self.category = category  # toolbar / menu group
        self.wall = wall          # placed on a wall face, pointing into the room
        self.power = power        # default power of one item
        self.unit = unit          # VA, W or CV
        self.kind = kind          # circuit kind it normally belongs to (None: carries no load)
        self.height = height      # mounting height above the floor (mm)


TOMADAS, INTERRUPTORES, ILUMINACAO, QUADROS = "Tomadas", "Interruptores", "Iluminação", "Quadros e outros"

SYMBOLS = {
    "tomada_baixa": Symbol("Tomada na parede, baixa (300 mm do piso)", _socket("empty"), TOMADAS,
                           wall=True, power=100, kind="TUG", height=300),
    "tomada_media": Symbol("Tomada na parede, a meia altura (1300 mm do piso)", _socket("half"), TOMADAS,
                           wall=True, power=100, kind="TUG", height=1300),
    "tomada_alta": Symbol("Tomada na parede, alta (2000 mm do piso)", _socket("full"), TOMADAS,
                          wall=True, power=100, kind="TUG", height=2000),
    "tomada_piso": Symbol("Tomada no piso", _floor_socket, TOMADAS, power=100, kind="TUG"),
    "interruptor_simples": Symbol("Interruptor simples de uma seção (uma tecla)", _switch("simples", 1),
                                  INTERRUPTORES, height=1300),
    "interruptor_simples_2": Symbol("Interruptor simples de duas seções", _switch("simples", 2),
                                    INTERRUPTORES, height=1300),
    "interruptor_simples_3": Symbol("Interruptor simples de três seções", _switch("simples", 3),
                                    INTERRUPTORES, height=1300),
    "interruptor_paralelo": Symbol("Interruptor paralelo (three-way)", _switch("paralelo", 1),
                                   INTERRUPTORES, height=1300),
    "interruptor_intermediario": Symbol("Interruptor intermediário (four-way)", _switch("intermediario", 1),
                                        INTERRUPTORES, height=1300),
    "ponto_luz_teto": Symbol("Ponto de luz no teto", _ceiling_light(False), ILUMINACAO,
                             power=100, unit="W", kind="Iluminação"),
    "ponto_luz_teto_embutido": Symbol("Ponto de luz no teto, embutido", _ceiling_light(True), ILUMINACAO,
                                      power=100, unit="W", kind="Iluminação"),
    "ponto_luz_parede": Symbol("Ponto de luz na parede (arandela)", _wall_light, ILUMINACAO,
                               wall=True, power=60, unit="W", kind="Iluminação", height=2000),
    "luminaria_fluorescente": Symbol("Luminária fluorescente / LED tubular no teto", _fluorescent, ILUMINACAO,
                                     power=40, unit="W", kind="Iluminação"),
    "quadro_terminal": Symbol("Quadro terminal de luz e força (QD)", _panel("terminal"), QUADROS, height=1500),
    "quadro_geral": Symbol("Quadro geral de luz e força (QG)", _panel("geral"), QUADROS, height=1500),
    "motor": Symbol("Motor (ponto de força)", _motor, QUADROS, power=1, unit="CV", kind="Motor"),
    "caixa_passagem": Symbol("Caixa de passagem na parede", _junction_box, QUADROS),
    "botao_campainha": Symbol("Botão de campainha na parede", _bell_button, QUADROS, height=1300),
    "circuito_sobe": Symbol("Circuito que sobe", _riser(True), QUADROS),
    "circuito_desce": Symbol("Circuito que desce", _riser(False), QUADROS),
}

CATEGORIES = [TOMADAS, INTERRUPTORES, ILUMINACAO, QUADROS]

# Conductor marks across a conduit, in paper mm, for a conduit along +X (y up = left side).
CONDUCTORS = {
    "fase": [("line", (0, -1.2), (0, 1.2))],
    "neutro": [("line", (0, -1.2), (0, 1.2)), ("line", (0, 1.2), (-0.8, 1.2))],
    "retorno": [("line", (0, 0), (0, 1.2))],
    "terra": [("line", (0, 0), (0, 1.2)), ("line", (-0.6, 1.2), (0.6, 1.2))],
}
