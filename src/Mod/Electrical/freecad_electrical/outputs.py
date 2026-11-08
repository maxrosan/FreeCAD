# SPDX-License-Identifier: LGPL-2.1-or-later

"""Documents generated from the installation, kept up to date like the load schedule:

* the bill of materials ("lista de materiais"): cables per section and function, conduits,
  boxes, devices, breakers, RCDs, surge protectors and panels, in a spreadsheet;
* the single-line diagram ("diagrama unifilar") of each panel, an SVG drawn on a TechDraw page.

Both objects link the load schedule, so they are recomputed after the circuits, and are touched
when items or conduits change (model.network_changed).
"""

import math
from xml.sax.saxutils import escape

import FreeCAD as App

from . import calc, model, network
from .symbols import SYMBOLS

WASTE = 0.10  # cable and conduit lengths get 10 % extra for joints and losses
FUNCTION_NAMES = {"fase": "fase", "neutro": "neutro (azul-claro)", "terra": "proteção PE (verde-amarelo)",
                  "retorno": "retorno"}


# --- bill of materials -------------------------------------------------------------------------------


def _section(c):
    return (c.Section or c.SectionSuggested) if c is not None else 1.5


def _num(value):
    return f"{value:g}".replace(".", ",")


def bill_of_materials(doc, waste=WASTE):
    """[(group, item, unit, quantity)] of the installation, and notes."""
    net = network.network(doc)
    system = model.system_of(doc)
    v_ll, v_ln = calc.voltages(system)
    cables = {}
    conduits = {}
    notes = []

    def add_cable(c, marks, length_mm):
        for mark in marks:
            key = (_section(c), mark)
            cables[key] = cables.get(key, 0.0) + length_mm

    for obj in model.conduits(doc):
        if obj.Automatic:
            for e in net.conduit_edges.get(obj.Name, []):
                for c, marks in net.conductors(e):
                    add_cable(c, marks, net.edges[e]["length"])
            extra = [(None, [m for m in obj.ExtraConductors])] if obj.ExtraConductors else []
        else:
            extra = obj.Proxy.groups(obj)
        length = sum((b - a).Length for a, b in zip(obj.Points, obj.Points[1:]))
        for c, marks in extra:
            add_cable(c, marks, length)
        size = obj.Diameter or obj.DiameterSuggested or "20"
        key = (f"Eletroduto PVC ø{size} mm", obj.Installation)
        conduits[key] = conduits.get(key, 0.0) + length
    for e, ed in enumerate(net.edges):  # riser shafts between floors
        if ed["installation"] == "prumada":
            sections = []
            for c, marks in net.conductors(e):
                add_cable(c, marks, ed["length"])
                sections += [_section(c)] * len(marks)
            key = (f"Eletroduto PVC ø{calc.suggest_conduit(sections)} mm", "prumada")
            conduits[key] = conduits.get(key, 0.0) + ed["length"]
    drops = {}
    for p, c, marks, length in net.vertical_runs():
        add_cable(c, marks, length)
        d = drops.setdefault(p.Name, [length, []])
        d[1] += [_section(c)] * len(marks)
    for length, sections in drops.values():
        key = (f"Eletroduto PVC ø{calc.suggest_conduit(sections)} mm", "descidas")
        conduits[key] = conduits.get(key, 0.0) + length
    entrances = []
    buried = sum(ed["length"] for ed in net.edges if ed["installation"] == "enterrado")
    for p in model.points(doc):  # service entrance: from the meter to the panel, buried
        if p.Symbol != model.ENTRANCE:
            continue
        e = model.entrance(doc, p)
        entrances.append(e)
        if not e["section"] or not buried:
            continue
        panel = next((q for q in net.panel_points() if e["panel"] and e["panel"].lower() in
                      (q.Label.lower(), (q.Letter or "").lower())), None)
        length = buried + 2 * network.BURIED_MM + float(p.Height.Value) + (float(panel.Height.Value) if panel else 0.0)
        neutral = 1 if e["connection"] in (calc.MONO, calc.TRI_N) else 0
        for mark, n in (("fase", calc.phase_count(e["connection"])), ("neutro", neutral), ("terra", 1)):
            if n:
                key = (e["section"], mark)
                cables[key] = cables.get(key, 0.0) + n * length
    if net.unrouted:
        notes.append(f"{len(net.unrouted)} item(ns) sem eletroduto até o quadro: seus cabos não estão na lista.")

    rows = []
    for (section, mark), length in sorted(cables.items()):
        metres = math.ceil(length / 1000.0 * (1 + waste))
        if metres:
            rows.append(("Condutores", f"Cabo flexível de cobre 750 V {_num(section)} mm² - {FUNCTION_NAMES[mark]}",
                         "m", metres))
    for (item, where), length in sorted(conduits.items()):
        metres = math.ceil(length / 1000.0 * (1 + waste))
        if metres:
            rows.append(("Eletrodutos", f"{item} ({where})", "m", metres))

    boxes, devices = {}, {}

    def count(table, key, n=1):
        table[key] = table.get(key, 0) + n

    for p in model.points(doc):
        sym = SYMBOLS[p.Symbol]
        quantity = max(int(p.Quantity or 1), 1)
        if p.Symbol in network.PANELS or p.Symbol in network.RISERS or p.Symbol == model.ENTRANCE:
            continue
        if p.Symbol == "tomada_piso":
            count(boxes, "Caixa de piso 4x4")
        elif p.Symbol in ("caixa_passagem", "motor"):
            count(boxes, "Caixa de passagem 4x4")
        elif float(p.Height.Value) > 0 or sym.wall:
            count(boxes, "Caixa 4x2 de embutir")
        else:
            count(boxes, "Caixa octogonal 4x4 (teto)")
        if p.Symbol.startswith("tomada"):
            va_each = calc.item_power(p.Power, p.PowerUnit, 1, p.PowerFactor)[0]
            volts = v_ll if p.Circuit is not None and p.Circuit.Connection == calc.BI else v_ln
            amps = 20 if va_each / volts > 10 else 10
            count(devices, f"Tomada 2P+T {amps} A (NBR 14136)", quantity)
        elif network.is_switch(p.Symbol):
            count(devices, sym.label)
        elif sym.kind == "Iluminação":
            count(devices, f"{sym.label} - {calc.power_text(p.Power, p.PowerUnit)} (lâmpada/luminária)", quantity)
        elif p.Symbol == "motor":
            count(devices, f"Ponto de força para motor de {calc.power_text(p.Power, p.PowerUnit)}", quantity)
        elif p.Symbol == "botao_campainha":
            count(devices, "Botão de campainha")
    rows += [("Caixas", item, "pç", n) for item, n in sorted(boxes.items())]
    for e in entrances:
        if e["category"]:
            rows.append(("Entrada", f"Padrão de entrada {e['category']} Neoenergia (poste, caixa de medição e "
                                    f"disjuntor {e['breaker_a']} A) - quadro {e['panel']}", "cj", 1))
    rows += [("Dispositivos", item, "pç", n) for item, n in sorted(devices.items())]

    protection, ways = {}, {}
    for c in model.circuits(doc):
        poles = calc.phase_count(c.Connection)
        breaker = int(c.Breaker or c.BreakerSuggested)
        count(protection, f"Disjuntor termomagnético {poles}P {breaker} A curva C")
        ways[c.Panel] = ways.get(c.Panel, 0) + poles
        if c.HasRCD:
            rcd_poles = 2 if c.Connection in (calc.MONO, calc.BI) else 4
            count(protection, f"Interruptor diferencial residual (DR) {rcd_poles}P {calc.rcd_rating(breaker)} A 30 mA")
            ways[c.Panel] += rcd_poles
    for name in model.top_panels(doc):
        dem = model.demand_summary(doc, name)
        phases = dem["phases"]
        if dem["main_breaker_a"]:
            count(protection, f"Disjuntor geral {phases}P {dem['main_breaker_a']} A (entrada {name}, categoria "
                              f"{dem['category']})")
        count(protection, "DPS classe II 275 V 20 kA", phases + 1)
        ways[name] = ways.get(name, 0) + phases + phases + 1
    rows += [("Proteção", item, "pç", n) for item, n in sorted(protection.items())]
    for name in model.panels(doc):
        n = len(model.panel_circuits(doc, name))
        spare = calc.spare_ways(n)
        rows.append(("Quadros", f"Quadro de distribuição {name} para {ways.get(name, 0) + spare} módulos DIN "
                                f"({n} circuitos, {spare} reservas pela NBR 5410)", "pç", 1))
    notes.append(f"Comprimentos com {waste * 100:.0f} % de sobra; cabos pelo traçado dos eletrodutos e pelas descidas "
                 f"até os pontos (pé-direito {network.ceiling_of(doc) / 1000:.2f} m). Luminárias e lâmpadas: "
                 f"conferir o modelo.")
    return rows, notes


class Materials(model._Proxy):
    Type = model.T_MATERIALS

    def __init__(self, obj):
        obj.Proxy = self
        g = "Lista de materiais"
        obj.addProperty("App::PropertyLink", "Sheet", g, "Planilha preenchida")
        obj.addProperty("App::PropertyLink", "Schedule", g, "Quadro de cargas (calcula depois dos circuitos)")
        obj.setEditorMode("Schedule", 2)
        obj.addProperty("App::PropertyFloat", "Waste", g, "Sobra de cabos e eletrodutos (%)").Waste = WASTE * 100
        obj.addProperty("App::PropertyString", "LastCell", g, "Última célula usada")
        obj.setEditorMode("LastCell", 1)

    WIDTHS = (150, 900, 60, 80)

    def execute(self, obj):
        sheet = obj.Sheet
        if sheet is None:
            return
        rows, notes = bill_of_materials(obj.Document, obj.Waste / 100.0)
        sheet.clearAll()
        sheet.set("A1", "LISTA DE MATERIAIS")
        for col, title in zip("ABCD", ("Grupo", "Item", "Unid.", "Quant.")):
            sheet.set(f"{col}3", title)
        r = 4
        last_group = None
        for group, item, unit, quantity in rows:
            sheet.set(f"A{r}", f"'{group}" if group != last_group else "")
            sheet.set(f"B{r}", f"'{item}")
            sheet.set(f"C{r}", f"'{unit}")
            sheet.set(f"D{r}", f"{quantity}")
            last_group = group
            r += 1
        r += 1
        for note in notes:
            sheet.set(f"A{r}", f"'{note}")
            r += 1
        try:
            sheet.setStyle("A1:A1", "bold")
            sheet.setStyle("A3:D3", "bold")
            for col, width in zip("ABCD", self.WIDTHS):
                sheet.setColumnWidth(col, width)
            for row in range(1, r + 1):
                sheet.setRowHeight(str(row), model.ROW_PX)
        except Exception:
            pass
        obj.LastCell = f"D{r - 1}"
        sheet.recompute()
        sheet.purgeTouched()


def materials(doc, create=True):
    for obj in model.items(doc, model.T_MATERIALS):
        return obj
    if not create:
        return None
    sheet = doc.addObject("Spreadsheet::Sheet", "TabelaMateriais")
    sheet.Label = "Tabela da lista de materiais"
    obj = doc.addObject("App::FeaturePython", "ListaDeMateriais")
    Materials(obj)
    obj.Label = "Lista de materiais"
    obj.Sheet = sheet
    obj.Schedule = model.schedule(doc)
    inst = model.installation(doc)
    inst.addObject(obj)
    inst.addObject(sheet)
    model._vp(obj, "ViewProviderOutput")
    return obj


# --- single-line diagram --------------------------------------------------------------------------------

ROW = 9.0
TEXT = 2.5
COLUMNS = [(100, "Circ."), (112, "Descrição"), (176, "Seção"), (194, "Disj."), (208, "Fases"), (222, "Potência"),
           (244, "Queda")]
WIDTH = 262.0


class _Svg:
    def __init__(self):
        self.parts = []

    def line(self, x1, y1, x2, y2, width=0.35):
        self.parts.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="black" '
                          f'stroke-width="{width}" stroke-linecap="round"/>')

    def rect(self, x, y, w, h, width=0.35):
        self.parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="none" '
                          f'stroke="black" stroke-width="{width}"/>')

    def text(self, x, y, value, size=TEXT, anchor="start", bold=False):
        weight = ' font-weight="bold"' if bold else ""
        self.parts.append(f'<text x="{x:.2f}" y="{y:.2f}" font-family="osifont" font-size="{size}" '
                          f'text-anchor="{anchor}"{weight} fill="black">{escape(str(value))}</text>')

    def breaker(self, x, y, poles, vertical=False):
        """Circuit breaker (IEC 60617): an open contact with a cross at the fixed contact, and one
        stroke per pole on the incoming line. Horizontal from (x, y) to (x + 20, y)."""
        if vertical:
            self.line(x, y, x, y + 3)
            self.line(x, y + 3, x - 3.5, y + 10)
            self.line(x - 1.2, y + 10.8, x + 1.2, y + 13.2)
            self.line(x - 1.2, y + 13.2, x + 1.2, y + 10.8)
            self.line(x, y + 12, x, y + 15)
            for k in range(poles):
                yy = y + 1.5 - (poles - 1) * 0.7 + k * 1.4
                self.line(x - 1.2, yy + 0.8, x + 1.2, yy - 0.8)
            return
        self.line(x, y, x + 10, y)
        for k in range(poles):
            xx = x + 5 - (poles - 1) * 0.7 + k * 1.4
            self.line(xx - 0.8, y + 1.2, xx + 0.8, y - 1.2)
        self.line(x + 10, y, x + 18, y - 3.5)
        self.line(x + 18.8, y - 1.2, x + 21.2, y + 1.2)
        self.line(x + 18.8, y + 1.2, x + 21.2, y - 1.2)

    def earth(self, x, y):
        for k, half in enumerate((2.5, 1.6, 0.7)):
            self.line(x - half, y + k * 1.1, x + half, y + k * 1.1)


def unifilar_svg(doc):
    """SVG (mm) of the single-line diagram of every panel; returns (svg, width, height)."""
    svg = _Svg()
    system = model.system_of(doc)
    names = model.panels(doc)
    top = model.top_panels(doc)
    y = 4.0
    bus_x = 40.0
    for name in names:
        rows = model.panel_circuits(doc, name)
        svg.text(6, y + 4, f"QUADRO {name}", 3.5, bold=True)
        y0 = y + 8
        if name in top:
            dem = model.demand_summary(doc, name)
            phases = dem["phases"]
            svg.line(bus_x, y0, bus_x, y0 + 4)
            category = f"categoria {dem['category']}" if dem["category"] else "fora das categorias de BT"
            svg.text(bus_x + 8, y0 + 3, f"Ramal de entrada {dem['connection']} {system} - {category} "
                                        f"(DIS-NOR-030) - demanda {_num(round(dem['demand_kva'], 2))} kVA")
            svg.rect(bus_x - 5, y0 + 4, 10, 7)
            svg.text(bus_x, y0 + 8.6, "kWh", 2.2, "middle")
            svg.line(bus_x, y0 + 11, bus_x, y0 + 13)
            svg.breaker(bus_x, y0 + 13, phases, vertical=True)
            if dem["main_breaker_a"]:
                svg.text(bus_x + 5, y0 + 21, f"Disjuntor geral {phases}P {dem['main_breaker_a']} A")
            svg.line(bus_x, y0 + 28, bus_x, y0 + 34)
            svg.line(bus_x, y0 + 31, bus_x - 12, y0 + 31)  # surge protectors to earth
            svg.line(bus_x - 12, y0 + 31, bus_x - 12, y0 + 33)
            svg.rect(bus_x - 15, y0 + 33, 6, 5)
            svg.text(bus_x - 12, y0 + 36.6, "DPS", 1.8, "middle")
            svg.line(bus_x - 12, y0 + 38, bus_x - 12, y0 + 40)
            svg.earth(bus_x - 12, y0 + 40)
            svg.text(bus_x - 17, y0 + 36.6, "Classe II", 1.8, "end")
            bus_top = y0 + 34
        else:
            feeder = next((c for c in model.circuits(doc) if c.Feeds == name), None)
            svg.line(bus_x, y0, bus_x, y0 + 34)
            if feeder is not None:
                svg.text(bus_x + 8, y0 + 12, f"Alimentado pelo circuito {feeder.Number} do {feeder.Panel}: "
                                             f"{feeder.Connection}, {_num(feeder.Section or feeder.SectionSuggested)} mm², "
                                             f"disjuntor {int(feeder.Breaker or feeder.BreakerSuggested)} A")
            bus_top = y0 + 34
        header_y = bus_top + 3
        for x, title in COLUMNS:
            svg.text(x, header_y, title, 2.2, bold=True)
        first = bus_top + 9
        bus_bottom = first + ROW * max(len(rows) - 1, 0) + 3
        svg.line(bus_x, bus_top, bus_x, bus_bottom, 1.0)
        for i, c in enumerate(rows):
            yy = first + i * ROW
            breaker = int(c.Breaker or c.BreakerSuggested)
            svg.breaker(bus_x, yy, calc.phase_count(c.Connection))
            svg.text(bus_x + 15, yy - 4.3, f"{breaker} A", 2.0, "middle")
            x = bus_x + 21
            if c.HasRCD:
                svg.line(x, yy, x + 5, yy)
                svg.rect(x + 5, yy - 3, 15, 6)
                svg.text(x + 12.5, yy + 0.7, "DR 30 mA", 1.8, "middle")
                x += 20
            svg.line(x, yy, 96, yy)
            svg.parts.append(f'<circle cx="96.6" cy="{yy:.2f}" r="0.6" fill="none" stroke="black" stroke-width="0.35"/>')
            description = (c.Description or c.Kind) + (f" (alimenta {c.Feeds})" if c.Feeds else "")
            if len(description) > 30:
                description = description[:29] + "…"
            drop = f"{_num(round(c.VoltageDrop, 1))} %" if c.LengthUsed else "-"
            values = [str(c.Number), description, f"{_num(c.Section or c.SectionSuggested)} mm²", f"{breaker} A",
                      c.Phases, f"{c.PowerVA:.0f} VA", drop]
            for (cx, _), value in zip(COLUMNS, values):
                svg.text(cx, yy + 0.9, value)
        y = bus_bottom + 10
    notes = ["Disjuntores termomagnéticos curva C; DR de 30 mA onde a NBR 5410 exige (5.1.3.2.2).",
             "Seções com fatores de agrupamento e temperatura e queda de tensão máxima "
             f"de {_num(getattr(model.installation(doc), 'MaxVoltageDrop', 4.0))} % (NBR 5410). "
             "Conferir com o responsável técnico."]
    for note in notes:
        svg.text(6, y, note, 2.2)
        y += 4
    height = y + 2
    body = "\n".join(svg.parts)
    text = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}mm" height="{height:.1f}mm" '
            f'viewBox="0 0 {WIDTH} {height:.1f}">\n{body}\n</svg>')
    return text, WIDTH, height


class Diagram(model._Proxy):
    Type = model.T_DIAGRAM

    def __init__(self, obj):
        obj.Proxy = self
        g = "Diagrama unifilar"
        obj.addProperty("App::PropertyLink", "View", g, "Vista da folha que mostra o diagrama")
        obj.addProperty("App::PropertyLink", "Schedule", g, "Quadro de cargas (calcula depois dos circuitos)")
        obj.setEditorMode("Schedule", 2)
        obj.addProperty("App::PropertyFloat", "Width", g, "Largura do diagrama (mm)")
        obj.addProperty("App::PropertyFloat", "Height", g, "Altura do diagrama (mm)")
        _readonly = ("Width", "Height")
        for prop in _readonly:
            obj.setEditorMode(prop, 1)

    def execute(self, obj):
        svg, width, height = unifilar_svg(obj.Document)
        obj.Width, obj.Height = width, height
        view = obj.View
        if view is None:
            return
        if view.Symbol != svg:
            view.Symbol = svg
        fit_view(view, width, height)
        view.recompute()
        view.purgeTouched()


def fit_view(view, width, height):
    """Scale and centre a view in the free area of its page."""
    from . import sheets

    page = next((o for o in view.InList if o.TypeId == "TechDraw::DrawPage"), None)
    if page is None:
        return
    size = page.Template.Width.Value, page.Template.Height.Value
    x0, y0, x1, y1 = sheets.drawing_area(size)
    scale = min(1.0, (x1 - x0) / width, (y1 - y0) / height)
    if view.ScaleType != "Custom":
        view.ScaleType = "Custom"
    if abs(view.Scale - scale) > 1e-6:
        view.Scale = scale
    view.X, view.Y = (x0 + x1) / 2, (y0 + y1) / 2


def diagram(doc, create=True):
    for obj in model.items(doc, model.T_DIAGRAM):
        return obj
    if not create:
        return None
    obj = doc.addObject("App::FeaturePython", "DiagramaUnifilar")
    Diagram(obj)
    obj.Label = "Diagrama unifilar"
    obj.Schedule = model.schedule(doc)
    model.installation(doc).addObject(obj)
    model._vp(obj, "ViewProviderOutput")
    return obj
