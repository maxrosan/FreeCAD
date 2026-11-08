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
from .symbols import CONDUCTORS, SYMBOLS, TEXT_HEIGHT

CONTAINER = "InstalacaoEletrica"
T_INSTALLATION, T_POINT, T_CIRCUIT, T_CONDUIT, T_SCHEDULE = (
    "InstalacaoEletrica", "PontoEletrico", "Circuito", "Eletroduto", "QuadroDeCargas")
INSTALLATIONS = ["teto", "parede", "piso", "enterrado"]
UNITS = ["VA", "W", "CV"]
T_MATERIALS, T_DIAGRAM, T_MODEL3D = "ListaDeMateriais", "DiagramaUnifilar", "Instalacao3D"
OUTPUT_TYPES = (T_MATERIALS, T_DIAGRAM, T_MODEL3D)
RCD_MODES = ["Automático", "Sim", "Não"]
FLOOR_TOLERANCE = 500.0  # mm: items and conduits closer than this in height are on the same floor
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

    def svg_filled_shapes(self, obj):
        """Texts of the object, filled without an outline on sheets (Draft get_svg asks)."""
        labels = getattr(obj, "Labels", None)
        return [labels] if labels is not None and not labels.isNull() else []

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


def _ensure(obj, specs, record=True):
    """Add the properties of `specs` that `obj` lacks (files saved by older versions).
    specs: [(type, name, group, doc, default, editor mode)]."""
    added = False
    for kind, name, group, doc, default, mode in specs:
        if name in obj.PropertiesList:
            continue
        added = True
        obj.addProperty(kind, name, group, doc)
        if isinstance(default, list):
            setattr(obj, name, default)
            setattr(obj, name, default[0])
        elif default is not None:
            setattr(obj, name, default)
        if mode:
            obj.setEditorMode(name, mode)
    if added and record:
        _MIGRATED.setdefault(obj.Document.Name if obj.Document else "", []).append(obj.Name)


def _restoring(obj):
    doc = getattr(obj, "Document", None)
    return doc is None or getattr(doc, "Restoring", False)


def network_changed(doc):
    """Something that changes routing or totals changed (an item moved, a conduit was drawn...):
    recompute circuits, automatic conduits and the generated outputs."""
    if doc is None or getattr(doc, "Restoring", False):
        return
    for obj in doc.Objects:
        kind = type_of(obj)
        # items and conduits too: their texts move to stay clear of each other (layout)
        if kind in (T_CIRCUIT, T_POINT, T_CONDUIT) or kind in OUTPUT_TYPES:
            obj.touch()


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
        _ensure(obj, self.SPECS, record=False)

    SPECS = [("App::PropertyLength", "CeilingHeight", "Instalação",
              "Pé-direito: altura dos eletrodutos de teto, para as descidas até os pontos", 2800.0, 0),
             ("App::PropertyFloat", "AmbientTemperature", "Instalação",
              "Temperatura ambiente (°C), para o fator de correção dos condutores", 30.0, 0),
             ("App::PropertyFloat", "MaxVoltageDrop", "Instalação",
              "Queda de tensão máxima dos circuitos terminais (%)", calc.MAX_VOLTAGE_DROP, 0)]

    def onDocumentRestored(self, obj):
        _ensure(obj, self.SPECS)

    def onChanged(self, obj, prop):
        if prop in ("Scale", "System", "CeilingHeight", "AmbientTemperature", "MaxVoltageDrop") \
                and not _restoring(obj):
            # every symbol is drawn at the scale; currents, sizing and the 3D depend on the others
            network_changed(obj.Document)


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
        _ensure(obj, self.SPECS, record=False)

    SPECS = [("App::PropertyEnumeration", "DemandGroup", "Ponto elétrico",
              "Grupo para o cálculo da demanda (Automático: pelo tipo e pelo nome)",
              [calc.AUTO_GROUP] + calc.DEMAND_GROUPS, 0),
             ("Part::PropertyPartShape", "Labels", "Ponto elétrico", "Textos do símbolo", None, 2)]

    def onDocumentRestored(self, obj):
        _ensure(obj, self.SPECS)
        if list(obj.getEnumerationsOfProperty("Symbol") or []) != list(SYMBOLS):
            value = obj.Symbol  # files from before new choices were added
            obj.Symbol = list(SYMBOLS)
            obj.Symbol = value

    def onChanged(self, obj, prop):
        if prop == "Placement" and not getattr(self, "_building", False):
            _absorb_placement(obj)
        elif prop in ("Power", "PowerUnit", "Quantity", "PowerFactor", "Circuit", "Position", "Letter", "Symbol",
                      "Height", "Room", "DemandGroup", "Label") and not _restoring(obj):
            # its circuit (old and new), feeders, routing of the conduits and the outputs
            name = getattr(obj, "CircuitName", "")
            old = obj.Document.getObject(name) if name else None
            if old is not None:
                old.touch()
            network_changed(obj.Document)

    def execute(self, obj):
        sym = SYMBOLS[obj.Symbol]
        va, w = calc.item_power(obj.Power, obj.PowerUnit, obj.Quantity, obj.PowerFactor)
        obj.PowerVA, obj.PowerW = round(va, 1), round(w, 1)
        obj.CircuitName = obj.Circuit.Name if obj.Circuit is not None else ""
        prims, frame = point_parts(obj)
        from . import layout

        shape, labels = geometry.build_shape(prims, frame, layout.slot_of(obj))
        self._building = True
        try:
            obj.Shape = shape
            if "Labels" in obj.PropertiesList:
                obj.Labels = labels if labels is not None else _empty_shape()
            obj.Placement = App.Placement()
        finally:
            self._building = False


def _empty_shape():
    import Part

    return Part.Shape()


def point_parts(obj):
    """(primitives, frame) of an item's symbol, from its data."""
    sym = SYMBOLS[obj.Symbol]
    number = obj.Circuit.Number if obj.Circuit is not None and type_of(obj.Circuit) == T_CIRCUIT else None
    params = {"potencia": calc.power_text(obj.Power, obj.PowerUnit, obj.Quantity) if sym.kind else "",
              "circuito": str(number) if number is not None else "",
              "letra": obj.Letter}
    if obj.Symbol == "padrao_entrada":
        params["potencia"] = entrance_text(obj.Document, obj)
    scale = obj.Scale if obj.Scale > 0 else scale_of(obj.Document)
    return sym.build(params), geometry.Frame(obj.Position, obj.Rotation.Value, scale)


ENTRANCE = "padrao_entrada"


def entrance_panel(doc, point=None):
    """Panel a service entrance supplies: the panel no feeder comes to nearest to it (for a
    house, the only one)."""
    tops = top_panels(doc)
    if not tops:
        return None
    if point is None or len(tops) == 1:
        return tops[0]

    def distance(name):
        item = next((q for q in points(doc) if q.Symbol in ("quadro_terminal", "quadro_geral")
                     and name.lower() in (q.Label.lower(), (q.Letter or "").lower())), None)
        return (item.Position - point.Position).Length if item is not None else float("inf")

    return min(tops, key=distance)


def entrance(doc, point=None):
    """Service entrance supplying the panel of `point` (a padrão de entrada): category and main
    breaker (Neoenergia DIS-NOR-030), and the cables and conduit from the meter to the panel:
    phases, neutral and earth sized for the main breaker (method B1, on the safe side for a
    buried run)."""
    panel = entrance_panel(doc, point)
    summary = demand_summary(doc, panel)
    breaker = summary["main_breaker_a"] or 0
    connection = summary["connection"]
    section = calc.size_conductor(breaker, "Alimentador", connection)[0] if breaker else 0.0
    cables = calc.phase_count(connection) + (1 if connection in (calc.MONO, calc.TRI_N) else 0) + 1
    return {"panel": panel or "", "category": summary["category"], "breaker_a": breaker,
            "connection": connection, "section": section, "cables": cables,
            "conduit": calc.suggest_conduit([section] * cables) if section else 0}


def entrance_text(doc, point=None):
    try:
        e = entrance(doc, point)
    except Exception:
        return ""
    if not e["category"]:
        return ""
    return f"{e['category']} {e['breaker_a']} A\n{e['cables']}#{e['section']:g} mm²".replace(".", ",")


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
               letter="", height=None, room="", power_factor=None, label=None, demand_group=None):
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
    if demand_group:
        obj.DemandGroup = demand_group
    _add(doc, obj)
    _vp(obj, "ViewProviderPoint")
    return obj


def set_point(obj, **changes):
    """Change data of a point: symbol, position, rotation, circuit, power, unit, quantity, letter,
    height, room, power_factor, label."""
    names = {"symbol": "Symbol", "position": "Position", "rotation": "Rotation", "power": "Power",
             "unit": "PowerUnit", "quantity": "Quantity", "letter": "Letter", "height": "Height",
             "room": "Room", "power_factor": "PowerFactor", "label": "Label", "demand_group": "DemandGroup"}
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
        _ensure(obj, self.SPECS, record=False)

    SPECS = [("App::PropertyFloat", "Length", "Circuito",
              "Comprimento até o ponto mais distante (m; 0 = pelos eletrodutos)", 0.0, 0),
             ("App::PropertyString", "Feeds", "Circuito",
              "Alimentador: nome do quadro que este circuito alimenta (ex.: QD2)", "", 0),
             ("App::PropertyEnumeration", "RCD", "Circuito",
              "Proteção por DR 30 mA (Automático: quando a NBR 5410 exige)", RCD_MODES, 0),
             ("App::PropertyFloat", "LengthUsed", "Cálculo", "Comprimento usado no cálculo (m)", None, 1),
             ("App::PropertyFloat", "VoltageDrop", "Cálculo", "Queda de tensão (%)", None, 1),
             ("App::PropertyInteger", "Grouping", "Cálculo",
              "Circuitos no eletroduto mais cheio por onde passa", 1, 1),
             ("App::PropertyFloat", "CorrectionFactor", "Cálculo",
              "Fator de correção da capacidade (agrupamento x temperatura)", 1.0, 1),
             ("App::PropertyString", "SectionReason", "Cálculo", "O que definiu a seção sugerida", "", 1),
             ("App::PropertyBool", "HasRCD", "Cálculo", "Protegido por DR", False, 1),
             ("App::PropertyString", "RCDReason", "Cálculo", "Por que a norma exige DR", "", 1),
             ("App::PropertyFloat", "DemandVA", "Cálculo", "Demanda do quadro alimentado (VA)", None, 1)]

    def onDocumentRestored(self, obj):
        _ensure(obj, self.SPECS)

    def onChanged(self, obj, prop):
        if prop == "Number" and obj.Document is not None:
            obj.Label = f"Circuito {obj.Number}" + (f" - {obj.Description}" if getattr(obj, "Description", "") else "")
        elif prop == "Description":
            obj.Label = f"Circuito {obj.Number}" + (f" - {obj.Description}" if obj.Description else "")
        if prop in ("Panel", "Feeds", "Connection", "Number") and not _restoring(obj):
            network_changed(obj.Document)  # routing, feeders and outputs

    def execute(self, obj):
        from . import network

        doc = obj.Document
        system = system_of(doc)
        inst = installation(doc, create=False)
        members = circuit_points(obj)
        if obj.Feeds:  # a feeder carries the load of the panel it feeds, sized by its demand
            fed = panel_points(doc, obj.Feeds)
            va = sum(calc.item_power(p.Power, p.PowerUnit, p.Quantity, p.PowerFactor)[0] for p in fed)
            w = sum(calc.item_power(p.Power, p.PowerUnit, p.Quantity, p.PowerFactor)[1] for p in fed)
            dem = demand_summary(doc, obj.Feeds)
            obj.DemandVA = round(dem["demand_kva"] * 1000, 1)
            amps = calc.current(obj.DemandVA, obj.Connection, system)
            loads = raw_phase_loads(doc, obj.Feeds)
            scale = obj.DemandVA / va if va else 0.0
            loads = {p: v * scale for p, v in loads.items()} if obj.Connection != calc.MONO else \
                calc.phase_loads(obj.DemandVA, obj.Connection, obj.Phases)
        else:
            va = sum(calc.item_power(p.Power, p.PowerUnit, p.Quantity, p.PowerFactor)[0] for p in members)
            w = sum(calc.item_power(p.Power, p.PowerUnit, p.Quantity, p.PowerFactor)[1] for p in members)
            obj.DemandVA = 0.0
            amps = calc.current(va, obj.Connection, system)
            loads = calc.phase_loads(va, obj.Connection, obj.Phases)
        net = network.network(doc)
        info = net.circuit_info.get(obj.Name, {})
        length = obj.Length or info.get("length_m", 0.0)
        grouping = self._grouping(obj, net, info, system)
        temperature = getattr(inst, "AmbientTemperature", 30.0) if inst is not None else 30.0
        max_drop = getattr(inst, "MaxVoltageDrop", calc.MAX_VOLTAGE_DROP) if inst is not None else calc.MAX_VOLTAGE_DROP
        factor = calc.grouping_factor(grouping) * calc.temperature_factor(temperature)
        breaker = calc.suggest_breaker(amps, obj.Kind)
        chosen_breaker = obj.Breaker or breaker
        section, reason = calc.size_conductor(chosen_breaker, obj.Kind, obj.Connection, amps, length, system,
                                              factor, max_drop)
        chosen_section = obj.Section or section
        drop = calc.voltage_drop(amps, length, chosen_section, obj.Connection, system)
        rcd_reason = self._rcd_reason(doc, members)
        has_rcd = obj.RCD == "Sim" or (obj.RCD == "Automático" and bool(rcd_reason))
        obj.Points = len(members)
        obj.PowerVA, obj.PowerW, obj.Current = round(va, 1), round(w, 1), round(amps, 2)
        obj.BreakerSuggested, obj.SectionSuggested = breaker, section
        obj.LoadR, obj.LoadS, obj.LoadT = (round(loads[p], 1) for p in calc.PHASES)
        obj.LengthUsed, obj.VoltageDrop = round(length, 2), round(drop, 2)
        obj.Grouping, obj.CorrectionFactor, obj.SectionReason = int(grouping), round(factor, 3), reason
        obj.HasRCD, obj.RCDReason = has_rcd, rcd_reason
        warnings = []
        if len([p for p in obj.Phases if p in calc.PHASES]) != calc.phase_count(obj.Connection):
            warnings.append(f"Ligação {obj.Connection} pede {calc.phase_count(obj.Connection)} fase(s); "
                            f"indicadas: '{obj.Phases}'")
        if obj.Breaker and obj.Breaker < amps:
            warnings.append(f"Disjuntor de {obj.Breaker} A abaixo da corrente de {amps:.1f} A")
        if obj.Section and obj.Section < calc.MIN_SECTION.get(obj.Kind, 2.5):
            warnings.append(f"Seção de {obj.Section:g} mm² abaixo do mínimo de "
                            f"{calc.MIN_SECTION[obj.Kind]:g} mm² para {obj.Kind}")
        if obj.Section and calc.capacity(obj.Section, obj.Connection) * factor < chosen_breaker:
            corrected = f" (com fator {factor:.2f}: {grouping} circuito(s) juntos, {temperature:g} °C)" \
                if factor < 1 else ""
            warnings.append(f"Seção de {obj.Section:g} mm² não suporta o disjuntor de {chosen_breaker} A{corrected}")
        if drop > max_drop:
            warnings.append(f"Queda de tensão de {drop:.1f} % em {length:.1f} m (máximo {max_drop:g} %)")
        if rcd_reason and obj.RCD == "Não":
            warnings.append(f"A NBR 5410 exige DR 30 mA neste circuito ({rcd_reason})")
        if obj.Kind == "Iluminação" and members and any(SYMBOLS[p.Symbol].kind == "TUG" for p in members):
            warnings.append("Circuito de iluminação com tomadas")
        if obj.Feeds and obj.Feeds == obj.Panel:
            warnings.append("O alimentador sai do mesmo quadro que alimenta")
        elif obj.Feeds and not panel_circuits(doc, obj.Feeds):
            warnings.append(f"Nenhum circuito no quadro {obj.Feeds}")
        elif not members and not obj.Feeds:
            warnings.append("Circuito sem itens")
        obj.Warnings = warnings

    @staticmethod
    def _grouping(obj, net, info, system):
        """Circuits sharing the most crowded conduit run of this one, itself included, leaving out
        those that carry up to 30 % of their grouped capacity (NBR 5410 6.2.5.5.6). Their current
        comes from the item data, so the order of recompute does not matter."""
        doc = obj.Document
        grouping = 1
        for e in info.get("edges", ()):
            names = [n for n in net.contents.get(e, {}) if n != obj.Name]
            factor = calc.grouping_factor(len(names) + 1)
            count = 1
            for name in names:
                other = doc.getObject(name)
                if other is None:
                    continue
                if not calc.negligible_in_group(raw_current(other, system), calc.MIN_SECTION.get(other.Kind, 2.5),
                                                other.Connection, factor):
                    count += 1
            grouping = max(grouping, count)
        return grouping

    @staticmethod
    def _rcd_reason(doc, members):
        """Why NBR 5410 requires a 30 mA RCD on a circuit with these items ("" if it does not)."""
        if not members:
            return ""
        from . import checks

        room_list = checks.rooms(doc)
        for p in members:
            sym = SYMBOLS[p.Symbol]
            room = checks.room_of(p, room_list)
            category = room["category"] if room else calc.room_category(p.Room)
            reason = calc.rcd_reason(category, sym.kind, float(p.Height.Value), not sym.wall and sym.height == 0)
            if reason:
                return f"{reason}: {room['name'] if room else p.Room}"
        return ""


def circuit_points(circuit):
    return [o for o in circuit.InList if type_of(o) == T_POINT and o.Circuit == circuit]


def raw_current(circuit, system=None):
    """Design current of a circuit from its items' data (a feeder: from the panel it feeds)."""
    doc = circuit.Document
    system = system or system_of(doc)
    if getattr(circuit, "Feeds", ""):
        va = demand_summary(doc, circuit.Feeds)["demand_kva"] * 1000
    else:
        va = sum(calc.item_power(p.Power, p.PowerUnit, p.Quantity, p.PowerFactor)[0] for p in circuit_points(circuit))
    return calc.current(va, circuit.Connection, system)


def panels(doc):
    """Panel names in use, in order of first appearance."""
    names = []
    for c in circuits(doc):
        for name in (c.Panel, getattr(c, "Feeds", "")):
            if name and name not in names:
                names.append(name)
    return names


def panel_circuits(doc, panel):
    return [c for c in circuits(doc) if c.Panel == panel]


def fed_panels(doc, panel, seen=None):
    """`panel` and every panel fed from it, directly or through other panels."""
    seen = seen if seen is not None else []
    if panel in seen:
        return seen
    seen.append(panel)
    for c in panel_circuits(doc, panel):
        if getattr(c, "Feeds", ""):
            fed_panels(doc, c.Feeds, seen)
    return seen


def top_panels(doc):
    """Panels no feeder comes to: each one is a service entrance (a consumer unit)."""
    fed = {getattr(c, "Feeds", "") for c in circuits(doc)}
    return [p for p in panels(doc) if p not in fed]


def panel_points(doc, panel):
    """Items supplied by `panel`, including those of the panels it feeds."""
    names = fed_panels(doc, panel)
    return [p for p in points(doc) if p.Circuit is not None and type_of(p.Circuit) == T_CIRCUIT
            and p.Circuit.Panel in names and not getattr(p.Circuit, "Feeds", "")]


def raw_phase_loads(doc, panel):
    """VA per phase of the items supplied by `panel`, from the item data (feeders excluded)."""
    totals = {p: 0.0 for p in calc.PHASES}
    for name in fed_panels(doc, panel):
        for c in panel_circuits(doc, name):
            if getattr(c, "Feeds", ""):
                continue
            va = sum(calc.item_power(p.Power, p.PowerUnit, p.Quantity, p.PowerFactor)[0] for p in circuit_points(c))
            for ph, v in calc.phase_loads(va, c.Connection, c.Phases).items():
                totals[ph] += v
    return totals


def _cv(p):
    if p.PowerUnit == "CV":
        return p.Power
    w = calc.item_power(p.Power, p.PowerUnit, 1, p.PowerFactor)[1]
    return w * calc.MOTOR_EFFICIENCY / calc.CV_W


def demand_items(doc, panel=None):
    """[(point, group)] of the loads supplied by `panel` (all loads when None)."""
    pts = panel_points(doc, panel) if panel else [p for p in points(doc) if p.Circuit is not None]
    out = []
    for p in pts:
        if not SYMBOLS[p.Symbol].kind:
            continue
        group = getattr(p, "DemandGroup", calc.AUTO_GROUP)
        if group == calc.AUTO_GROUP:
            group = calc.demand_group(p.Circuit.Kind, p.Label, p.Circuit.Description)
        out.append((p, group))
    return out


def demand_summary(doc, panel=None):
    """Installed load, demand and service entrance category of `panel` (or the whole
    installation), by the Neoenergia DIS-NOR-030 method. Computed from the item data."""
    rows, motors, phases_needed, installed_w = [], [], 1, 0.0
    for p, group in demand_items(doc, panel):
        va, w = calc.item_power(p.Power, p.PowerUnit, p.Quantity, p.PowerFactor)
        installed_w += w
        rows.append((group, max(int(p.Quantity or 1), 1), w / 1000.0, va / 1000.0))
        n = calc.phase_count(p.Circuit.Connection)
        phases_needed = max(phases_needed, n)
        if group == calc.G_MOTOR:
            motors += [(_cv(p), n)] * max(int(p.Quantity or 1), 1)
    result = calc.demand(rows)
    system = system_of(doc)
    category = calc.service_category(system, installed_w / 1000.0, result["kva"], phases_needed, motors)
    phases = category["phases"]
    connection = {1: calc.MONO, 2: calc.BI, 3: calc.TRI_N}[phases]
    amps = calc.current(result["kva"] * 1000, connection, system)
    return {"panel": panel or "", "system": system, "installed_kw": round(installed_w / 1000.0, 2),
            "demand_kva": result["kva"], "groups": result["groups"], "category": category["category"],
            "category_basis": category["basis"], "phases": phases, "connection": connection,
            "demand_current_a": round(amps, 1), "main_breaker_a": category["breaker_a"],
            "phases_needed": phases_needed,
            "note": "Demanda e categoria pela norma Neoenergia DIS-NOR-030 rev. 04 (Cosern e demais "
                    "distribuidoras Neoenergia). Outras distribuidoras têm limites próprios. Edifícios com "
                    "várias unidades: cada quadro sem alimentador é uma unidade; a entrada coletiva segue "
                    "a norma de edificações coletivas."}


def phase_totals(doc, panel=None):
    """VA per phase of the circuits of `panel`, or of the whole installation (feeders excluded,
    their loads are already in the circuits they supply)."""
    totals = {p: 0.0 for p in calc.PHASES}
    for c in circuits(doc):
        if panel and c.Panel != panel:
            continue
        if not panel and getattr(c, "Feeds", ""):
            continue
        for p, attr in zip(calc.PHASES, ("LoadR", "LoadS", "LoadT")):
            totals[p] += getattr(c, attr)
    return totals


def make_circuit(doc, number=None, description="", kind="TUG", connection=calc.MONO, phases=None,
                 panel="QD1", breaker=0, section=0, feeds="", length=0, rcd="Automático"):
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
    obj.Feeds = feeds or ""
    obj.Length = float(length or 0)
    obj.RCD = rcd or "Automático"
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
             "phases": "Phases", "panel": "Panel", "breaker": "Breaker", "section": "Section", "feeds": "Feeds",
             "length": "Length", "rcd": "RCD"}
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
        _ensure(obj, self.SPECS, record=False)

    SPECS = [("App::PropertyBool", "Automatic", "Eletroduto",
              "Circuitos e condutores deduzidos dos pontos ligados pelos eletrodutos", False, 0),
             ("App::PropertyLink", "Network", "Eletroduto",
              "Quadro de cargas (para calcular depois dos circuitos)", None, 2),
             ("App::PropertyString", "DiameterSuggested", "Cálculo",
              "Diâmetro sugerido (ocupação de até 40 %)", "", 1),
             ("App::PropertyBool", "AutoMarks", "Eletroduto",
              "Marcas onde não sobrepõem outros textos (desligado: em MarksAt)", True, 0),
             ("Part::PropertyPartShape", "Labels", "Eletroduto", "Textos das marcas", None, 2)]

    def onDocumentRestored(self, obj):
        _ensure(obj, self.SPECS)
        if list(obj.getEnumerationsOfProperty("Installation") or []) != list(INSTALLATIONS):
            value = obj.Installation  # files from before new choices were added
            obj.Installation = list(INSTALLATIONS)
            obj.Installation = value

    def onChanged(self, obj, prop):
        if prop == "Placement" and not getattr(self, "_building", False):
            _absorb_placement(obj)
        elif prop in ("Points", "Installation", "Automatic") and not _restoring(obj):
            network_changed(obj.Document)

    def groups(self, obj, fraction=None):
        """[(circuit, marks)] in the conduit: from the routing when automatic (at `fraction` of
        its length, the marks position by default), else from its Circuits list. Plus the extra
        conductors (circuit None)."""
        out = []
        if obj.Automatic:
            from . import network

            net = network.network(obj.Document)
            edge = net.edge_at(obj, obj.MarksAt if fraction is None else fraction)
            if edge is not None:
                out = net.conductors(edge)
        if not out:
            out = [(c, conductors_of(c)) for c in obj.Circuits if c is not None and type_of(c) == T_CIRCUIT]
        if obj.ExtraConductors:
            out.append((None, [c for c in obj.ExtraConductors if c in CONDUCTORS]))
        return out

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
            elif obj.Installation == "enterrado":  # dash and dot
                shapes += _dashes(a, b, 4.0 * scale, 0.8 * scale, 0.4 * scale)
            elif (b - a).Length > 1e-9:
                shapes.append(Part.LineSegment(a, b).toShape())
        from . import layout

        prims, frame, sections, _ = conduit_marks(obj, *layout.marks_of(obj))
        obj.DiameterSuggested = str(calc.suggest_conduit(sections)) if sections else ""
        if not sections and obj.Installation == "enterrado":
            conduit = entrance(obj.Document)["conduit"]
            obj.DiameterSuggested = str(conduit) if conduit else ""
        labels = None
        if prims:
            marks, labels = geometry.build_shape(prims, frame)
            shapes.append(marks)
        self._building = True
        try:
            obj.Shape = Part.makeCompound(shapes)
            if "Labels" in obj.PropertiesList:
                obj.Labels = labels if labels is not None else _empty_shape()
            obj.Placement = App.Placement()
        finally:
            self._building = False


DEFAULT_SECTION = 2.5   # mm²: sections not written on the plan (see the note on the sheet)
DEFAULT_CONDUIT = "20"  # mm: conduit diameter not written on the plan
MARK_PITCH, GROUP_GAP = 1.3, 1.2  # paper mm between conductor marks, and between circuits


def conduit_marks(obj, fraction, flip=False):
    """(primitives, frame, sections, half length in mm) of the conductor marks of a conduit,
    placed at `fraction` of its length: one tick per conductor, the circuit number on one side of
    each group (the other side when `flip`), its section on the other side when not 2,5 mm² and
    the diameter when not 20 mm."""
    up = -1 if flip else 1
    pts = list(obj.Points)
    scale = obj.Scale if obj.Scale > 0 else scale_of(obj.Document)
    at, direction = _point_along(pts, fraction)
    frame = geometry.Frame(at, geometry.angle_of(direction), scale)
    content = obj.Proxy.groups(obj, fraction)
    sections = []
    for c, marks in content:
        section = (c.Section or c.SectionSuggested) if c is not None else 1.5
        sections += [section] * len(marks)
    th = TEXT_HEIGHT
    d = frame.direction

    def along(text):  # paper mm an upright text takes along the conduit
        return (abs(d.x) * geometry.text_width(text, th) + abs(d.y) * th * 1.2) if text else 0.0

    groups = []
    for c, marks in content:
        label = str(c.Number) if c is not None else ""
        section = (c.Section or c.SectionSuggested) if c is not None else None
        text = f"{section:g}".replace(".", ",") if section and abs(section - DEFAULT_SECTION) > 1e-9 else ""
        width = max(len(marks) * MARK_PITCH, along(label) + 0.4, along(text) + 0.4)
        groups.append((label, marks, text, width))
    prims = []
    total = sum(g[3] for g in groups) + GROUP_GAP * max(len(groups) - 1, 0)
    x = -total / 2
    for label, marks, text, width in groups:
        middle = x + width / 2
        mx = middle - (len(marks) - 1) * MARK_PITCH / 2
        for mark in marks:
            for prim in CONDUCTORS[mark]:
                prims.append((prim[0], (prim[1][0] + mx, prim[1][1]), (prim[2][0] + mx, prim[2][1])))
            mx += MARK_PITCH
        if label:
            prims.append(("side", (middle, 0), label, 2.0, up))
        if text:
            prims.append(("side", (middle, 0), text, 2.0, -up))
        x += width + GROUP_GAP
    suggested = str(calc.suggest_conduit(sections)) if sections else ""
    if not content and obj.Installation == "enterrado":
        # the service entrance run, from the meter to the panel
        e = entrance(obj.Document)
        if e["section"]:
            suggested = str(e["conduit"])
            text = f"{e['cables']}#{e['section']:g}".replace(".", ",")
            prims.append(("side", (0, 0), text, 2.0, up))
            total = along(text) + 0.4
    diameter = obj.Diameter or (suggested if obj.Automatic else "")
    half = total / 2
    if diameter and diameter != DEFAULT_CONDUIT and prims:
        text = f"ø{diameter}"
        prims.append(("side", (-total / 2 - 0.6 - along(text) / 2, 0), text, 0.6, -up))
        half += 0.6 + along(text)
    return prims, frame, sections, half * scale


def _dashes(a, b, dash, gap, dot=0.0):
    """Dashes (and dots between them, when `dot`) along a-b."""
    import Part

    d = b - a
    length = d.Length
    if length < 1e-9:
        return []
    u = App.Vector(d).normalize()
    out, s = [], 0.0
    pattern = [dash, dot] if dot else [dash]
    k = 0
    while s < length:
        e = min(s + pattern[k % len(pattern)], length)
        if e - s > 1e-9:
            out.append(Part.LineSegment(a + u * s, a + u * e).toShape())
        s = e + gap
        k += 1
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


def make_conduit(doc, points, installation="teto", circuits=(), extra=(), diameter="", marks_at=None, label=None,
                 automatic=None):
    """A conduit. Automatic (the default when no circuits or extra conductors are given): its
    circuits and conductors come from the items it connects. Its conductor marks go where they
    overlap the least, or at `marks_at` (0..1 of the length) when given."""
    if automatic is None:
        automatic = not circuits and not extra
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
    obj.MarksAt = 0.5 if marks_at is None else float(marks_at)
    obj.AutoMarks = marks_at is None
    obj.Automatic = bool(automatic)
    obj.Network = schedule(doc)
    numbers = ",".join(str(c.Number) for c in obj.Circuits)
    obj.Label = label or f"Eletroduto ({installation})" + (f" circ. {numbers}" if numbers else "")
    _add(doc, obj)
    _vp(obj, "ViewProviderConduit")
    return obj


# --- load schedule ------------------------------------------------------------------------------

HEADER = ["Circuito", "Descrição", "Tipo", "Ligação", "Fases", "Itens", "Potência (VA)", "Potência (W)",
          "Corrente (A)", "Disjuntor (A)", "DR", "Seção (mm²)", "Comp. (m)", "Queda (%)", "R (VA)", "S (VA)",
          "T (VA)"]
COLUMNS = "ABCDEFGHIJKLMNOPQ"
WIDTHS = (60, 170, 80, 120, 50, 45, 90, 90, 80, 80, 60, 80, 70, 70, 70, 70, 70)  # spreadsheet column widths
TEXT_COLUMNS = "BCDEK"
ROW_PX = 16  # spreadsheet row height (px): the default 30 px makes printed tables too tall


class LoadSchedule(_Proxy):
    Type = T_SCHEDULE

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLink", "Sheet", "Quadro de cargas", "Planilha preenchida")
        self._add_circuits(obj)
        obj.addProperty("App::PropertyString", "LastCell", "Quadro de cargas", "Última célula usada")
        obj.setEditorMode("LastCell", 1)

    @staticmethod
    def _add_circuits(obj, links=()):
        # A plain link list: hidden link lists do not count as dependencies, and the schedule
        # must be recomputed after its circuits. It is only hidden from the property editor.
        obj.addProperty("App::PropertyLinkList", "Circuits", "Quadro de cargas", "Circuitos")
        obj.Circuits = list(links)
        obj.setEditorMode("Circuits", 2)

    def onDocumentRestored(self, obj):
        if obj.getTypeIdOfProperty("Circuits") == "App::PropertyLinkListHidden":  # files from before
            links = list(obj.Circuits)
            obj.removeProperty("Circuits")
            self._add_circuits(obj, links)
        if obj.LastCell and not obj.LastCell.startswith(COLUMNS[-1]):
            _MIGRATED.setdefault(obj.Document.Name, []).append(obj.Name)  # a table with older columns

    def execute(self, obj):
        sheet = obj.Sheet
        if sheet is None:
            return
        doc = obj.Document
        system = system_of(doc)
        v_ll, v_ln = calc.voltages(system)
        sheet.clearAll()
        sheet.set("A1", "QUADRO DE CARGAS")
        sheet.set("A2", f"'Sistema {system}")
        for col, title in zip(COLUMNS, HEADER):
            sheet.set(f"{col}4", title)
        last = COLUMNS[-1]
        bold = ["A1:A1", f"A4:{last}4"]
        r = 5
        names = panels(doc) or ["QD1"]
        several = len(names) > 1
        for name in names:
            rows = panel_circuits(doc, name)
            if not rows:
                continue
            if several:
                sheet.set(f"A{r}", f"'Quadro {name}")
                bold.append(f"A{r}:A{r}")
                r += 1
            for c in rows:
                feeds = f" (alimenta {c.Feeds})" if getattr(c, "Feeds", "") else ""
                values = [str(c.Number), (c.Description or c.Kind) + feeds, c.Kind, c.Connection, c.Phases,
                          f"{c.Points:g}", f"{c.PowerVA:.0f}", f"{c.PowerW:.0f}", f"{c.Current:.1f}",
                          f"{c.Breaker or c.BreakerSuggested:g}", "30 mA" if c.HasRCD else "-",
                          f"{c.Section or c.SectionSuggested:g}", f"{c.LengthUsed:.1f}" if c.LengthUsed else "-",
                          f"{c.VoltageDrop:.2f}" if c.LengthUsed else "-",
                          f"{c.LoadR:.0f}", f"{c.LoadS:.0f}", f"{c.LoadT:.0f}"]
                for col, value in zip(COLUMNS, values):
                    sheet.set(f"{col}{r}", f"'{value}" if col in TEXT_COLUMNS or value == "-" else value)
                r += 1
            totals = phase_totals(doc, name)
            sheet.set(f"A{r}", f"'TOTAL {name}" if several else "'TOTAL")
            sheet.set(f"G{r}", f"{sum(c.PowerVA for c in rows):.0f}")
            sheet.set(f"H{r}", f"{sum(c.PowerW for c in rows):.0f}")
            for col, p in zip("OPQ", calc.PHASES):
                sheet.set(f"{col}{r}", f"{totals[p]:.0f}")
            bold.append(f"A{r}:{last}{r}")
            r += 2
        totals = phase_totals(doc)
        biggest = max(totals.values()) if totals else 0
        sheet.set(f"A{r}", f"'Desequilíbrio entre fases: {calc.imbalance(totals):.1f} %   "
                           f"Corrente da fase mais carregada: {biggest / v_ln:.1f} A")
        r += 1
        for name in top_panels(doc):
            dem = demand_summary(doc, name)
            category = (f"categoria {dem['category']}, disjuntor geral {dem['main_breaker_a']} A"
                        if dem["category"] else dem["category_basis"])
            sheet.set(f"A{r}", f"'Entrada {name}: carga instalada {dem['installed_kw']:.2f} kW, demanda "
                               f"{dem['demand_kva']:.2f} kVA ({dem['demand_current_a']:.1f} A), {category}")
            r += 1
        sheet.set(f"A{r}", "'Demanda e categoria pela norma Neoenergia DIS-NOR-030 (Cosern). Queda de tensão "
                           "até o ponto mais distante; seções com fatores de agrupamento e temperatura (NBR 5410).")
        try:
            for cells in bold:
                sheet.setStyle(cells, "bold")
            for col, width in zip(COLUMNS, WIDTHS):
                sheet.setColumnWidth(col, width)
            for row in range(1, r + 1):
                sheet.setRowHeight(str(row), ROW_PX)
        except Exception:
            pass
        obj.LastCell = f"{last}{r}"
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
            "load_va": {"R": c.LoadR, "S": c.LoadS, "T": c.LoadT}, "length_m": c.LengthUsed,
            "voltage_drop_percent": c.VoltageDrop, "grouping": c.Grouping, "correction_factor": c.CorrectionFactor,
            "section_reason": c.SectionReason, "rcd": c.HasRCD, "rcd_mode": c.RCD, "rcd_reason": c.RCDReason,
            "feeds": c.Feeds, "demand_va": c.DemandVA, "warnings": list(c.Warnings)}


def load_summary(doc):
    system = system_of(doc)
    v_ll, v_ln = calc.voltages(system)
    cs = circuits(doc)
    loads = [c for c in cs if not c.Feeds]  # feeders repeat the loads of the panels they supply
    totals = phase_totals(doc)
    by_panel = {}
    for c in cs:
        panel = by_panel.setdefault(c.Panel, {"power_va": 0.0, "power_w": 0.0, "circuits": 0})
        panel["power_va"] += c.PowerVA
        panel["power_w"] += c.PowerW
        panel["circuits"] += 1
    return {"system": system, "circuits": [describe_circuit(c) for c in cs],
            "total_va": round(sum(c.PowerVA for c in loads), 1), "total_w": round(sum(c.PowerW for c in loads), 1),
            "phase_va": {p: round(v, 1) for p, v in totals.items()},
            "phase_current_a": {p: round(v / v_ln, 2) for p, v in totals.items()},
            "imbalance_percent": round(calc.imbalance(totals), 1), "panels": by_panel,
            "service_entrances": [demand_summary(doc, name) for name in top_panels(doc)],
            "floors": floors(doc),
            "unassigned_points": [p.Label for p in points(doc) if p.Circuit is None and SYMBOLS[p.Symbol].kind]}


def floors(doc):
    """[(elevation mm, name)] of the floors with electrical items: the distinct elevations of
    items and conduits, named after the BIM level at that height when there is one."""
    levels = [(o.Placement.Base.z, o.Label) for o in doc.Objects
              if type_of(o) == "BuildingPart" and getattr(o, "IfcType", "") in ("Building Storey", "Floor")]
    zs = []
    for z in [p.Position.z for p in points(doc)] + [v.z for c in conduits(doc) for v in c.Points]:
        if not any(abs(z - other) < FLOOR_TOLERANCE for other in zs):
            zs.append(z)
    out = []
    for z in sorted(zs):
        name = next((label for lz, label in levels if abs(lz - z) < FLOOR_TOLERANCE), None)
        out.append((round(z, 1), name or f"Pavimento {z / 1000:+.2f} m".replace(".", ",")))
    return out


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
            elif kind != T_CONDUIT:
                return
            network_changed(obj.Document)  # routing, feeders and outputs
            sched = schedule(obj.Document, create=False)
            if sched is not None:
                sched.touch()
            _later(obj.Document)
        except Exception:
            pass

    def slotBeforeRecomputeDocument(self, doc):
        """A file saved by an older version got new properties: compute them on its first
        recompute (marks set while restoring are cleared when the restore ends)."""
        if doc.Name not in _MIGRATED:
            return
        names = _MIGRATED.pop(doc.Name, [])
        for name in names:
            obj = doc.getObject(name)
            if obj is not None:
                obj.touch()
        if names:
            network_changed(doc)


_MIGRATED = {}  # document name -> objects that got new properties while restoring


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
