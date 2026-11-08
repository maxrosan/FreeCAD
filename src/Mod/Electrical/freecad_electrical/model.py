# SPDX-License-Identifier: LGPL-2.1-or-later

"""Data model of an electrical installation, saved with the document.

Everything lives in one container, "Instalação elétrica":

* ElectricalPoint (a Part feature): an outlet, light, switch, panel... Its data (kind,
  position, rotation, power, circuit, letter, height, room) is the source of truth; its shape
  is the NBR 5444 symbol drawn from that data, texts included, at the drawing scale.
* Circuit: number, kind, connection (single, two or three phase), phases, panel. Its totals,
  current, suggested breaker and conductor, and load per phase are computed from the points
  that link to it.
* Conduit (a Part feature): a path and the circuits in it; its conductor marks come from the
  circuits.
* LoadSchedule: fills a spreadsheet with the circuits table ("quadro de cargas").

Recompute keeps everything consistent: points link to their circuit, circuits are recomputed
when a point changes or is deleted, the schedule links all circuits. Sheets draw the container,
so new items show up on them.

This module works without the GUI (FreeCADCmd); view providers are attached when the GUI is up.
"""

import FreeCAD as App

from . import calc, geometry
from .symbols import CONDUCTORS, SYMBOLS

CONTAINER = "InstalacaoEletrica"
T_INSTALLATION, T_POINT, T_CIRCUIT, T_CONDUIT, T_SCHEDULE = (
    "InstalacaoEletrica", "PontoEletrico", "Circuito", "Eletroduto", "QuadroDeCargas")
INSTALLATIONS = ["teto", "parede", "piso"]
UNITS = ["VA", "W", "CV"]
DEFAULT_SCALE = 50.0
DEFAULT_PF = 0.92


def type_of(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", None)


def _vp(obj, name):
    """Attach the view provider `name` from gui.py when the GUI is up."""
    if App.GuiUp and obj.ViewObject is not None:
        from . import gui

        getattr(gui, name)(obj.ViewObject)


class _Proxy:
    Type = None

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps

    def __setstate__(self, state):
        return None

    def onDocumentRestored(self, obj):
        pass


def _readonly(obj, *props):
    for prop in props:
        obj.setEditorMode(prop, 1)


# --- installation -----------------------------------------------------------------------


class Installation(_Proxy):
    Type = T_INSTALLATION

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyEnumeration", "System", "Instalação", "Sistema de tensão")
        obj.System = list(calc.SYSTEMS)
        obj.System = calc.DEFAULT_SYSTEM
        obj.addProperty("App::PropertyFloat", "Scale", "Instalação",
                        "Escala do desenho (N de 1:N): tamanho dos símbolos e textos").Scale = DEFAULT_SCALE
        obj.addProperty("App::PropertyFloat", "PowerFactor", "Instalação",
                        "Fator de potência padrão dos itens").PowerFactor = DEFAULT_PF

    def onChanged(self, obj, prop):
        if prop in ("Scale", "System"):  # every symbol is drawn at the scale; currents depend on the system
            for child in getattr(obj, "Group", []):
                if type_of(child) in (T_POINT, T_CONDUIT, T_CIRCUIT):
                    child.touch()


def installation(doc, create=True):
    obj = doc.getObject(CONTAINER)
    if obj is None and create:
        obj = doc.addObject("App::DocumentObjectGroupPython", CONTAINER)
        Installation(obj)
        obj.Label = "Instalação elétrica"
        mcp = doc.getObject("MCP_Settings")  # use the drawing scale set through the MCP, if any
        if mcp is not None and getattr(mcp, "ScaleDenominator", 0) > 1:
            obj.Scale = mcp.ScaleDenominator
        _vp(obj, "ViewProviderInstallation")
    return obj


def scale_of(doc):
    inst = installation(doc, create=False)
    return inst.Scale if inst is not None and inst.Scale > 0 else DEFAULT_SCALE


def system_of(doc):
    inst = installation(doc, create=False)
    return inst.System if inst is not None else calc.DEFAULT_SYSTEM


def _add(doc, obj):
    installation(doc).addObject(obj)


def items(doc, kind):
    return [o for o in doc.Objects if type_of(o) == kind]


def points(doc):
    return items(doc, T_POINT)


def circuits(doc):
    return sorted(items(doc, T_CIRCUIT), key=lambda c: c.Number)


def conduits(doc):
    return items(doc, T_CONDUIT)


# --- points -------------------------------------------------------------------------------


class ElectricalPoint(_Proxy):
    Type = T_POINT

    def __init__(self, obj):
        obj.Proxy = self
        g = "Ponto elétrico"
        obj.addProperty("App::PropertyEnumeration", "Symbol", g, "Tipo do item (símbolo NBR 5444)")
        obj.Symbol = list(SYMBOLS)
        obj.addProperty("App::PropertyVector", "Position", g, "Ponto de inserção (na face da parede, para "
                                                               "itens de parede)")
        obj.addProperty("App::PropertyAngle", "Rotation", g, "Direção do símbolo (0 = +X)")
        obj.addProperty("App::PropertyFloat", "Power", g, "Potência de cada unidade")
        obj.addProperty("App::PropertyEnumeration", "PowerUnit", g, "Unidade da potência")
        obj.PowerUnit = UNITS
        obj.addProperty("App::PropertyInteger", "Quantity", g, "Quantidade (ex.: 2 lâmpadas)").Quantity = 1
        obj.addProperty("App::PropertyFloat", "PowerFactor", g, "Fator de potência").PowerFactor = DEFAULT_PF
        obj.addProperty("App::PropertyLinkGlobal", "Circuit", g, "Circuito do item")
        obj.addProperty("App::PropertyString", "Letter", g, "Letra do comando (a, b...)")
        obj.addProperty("App::PropertyLength", "Height", g, "Altura de instalação, do piso acabado")
        obj.addProperty("App::PropertyString", "Room", g, "Cômodo (vazio: pela posição)")
        obj.addProperty("App::PropertyFloat", "Scale", g, "Escala própria (0 = a da instalação)")
        c = "Cálculo"
        obj.addProperty("App::PropertyFloat", "PowerVA", c, "Potência aparente total (VA)")
        obj.addProperty("App::PropertyFloat", "PowerW", c, "Potência ativa total (W)")
        obj.addProperty("App::PropertyString", "CircuitName", c, "Circuito no último cálculo")
        _readonly(obj, "PowerVA", "PowerW")
        obj.setEditorMode("CircuitName", 2)

    def onChanged(self, obj, prop):
        if prop == "Placement" and not getattr(self, "_building", False):
            _absorb_placement(obj)
        elif prop in ("Power", "PowerUnit", "Quantity", "PowerFactor", "Circuit"):
            for name in (getattr(obj, "CircuitName", ""),):
                old = obj.Document.getObject(name) if name else None
                if old is not None:
                    old.touch()
            circuit = getattr(obj, "Circuit", None)
            if circuit is not None:
                circuit.touch()

    def execute(self, obj):
        sym = SYMBOLS[obj.Symbol]
        va, w = calc.item_power(obj.Power, obj.PowerUnit, obj.Quantity, obj.PowerFactor)
        obj.PowerVA, obj.PowerW = round(va, 1), round(w, 1)
        number = obj.Circuit.Number if obj.Circuit is not None and type_of(obj.Circuit) == T_CIRCUIT else None
        obj.CircuitName = obj.Circuit.Name if obj.Circuit is not None else ""
        params = {"potencia": calc.power_text(obj.Power, obj.PowerUnit, obj.Quantity) if sym.kind else "",
                  "circuito": str(number) if number is not None else "",
                  "letra": obj.Letter}
        scale = obj.Scale if obj.Scale > 0 else scale_of(obj.Document)
        frame = geometry.Frame(obj.Position, obj.Rotation.Value, scale)
        self._building = True
        try:
            obj.Shape = geometry.build_shape(sym.build(params), frame)
            obj.Placement = App.Placement()
        finally:
            self._building = False


def _absorb_placement(obj):
    """Moving or rotating the object (Draft Move/Rotate) moves its data, then the shape is redrawn."""
    pl = obj.Placement
    if pl.isIdentity():
        return
    yaw = pl.Rotation.toEuler()[0]
    point_obj = getattr(obj, "Position", None)
    if point_obj is not None:
        obj.Position = pl.multVec(obj.Position)
        obj.Rotation = obj.Rotation.Value + yaw
    elif hasattr(obj, "Points"):  # conduit
        obj.Points = [pl.multVec(p) for p in obj.Points]
    obj.Proxy._building = True
    try:
        obj.Placement = App.Placement()
    finally:
        obj.Proxy._building = False
    obj.touch()


def find_circuit(doc, number):
    for c in circuits(doc):
        if c.Number == int(number):
            return c
    return None


def _resolve_circuit(doc, circuit, symbol):
    """A circuit object from an object, a number (created if missing) or None."""
    if circuit is None or circuit == "":
        return None
    if not isinstance(circuit, (int, str)):
        return circuit
    found = find_circuit(doc, int(circuit))
    if found is not None:
        return found
    kind = SYMBOLS[symbol].kind or "TUG"
    return make_circuit(doc, number=int(circuit), kind=kind)


def make_point(doc, symbol, position, rotation=0.0, circuit=None, power=None, unit=None, quantity=1,
               letter="", height=None, room="", power_factor=None, label=None):
    if symbol not in SYMBOLS:
        raise ValueError(f"Unknown symbol {symbol!r}; use one of {sorted(SYMBOLS)}")
    sym = SYMBOLS[symbol]
    obj = doc.addObject("Part::FeaturePython", "PontoEletrico")
    ElectricalPoint(obj)
    obj.Symbol = symbol
    obj.Position = position
    obj.Rotation = rotation
    obj.Power = float(power) if power is not None else sym.power
    obj.PowerUnit = unit or sym.unit
    obj.Quantity = int(quantity or 1)
    obj.PowerFactor = float(power_factor) if power_factor else installation(doc).PowerFactor
    obj.Letter = letter or ""
    obj.Height = float(height) if height is not None else sym.height
    obj.Room = room or ""
    obj.Circuit = _resolve_circuit(doc, circuit, symbol)
    obj.Label = label or sym.label
    _add(doc, obj)
    _vp(obj, "ViewProviderPoint")
    return obj


def set_point(obj, **changes):
    """Change data of a point: symbol, position, rotation, circuit, power, unit, quantity, letter,
    height, room, power_factor, label."""
    names = {"symbol": "Symbol", "position": "Position", "rotation": "Rotation", "power": "Power",
             "unit": "PowerUnit", "quantity": "Quantity", "letter": "Letter", "height": "Height",
             "room": "Room", "power_factor": "PowerFactor", "label": "Label"}
    for key, value in changes.items():
        if value is None:
            continue
        if key == "circuit":
            obj.Circuit = _resolve_circuit(obj.Document, value, obj.Symbol)
        elif key in names:
            setattr(obj, names[key], value)
        else:
            raise ValueError(f"Unknown point field {key!r}")
    obj.touch()
    return obj


# --- circuits -------------------------------------------------------------------------------


class Circuit(_Proxy):
    Type = T_CIRCUIT

    def __init__(self, obj):
        obj.Proxy = self
        g = "Circuito"
        obj.addProperty("App::PropertyInteger", "Number", g, "Número do circuito")
        obj.addProperty("App::PropertyString", "Description", g, "Descrição (ex.: Tomadas cozinha)")
        obj.addProperty("App::PropertyEnumeration", "Kind", g, "Tipo do circuito")
        obj.Kind = calc.KINDS
        obj.addProperty("App::PropertyEnumeration", "Connection", g, "Ligação")
        obj.Connection = calc.CONNECTIONS
        obj.addProperty("App::PropertyString", "Phases", g, "Fases em que está ligado (R, S, T)")
        obj.addProperty("App::PropertyString", "Panel", g, "Quadro de distribuição").Panel = "QD1"
        obj.addProperty("App::PropertyInteger", "Breaker", g, "Disjuntor escolhido (A; 0 = sugerido)")
        obj.addProperty("App::PropertyFloat", "Section", g, "Seção escolhida (mm²; 0 = sugerida)")
        c = "Cálculo"
        for prop, doc in (("Points", "Quantidade de itens"), ("PowerVA", "Potência aparente (VA)"),
                          ("PowerW", "Potência ativa (W)"), ("Current", "Corrente de projeto (A)"),
                          ("BreakerSuggested", "Disjuntor sugerido (A)"),
                          ("SectionSuggested", "Seção sugerida (mm²)"),
                          ("LoadR", "Carga na fase R (VA)"), ("LoadS", "Carga na fase S (VA)"),
                          ("LoadT", "Carga na fase T (VA)")):
            obj.addProperty("App::PropertyFloat", prop, c, doc)
            obj.setEditorMode(prop, 1)
        obj.addProperty("App::PropertyStringList", "Warnings", c, "Avisos")
        obj.setEditorMode("Warnings", 1)

    def onChanged(self, obj, prop):
        if prop == "Number" and obj.Document is not None:
            obj.Label = f"Circuito {obj.Number}" + (f" - {obj.Description}" if getattr(obj, "Description", "") else "")
        elif prop == "Description":
            obj.Label = f"Circuito {obj.Number}" + (f" - {obj.Description}" if obj.Description else "")

    def execute(self, obj):
        members = circuit_points(obj)
        va = sum(calc.item_power(p.Power, p.PowerUnit, p.Quantity, p.PowerFactor)[0] for p in members)
        w = sum(calc.item_power(p.Power, p.PowerUnit, p.Quantity, p.PowerFactor)[1] for p in members)
        system = system_of(obj.Document)
        amps = calc.current(va, obj.Connection, system)
        breaker = calc.suggest_breaker(amps, obj.Kind)
        chosen_breaker = obj.Breaker or breaker
        section = calc.suggest_section(chosen_breaker, obj.Kind, obj.Connection)
        chosen_section = obj.Section or section
        loads = calc.phase_loads(va, obj.Connection, obj.Phases)
        obj.Points = len(members)
        obj.PowerVA, obj.PowerW, obj.Current = round(va, 1), round(w, 1), round(amps, 2)
        obj.BreakerSuggested, obj.SectionSuggested = breaker, section
        obj.LoadR, obj.LoadS, obj.LoadT = (round(loads[p], 1) for p in calc.PHASES)
        warnings = []
        if len([p for p in obj.Phases if p in calc.PHASES]) != calc.phase_count(obj.Connection):
            warnings.append(f"Ligação {obj.Connection} pede {calc.phase_count(obj.Connection)} fase(s); "
                            f"indicadas: '{obj.Phases}'")
        if obj.Breaker and obj.Breaker < amps:
            warnings.append(f"Disjuntor de {obj.Breaker} A abaixo da corrente de {amps:.1f} A")
        if obj.Section and obj.Section < calc.MIN_SECTION.get(obj.Kind, 2.5):
            warnings.append(f"Seção de {obj.Section:g} mm² abaixo do mínimo de "
                            f"{calc.MIN_SECTION[obj.Kind]:g} mm² para {obj.Kind}")
        if obj.Section and calc.capacity(obj.Section, obj.Connection) < chosen_breaker:
            warnings.append(f"Seção de {obj.Section:g} mm² não suporta o disjuntor de {chosen_breaker} A")
        if obj.Kind == "Iluminação" and members and any(SYMBOLS[p.Symbol].kind == "TUG" for p in members):
            warnings.append("Circuito de iluminação com tomadas")
        if not members:
            warnings.append("Circuito sem itens")
        obj.Warnings = warnings


def circuit_points(circuit):
    return [o for o in circuit.InList if type_of(o) == T_POINT and o.Circuit == circuit]


def phase_totals(doc, panel=None):
    totals = {p: 0.0 for p in calc.PHASES}
    for c in circuits(doc):
        if panel and c.Panel != panel:
            continue
        for p, attr in zip(calc.PHASES, ("LoadR", "LoadS", "LoadT")):
            totals[p] += getattr(c, attr)
    return totals


def make_circuit(doc, number=None, description="", kind="TUG", connection=calc.MONO, phases=None,
                 panel="QD1", breaker=0, section=0):
    if kind not in calc.KINDS:
        raise ValueError(f"kind must be one of {calc.KINDS}")
    if connection not in calc.CONNECTIONS:
        raise ValueError(f"connection must be one of {calc.CONNECTIONS}")
    if number is None:
        number = max((c.Number for c in circuits(doc)), default=0) + 1
    if find_circuit(doc, number) is not None:
        raise ValueError(f"Circuit {number} already exists")
    obj = doc.addObject("App::FeaturePython", "Circuito")
    Circuit(obj)
    obj.Number = int(number)
    obj.Description = description or ""
    obj.Kind = kind
    obj.Connection = connection
    obj.Panel = panel or "QD1"
    obj.Breaker = int(breaker or 0)
    obj.Section = float(section or 0)
    if not phases:
        others = [c for c in circuits(doc) if c is not obj and c.Panel == obj.Panel]
        counts = {p: sum(1 for c in others if p in c.Phases) for p in calc.PHASES}
        phases = calc.choose_phases(connection, phase_totals(doc, obj.Panel), counts)
    obj.Phases = phases
    _add(doc, obj)
    sched = schedule(doc)
    sched.Circuits = list(sched.Circuits) + [obj]
    _vp(obj, "ViewProviderCircuit")
    return obj


def set_circuit(obj, **changes):
    names = {"number": "Number", "description": "Description", "kind": "Kind", "connection": "Connection",
             "phases": "Phases", "panel": "Panel", "breaker": "Breaker", "section": "Section"}
    for key, value in changes.items():
        if value is None:
            continue
        if key not in names:
            raise ValueError(f"Unknown circuit field {key!r}")
        setattr(obj, names[key], value)
    obj.touch()
    for p in circuit_points(obj):
        p.touch()
    return obj


def rebalance_phases(doc, panel=None):
    """Reassign the phases of every circuit (of `panel`, or all) to spread the load evenly.
    Returns {number: (old phases, new phases)}. Recompute first so the loads are current."""
    doc.recompute()
    selected = [c for c in circuits(doc) if panel is None or c.Panel == panel]
    plan = calc.balance([(c.Name, c.PowerVA, c.Connection) for c in selected])
    changes = {}
    for c in selected:
        new = plan[c.Name]
        if new != c.Phases:
            changes[c.Number] = (c.Phases, new)
            c.Phases = new
            c.touch()
    sched = schedule(doc, create=False)
    if sched is not None:
        sched.touch()
    doc.recompute()
    return changes


def conductors_of(circuit):
    """Conductor marks for a circuit, from its connection."""
    n = calc.phase_count(circuit.Connection)
    marks = ["fase"] * n
    if circuit.Connection in (calc.MONO, calc.TRI_N):
        marks.append("neutro")
    marks.append("terra")
    return marks


# --- conduits -----------------------------------------------------------------------------------


class Conduit(_Proxy):
    Type = T_CONDUIT

    def __init__(self, obj):
        obj.Proxy = self
        g = "Eletroduto"
        obj.addProperty("App::PropertyVectorList", "Points", g, "Pontos do traçado")
        obj.addProperty("App::PropertyEnumeration", "Installation", g, "Embutido no teto, na parede ou no piso")
        obj.Installation = INSTALLATIONS
        obj.addProperty("App::PropertyLinkListGlobal", "Circuits", g, "Circuitos que passam pelo eletroduto")
        obj.addProperty("App::PropertyStringList", "ExtraConductors", g,
                        "Condutores além dos circuitos (ex.: retorno)")
        obj.addProperty("App::PropertyString", "Diameter", g, "Diâmetro (ex.: 25)")
        obj.addProperty("App::PropertyFloat", "MarksAt", g, "Posição das marcas (0 a 1 do comprimento)").MarksAt = 0.5
        obj.addProperty("App::PropertyFloat", "Scale", g, "Escala própria (0 = a da instalação)")

    def onChanged(self, obj, prop):
        if prop == "Placement" and not getattr(self, "_building", False):
            _absorb_placement(obj)

    def execute(self, obj):
        import Part

        pts = list(obj.Points)
        if len(pts) < 2:
            return
        scale = obj.Scale if obj.Scale > 0 else scale_of(obj.Document)
        shapes = []
        for a, b in zip(pts, pts[1:]):
            if obj.Installation == "piso":  # dashed: drawn as dashes so sheets show them too
                shapes += _dashes(a, b, 2.0 * scale, 1.0 * scale)
            elif (b - a).Length > 1e-9:
                shapes.append(Part.LineSegment(a, b).toShape())
        at, direction = _point_along(pts, obj.MarksAt)
        frame = geometry.Frame(at, geometry.angle_of(direction), scale)
        groups = [(str(c.Number), conductors_of(c), c.Section or c.SectionSuggested)
                  for c in obj.Circuits if c is not None and type_of(c) == T_CIRCUIT]
        if obj.ExtraConductors:
            groups.append(("", [c for c in obj.ExtraConductors if c in CONDUCTORS], None))
        prims = []
        pitch, gap = 1.3, 2.2
        widths = [len(marks) * pitch for _, marks, _ in groups]
        x = -(sum(widths) + gap * (len(groups) - 1)) / 2 + pitch / 2
        for (label, marks, section), width in zip(groups, widths):
            start = x
            for mark in marks:
                for prim in CONDUCTORS[mark]:
                    prims.append((prim[0], (prim[1][0] + x, prim[1][1]), (prim[2][0] + x, prim[2][1])))
                x += pitch
            middle = (start + x - pitch) / 2
            if label:
                prims.append(("text", (middle, 1.9), label, "center"))
            if section:
                prims.append(("text", (middle, -3.4), f"{section:g}".replace(".", ","), "center"))
            x += gap
        if obj.Diameter:
            prims.append(("text", (-(sum(widths) + gap * len(groups)) / 2 - 0.5, -3.4), f"ø{obj.Diameter}",
                          "right"))
        if prims:
            shapes.append(geometry.build_shape(prims, frame))
        self._building = True
        try:
            obj.Shape = Part.makeCompound(shapes)
            obj.Placement = App.Placement()
        finally:
            self._building = False


def _dashes(a, b, dash, gap):
    import Part

    d = b - a
    length = d.Length
    if length < 1e-9:
        return []
    u = App.Vector(d).normalize()
    out, s = [], 0.0
    while s < length:
        e = min(s + dash, length)
        out.append(Part.LineSegment(a + u * s, a + u * e).toShape())
        s = e + gap
    return out


def _point_along(pts, fraction):
    lengths = [(b - a).Length for a, b in zip(pts, pts[1:])]
    target = sum(lengths) * min(max(float(fraction), 0.0), 1.0)
    for (a, b), seg in zip(zip(pts, pts[1:]), lengths):
        if target <= seg:
            break
        target -= seg
    else:
        target = seg
    direction = App.Vector(b - a).normalize() if (b - a).Length > 1e-9 else App.Vector(1, 0, 0)
    return a + direction * target, direction


def make_conduit(doc, points, installation="teto", circuits=(), extra=(), diameter="", marks_at=0.5, label=None):
    if installation not in INSTALLATIONS:
        raise ValueError(f"installation must be one of {INSTALLATIONS}")
    if len(points) < 2:
        raise ValueError("A conduit needs at least 2 points")
    for c in extra or ():
        if c not in CONDUCTORS:
            raise ValueError(f"Unknown conductor {c!r}; use {sorted(CONDUCTORS)}")
    obj = doc.addObject("Part::FeaturePython", "Eletroduto")
    Conduit(obj)
    obj.Points = list(points)
    obj.Installation = installation
    obj.Circuits = [_resolve_circuit(doc, c, "tomada_baixa") for c in circuits or ()]
    obj.ExtraConductors = list(extra or ())
    obj.Diameter = str(diameter or "")
    obj.MarksAt = float(marks_at)
    numbers = ",".join(str(c.Number) for c in obj.Circuits)
    obj.Label = label or f"Eletroduto ({installation})" + (f" circ. {numbers}" if numbers else "")
    _add(doc, obj)
    _vp(obj, "ViewProviderConduit")
    return obj


# --- load schedule ------------------------------------------------------------------------------

HEADER = ["Circuito", "Descrição", "Tipo", "Ligação", "Fases", "Itens", "Potência (VA)", "Potência (W)",
          "Corrente (A)", "Disjuntor (A)", "Seção (mm²)", "R (VA)", "S (VA)", "T (VA)"]
COLUMNS = "ABCDEFGHIJKLMN"


class LoadSchedule(_Proxy):
    Type = T_SCHEDULE

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLink", "Sheet", "Quadro de cargas", "Planilha preenchida")
        obj.addProperty("App::PropertyLinkListHidden", "Circuits", "Quadro de cargas", "Circuitos")
        obj.addProperty("App::PropertyString", "LastCell", "Quadro de cargas", "Última célula usada")
        obj.setEditorMode("LastCell", 1)

    def execute(self, obj):
        sheet = obj.Sheet
        if sheet is None:
            return
        doc = obj.Document
        rows = [c for c in circuits(doc)]
        system = system_of(doc)
        sheet.clearAll()
        sheet.set("A1", "QUADRO DE CARGAS")
        sheet.set("A2", f"Sistema {system}")
        for col, title in zip(COLUMNS, HEADER):
            sheet.set(f"{col}4", title)
        r = 5
        for c in rows:
            values = [str(c.Number), c.Description or c.Kind, c.Kind, c.Connection, c.Phases, f"{c.Points:g}",
                      f"{c.PowerVA:.0f}", f"{c.PowerW:.0f}", f"{c.Current:.1f}",
                      f"{c.Breaker or c.BreakerSuggested:g}", f"{c.Section or c.SectionSuggested:g}",
                      f"{c.LoadR:.0f}", f"{c.LoadS:.0f}", f"{c.LoadT:.0f}"]
            for col, value in zip(COLUMNS, values):
                sheet.set(f"{col}{r}", value if col not in "BCDE" else f"'{value}")
            r += 1
        totals = phase_totals(doc)
        total_va = sum(c.PowerVA for c in rows)
        total_w = sum(c.PowerW for c in rows)
        sheet.set(f"A{r}", "'TOTAL")
        sheet.set(f"G{r}", f"{total_va:.0f}")
        sheet.set(f"H{r}", f"{total_w:.0f}")
        for col, p in zip("LMN", calc.PHASES):
            sheet.set(f"{col}{r}", f"{totals[p]:.0f}")
        v_ll, v_ln = calc.voltages(system)
        r += 2
        biggest = max(totals.values()) if totals else 0
        sheet.set(f"A{r}", f"'Desequilíbrio entre fases: {calc.imbalance(totals):.1f} %   "
                           f"Corrente da fase mais carregada: {biggest / v_ln:.1f} A")
        try:
            sheet.setStyle("A1:N1", "bold")
            sheet.setStyle(f"A4:N4", "bold")
            sheet.setStyle(f"A{r - 2}:N{r - 2}", "bold")
            for col, width in zip(COLUMNS, (60, 170, 80, 120, 50, 45, 90, 90, 80, 80, 80, 70, 70, 70)):
                sheet.setColumnWidth(col, width)
        except Exception:
            pass
        obj.LastCell = f"N{r}"
        # The table is written after the sheet's own recompute: recompute it now and clear the
        # touched flag, or the document would report it as still pending.
        sheet.recompute()
        sheet.purgeTouched()


def schedule(doc, create=True):
    for obj in items(doc, T_SCHEDULE):
        return obj
    if not create:
        return None
    sheet = doc.addObject("Spreadsheet::Sheet", "TabelaQuadroDeCargas")
    sheet.Label = "Tabela do quadro de cargas"
    obj = doc.addObject("App::FeaturePython", "QuadroDeCargas")
    LoadSchedule(obj)
    obj.Label = "Quadro de cargas"
    obj.Sheet = sheet
    inst = installation(doc)
    inst.addObject(obj)
    inst.addObject(sheet)
    _vp(obj, "ViewProviderSchedule")
    return obj


# --- summaries -----------------------------------------------------------------------------------


def describe_point(p):
    return {"name": p.Name, "label": p.Label, "symbol": p.Symbol,
            "position": [round(p.Position.x, 1), round(p.Position.y, 1)], "rotation": round(p.Rotation.Value, 1),
            "power": p.Power, "unit": p.PowerUnit, "quantity": p.Quantity, "power_va": p.PowerVA,
            "power_w": p.PowerW, "circuit": p.Circuit.Number if p.Circuit is not None else None,
            "letter": p.Letter, "height_mm": p.Height.Value, "room": p.Room}


def describe_circuit(c):
    return {"name": c.Name, "number": c.Number, "description": c.Description, "kind": c.Kind,
            "connection": c.Connection, "phases": c.Phases, "panel": c.Panel, "items": int(c.Points),
            "power_va": c.PowerVA, "power_w": c.PowerW, "current_a": c.Current,
            "breaker_a": c.Breaker or c.BreakerSuggested, "breaker_suggested_a": c.BreakerSuggested,
            "section_mm2": c.Section or c.SectionSuggested, "section_suggested_mm2": c.SectionSuggested,
            "load_va": {"R": c.LoadR, "S": c.LoadS, "T": c.LoadT}, "warnings": list(c.Warnings)}


def load_summary(doc):
    system = system_of(doc)
    v_ll, v_ln = calc.voltages(system)
    cs = circuits(doc)
    totals = phase_totals(doc)
    panels = {}
    for c in cs:
        panel = panels.setdefault(c.Panel, {"power_va": 0.0, "power_w": 0.0, "circuits": 0})
        panel["power_va"] += c.PowerVA
        panel["power_w"] += c.PowerW
        panel["circuits"] += 1
    return {"system": system, "circuits": [describe_circuit(c) for c in cs],
            "total_va": round(sum(c.PowerVA for c in cs), 1), "total_w": round(sum(c.PowerW for c in cs), 1),
            "phase_va": {p: round(v, 1) for p, v in totals.items()},
            "phase_current_a": {p: round(v / v_ln, 2) for p, v in totals.items()},
            "imbalance_percent": round(calc.imbalance(totals), 1), "panels": panels,
            "unassigned_points": [p.Label for p in points(doc) if p.Circuit is None and SYMBOLS[p.Symbol].kind]}


# --- keeping things up to date when items are deleted ------------------------------------------------


class _Observer:
    def slotDeletedObject(self, obj):
        try:
            kind = type_of(obj)
            if kind == T_POINT and obj.Circuit is not None:
                obj.Circuit.touch()
            elif kind == T_CIRCUIT:
                for dependent in obj.InList:
                    if type_of(dependent) in (T_POINT, T_CONDUIT, T_SCHEDULE):
                        dependent.touch()
            else:
                return
            sched = schedule(obj.Document, create=False)
            if sched is not None:
                sched.touch()
            _later(obj.Document)
        except Exception:
            pass


def _later(doc):
    """Recompute after the deletion is finished (GUI); without the GUI the caller recomputes."""
    if not App.GuiUp:
        return
    from PySide import QtCore

    name = doc.Name
    QtCore.QTimer.singleShot(0, lambda: App.listDocuments().get(name) and App.listDocuments()[name].recompute())


_observer = None


def install_observer():
    global _observer
    if _observer is None:
        _observer = _Observer()
        App.addDocumentObserver(_observer)
