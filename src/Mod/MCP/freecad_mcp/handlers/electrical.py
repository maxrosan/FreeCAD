# SPDX-License-Identifier: LGPL-2.1-or-later

"""Electrical plan commands, on top of the Electrical module (src/Mod/Electrical).

Items are objects that carry their data (power, circuit, room...); their NBR 5444 symbol is
drawn from it. Circuits compute their load, current and protection on 380/220 V systems.
"""

import re

from freecad_electrical import calc, checks, model, sheets
from freecad_electrical.symbols import SYMBOLS

from ..registry import CommandError, command
from ..units import angle, length, point, points
from .common import active_document, get_object, recompute

POWER = re.compile(r"^\s*(?:(\d+)\s*[x×]\s*)?([\d.,]+)\s*(VA|W|CV|HP|kVA|kW)?\s*$", re.I)


def parse_power(text, default_unit):
    """'300VA', '2x100W', '1,5CV', '3 kW', 450 -> (power, unit, quantity)."""
    if text is None or text == "":
        return None, None, None
    if isinstance(text, (int, float)):
        return float(text), default_unit, 1
    m = POWER.match(str(text))
    if not m:
        raise CommandError(f"Cannot read the power {text!r}; use e.g. '300VA', '2x100W', '1,5CV'")
    quantity = int(m.group(1)) if m.group(1) else 1
    value = float(m.group(2).replace(",", "."))
    unit = (m.group(3) or default_unit).upper()
    if unit in ("KVA", "KW"):
        value *= 1000
        unit = unit[1:]
    if unit == "HP":
        value *= 1.0139  # 1 HP = 1.0139 CV
        unit = "CV"
    return value, unit, quantity


def _point_obj(doc, name):
    obj = get_object(doc, name)
    if model.type_of(obj) != model.T_POINT:
        raise CommandError(f"{obj.Label} is not an electrical item")
    return obj


def _circuit_obj(doc, number):
    c = model.find_circuit(doc, number)
    if c is None:
        raise CommandError(f"No circuit {number}")
    return c


@command("list_electrical_symbols")
def list_electrical_symbols():
    return {"symbols": {k: {"label": s.label, "category": s.category, "wall": s.wall,
                            "default_power": f"{s.power:g}{s.unit}" if s.kind else None,
                            "circuit_kind": s.kind, "height_mm": s.height} for k, s in SYMBOLS.items()},
            "circuit_kinds": calc.KINDS, "connections": calc.CONNECTIONS, "systems": list(calc.SYSTEMS),
            "convention": "NBR 5444 (cancelada em 2014, ainda usada como padrão nos projetos)"}


@command("set_installation", transaction=True)
def set_installation(system=None, scale=None, power_factor=None):
    """Voltage system ('380/220 V' default, or '220/127 V'), drawing scale N (1:N), default power factor."""
    doc = active_document()
    inst = model.installation(doc)
    if system is not None:
        if system not in calc.SYSTEMS:
            raise CommandError(f"system must be one of {list(calc.SYSTEMS)}")
        inst.System = system
    if scale is not None:
        inst.Scale = float(scale)
    if power_factor is not None:
        inst.PowerFactor = float(power_factor)
    recompute(doc)
    return {"system": inst.System, "scale": f"1:{inst.Scale:g}", "power_factor": inst.PowerFactor}


@command("insert_electrical_symbol", transaction=True)
def insert_electrical_symbol(symbol, position, rotation=0, letra=None, circuito=None, potencia=None,
                             quantidade=None, altura=None, comodo=None, fator_potencia=None, scale=None,
                             label=None, layer=None):
    """Insert an electrical item with its data; its symbol is drawn from it at the drawing scale."""
    if symbol not in SYMBOLS:
        raise CommandError(f"Unknown symbol {symbol!r}; use one of {sorted(SYMBOLS)}")
    doc = active_document()
    power, unit, qty = parse_power(potencia, SYMBOLS[symbol].unit)
    if scale:
        model.installation(doc).Scale = float(scale)
    obj = model.make_point(doc, symbol, point(position, "position"), angle(rotation),
                           circuit=circuito, power=power, unit=unit, quantity=quantidade or qty or 1,
                           letter=letra or "", height=length(altura, "altura") if altura is not None else None,
                           room=comodo or "", power_factor=fator_potencia, label=label)
    recompute(doc)
    info = model.describe_point(obj)
    info["scale"] = f"1:{model.scale_of(doc):g}"
    return info


@command("set_electrical_item", transaction=True)
def set_electrical_item(name, symbol=None, position=None, rotation=None, circuito=None, potencia=None,
                        quantidade=None, letra=None, altura=None, comodo=None, fator_potencia=None):
    """Change an item's data (only the fields given); its symbol, circuit and the schedule follow."""
    doc = active_document(create=False)
    obj = _point_obj(doc, name)
    power, unit, qty = parse_power(potencia, obj.PowerUnit)
    if circuito == 0 or circuito == "nenhum":
        obj.Circuit = None
        circuito = None
    model.set_point(obj, symbol=symbol, position=point(position, "position") if position is not None else None,
                    rotation=angle(rotation) if rotation is not None else None, circuit=circuito, power=power,
                    unit=unit, quantity=quantidade or (qty if potencia is not None else None), letter=letra,
                    height=length(altura, "altura") if altura is not None else None, room=comodo,
                    power_factor=fator_potencia)
    recompute(doc)
    return model.describe_point(obj)


@command("list_electrical_items")
def list_electrical_items(circuito=None, symbol=None):
    doc = active_document(create=False)
    found = model.points(doc)
    if circuito is not None:
        found = [p for p in found if p.Circuit is not None and p.Circuit.Number == int(circuito)]
    if symbol:
        found = [p for p in found if p.Symbol == symbol]
    return {"items": [model.describe_point(p) for p in found],
            "circuits": [model.describe_circuit(c) for c in model.circuits(doc)]}


@command("create_circuit", transaction=True)
def create_circuit(number=None, description="", kind="TUG", connection=calc.MONO, phases=None, panel="QD1",
                   breaker=0, section=0):
    doc = active_document()
    try:
        c = model.make_circuit(doc, number, description, kind, connection, phases, panel, breaker, section)
    except ValueError as e:
        raise CommandError(str(e)) from e
    recompute(doc)
    return model.describe_circuit(c)


@command("set_circuit", transaction=True)
def set_circuit(number, new_number=None, description=None, kind=None, connection=None, phases=None,
                panel=None, breaker=None, section=None):
    doc = active_document(create=False)
    c = _circuit_obj(doc, number)
    model.set_circuit(c, number=new_number, description=description, kind=kind, connection=connection,
                      phases=phases, panel=panel, breaker=breaker, section=section)
    recompute(doc)
    return model.describe_circuit(c)


@command("load_summary")
def load_summary():
    doc = active_document(create=False)
    recompute(doc)
    return model.load_summary(doc)


@command("rebalance_phases", transaction=True)
def rebalance_phases(panel=None):
    doc = active_document(create=False)
    changes = model.rebalance_phases(doc, panel)
    summary = model.load_summary(doc)
    return {"changes": {str(n): {"from": old, "to": new} for n, (old, new) in changes.items()},
            "phase_va": summary["phase_va"], "imbalance_percent": summary["imbalance_percent"]}


@command("check_installation")
def check_installation():
    return checks.check(active_document(create=False))


@command("create_electrical_sheets", transaction=True)
def create_electrical_sheets(sheet="auto", title=None):
    """Plan sheet (everything in the document, kept up to date) and the load schedule page."""
    doc = active_document(create=False)
    try:
        page, view, size, scale, box = sheets.make_sheet(doc, sheet, title=title or f"{doc.Label} - planta elétrica")
        schedule_page, table = sheets.make_schedule_page(doc)
    except ValueError as e:
        raise CommandError(str(e)) from e
    return {"plan_page": page.Name, "sheet": size, "scale": f"1:{scale:g}", "schedule_page": schedule_page.Name}


@command("draw_conduit", transaction=True)
def draw_conduit(points=None, installation="teto", circuits=None, extra=None, diameter=None, marks_at=0.5,
                 conductors=None, circuit=None, gauge=None, scale=None, layer=None, label=None):
    """Conduit along `points`; its conductor marks come from `circuits` (numbers) plus `extra`
    conductors (e.g. ["retorno"]). Older calls with `conductors` and `circuit` still work: the
    circuit is linked and any "retorno" in `conductors` becomes an extra conductor; `gauge` and
    `layer` are ignored (the gauge comes from the circuit)."""
    doc = active_document()
    circuit_list = list(circuits or [])
    if circuit is not None and circuit not in circuit_list:
        circuit_list.append(circuit)
    extras = list(extra or []) + [c for c in (conductors or []) if c == "retorno"]
    if scale:
        model.installation(doc).Scale = float(scale)
    try:
        obj = model.make_conduit(doc, _points(points), installation, circuit_list, extras,
                                 diameter if diameter is not None else "", marks_at, label)
    except ValueError as e:
        raise CommandError(str(e)) from e
    recompute(doc)
    length_mm = sum((b - a).Length for a, b in zip(obj.Points, obj.Points[1:]))
    return {"name": obj.Name, "label": obj.Label, "length_mm": round(length_mm, 1),
            "circuits": [c.Number for c in obj.Circuits], "extra": list(obj.ExtraConductors),
            "scale": f"1:{model.scale_of(doc):g}"}


def _points(values):
    if not values or len(values) < 2:
        raise CommandError("A conduit needs at least 2 points")
    return points(values, "points")
