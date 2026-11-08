# SPDX-License-Identifier: LGPL-2.1-or-later

"""Basic NBR 5410 checks of an installation: circuits, phase balance and the load-planning
minimums per room (lighting VA, number and VA of general-use sockets).

Rooms are BIM spaces (Arch Space); an item belongs to the room that contains its position,
unless its Room field names one. Results are estimates for review, not a design approval.
"""

import FreeCAD as App

from . import calc, model
from .symbols import SYMBOLS


def _footprint(space):
    """Lowest horizontal face of a space."""
    shape = getattr(space, "Shape", None)
    if shape is None or shape.isNull() or not shape.Faces:
        return None
    zmin = shape.BoundBox.ZMin
    faces = [f for f in shape.Faces if abs(f.BoundBox.ZMax - zmin) < 1e-3 and abs(f.BoundBox.ZMin - zmin) < 1e-3]
    return max(faces, key=lambda f: f.Area) if faces else None


def rooms(doc):
    found = []
    for obj in doc.Objects:
        if model.type_of(obj) != "Space":
            continue
        face = _footprint(obj)
        if face is None:
            continue
        box = obj.Shape.BoundBox
        found.append({"object": obj, "name": obj.Label, "face": face, "zmin": box.ZMin, "zmax": max(box.ZMax, box.ZMin + 1),
                      "area_m2": face.Area / 1e6, "perimeter_m": face.OuterWire.Length / 1000.0,
                      "category": calc.room_category(obj.Label)})
    return found


def room_of(point, room_list):
    if point.Room:
        for room in room_list:
            if room["name"].lower() == point.Room.lower():
                return room
        return None
    z = point.Position.z
    for room in room_list:
        # the room of the same floor: items sit at the floor elevation, within the room's height
        if not room["zmin"] - model.FLOOR_TOLERANCE <= z < room["zmax"] - 1:
            continue
        face = room["face"]
        probe = App.Vector(point.Position.x, point.Position.y, face.BoundBox.ZMin)
        if face.isInside(probe, 1.0, True):
            return room
    return None


def indoor_floor_conduits(doc, room_list=None):
    """Floor conduits ("piso") that run inside a room: under the ceramic floor, which is not
    recommended indoors (go down the wall from the forro instead). Outdoors they are fine."""
    room_list = rooms(doc) if room_list is None else room_list
    found = []
    for c in model.conduits(doc):
        if getattr(c, "Installation", "") != "piso" or c.Shape.isNull():
            continue
        z = c.Shape.BoundBox.ZMin
        probes = [e.valueAt(e.FirstParameter + 0.5 * (e.LastParameter - e.FirstParameter)) for e in c.Shape.Edges]
        for room in room_list:
            if not room["zmin"] - model.FLOOR_TOLERANCE <= z < room["zmax"] - 1:
                continue
            face = room["face"]
            if any(face.isInside(App.Vector(p.x, p.y, face.BoundBox.ZMin), 1.0, True) for p in probes):
                found.append((c, room["name"]))
                break
    return found


def check(doc):
    doc.recompute()
    report = {"system": model.system_of(doc), "circuits": [], "rooms": [], "warnings": [],
              "note": "Conferência básica (NBR 5410, previsão de cargas e proteção): estimativa para revisão, "
                      "não substitui o projeto do responsável técnico."}
    for c in model.circuits(doc):
        if c.Warnings:
            report["circuits"].append({"number": c.Number, "warnings": list(c.Warnings)})
    totals = model.phase_totals(doc)
    unbalance = calc.imbalance(totals)
    if unbalance > 10:
        report["warnings"].append(f"Desequilíbrio entre fases de {unbalance:.0f} % (R {totals['R']:.0f}, "
                                  f"S {totals['S']:.0f}, T {totals['T']:.0f} VA): use rebalance_phases")
    loose = [p.Label for p in model.points(doc) if p.Circuit is None and SYMBOLS[p.Symbol].kind]
    if loose:
        report["warnings"].append(f"{len(loose)} item(ns) com carga sem circuito: {', '.join(loose[:10])}")
    from . import network

    net = network.network(doc)
    if model.conduits(doc) and net.unrouted:
        report["warnings"].append(f"{len(net.unrouted)} item(ns) sem eletroduto até o quadro (comprimento estimado "
                                  f"em ângulo reto): {', '.join(net.unrouted[:10])}")
    report["service_entrances"] = []
    for name in model.top_panels(doc):
        dem = model.demand_summary(doc, name)
        report["service_entrances"].append(dem)
        if dem["category"] is None:
            report["warnings"].append(f"Quadro {name}: {dem['category_basis']}")
    report["floors"] = model.floors(doc)

    room_list = rooms(doc)
    for c, room in indoor_floor_conduits(doc, room_list):
        report["warnings"].append(f"{c.Label}: eletroduto no piso dentro de {room} (sob a cerâmica); "
                                  "em área interna prefira descer pela parede a partir do forro")
    by_room = {id(r): [] for r in room_list}
    for p in model.points(doc):
        room = room_of(p, room_list)
        if room is not None:
            by_room[id(room)].append(p)
    for room in room_list:
        members = by_room[id(room)]
        lights = [p for p in members if SYMBOLS[p.Symbol].kind == "Iluminação"]
        # general-use sockets: socket symbols whose circuit is a TUG one (a shower outlet on a TUE
        # circuit uses the same symbol but is not a general-use socket)
        sockets = [p for p in members if SYMBOLS[p.Symbol].kind == "TUG"
                   and (p.Circuit is None or p.Circuit.Kind == "TUG")]
        light_va = sum(p.PowerVA for p in lights)
        min_light = calc.min_lighting_va(room["area_m2"])
        min_count = calc.min_sockets(room["category"], room["area_m2"], room["perimeter_m"])
        needed_va = sorted(calc.min_socket_va(room["category"], len(sockets)), reverse=True)
        have_va = sorted((p.PowerVA for p in sockets), reverse=True)
        short_va = sum(1 for need, have in zip(needed_va, have_va) if have + 1e-6 < need)
        warnings = []
        if light_va < min_light:
            warnings.append(f"Iluminação de {light_va:.0f} VA; mínimo {min_light} VA")
        if len(sockets) < min_count:
            warnings.append(f"{len(sockets)} tomada(s) de uso geral; mínimo {min_count}")
        if short_va:
            warnings.append(f"{short_va} tomada(s) abaixo da potência mínima "
                            f"({'600 VA nas três primeiras' if room['category'] in ('cozinha', 'banheiro') else '100 VA'})")
        report["rooms"].append({"room": room["name"], "category": room["category"],
                                "area_m2": round(room["area_m2"], 2), "perimeter_m": round(room["perimeter_m"], 2),
                                "lighting_va": round(light_va), "min_lighting_va": min_light,
                                "sockets": len(sockets), "min_sockets": min_count, "ok": not warnings,
                                "warnings": warnings})
    return report
