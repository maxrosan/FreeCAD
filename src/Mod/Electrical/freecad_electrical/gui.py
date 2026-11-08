# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI of the Electrical workbench: view providers, data panels and commands."""

import json
import os

import FreeCAD as App
import FreeCADGui as Gui

from . import calc, checks, geometry, model, sheets
from .symbols import CATEGORIES, SYMBOLS

PARAMS = "User parameter:BaseApp/Preferences/Mod/Electrical"
BLACK = (0.0, 0.0, 0.0)
WALL_SEARCH_MM = 400.0  # how far from the click a wall face is looked for


# --- icons ------------------------------------------------------------------------------------

EXTRA_ICONS = {
    "circuito": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><path d="M8 32h12l6-14 12 28 6-14h12" '
                'fill="none" stroke="#1565c0" stroke-width="5"/></svg>',
    "eletroduto": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><path d="M6 44 L58 20" stroke="black" '
                  'stroke-width="4"/><path d="M26 26v18M32 24v18M38 21v18M38 21h-5" stroke="black" stroke-width="3"/></svg>',
    "quadro_cargas": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect x="6" y="8" width="52" '
                     'height="48" fill="white" stroke="black" stroke-width="3"/><path d="M6 20h52M6 32h52M6 44h52M24 '
                     '8v48" stroke="black" stroke-width="2"/></svg>',
    "folha": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect x="8" y="10" width="48" height="44" '
             'fill="white" stroke="black" stroke-width="3"/><rect x="34" y="42" width="22" height="12" fill="#ccc" '
             'stroke="black" stroke-width="2"/><path d="M16 18h20v16H16z" fill="none" stroke="black" stroke-width="2"/></svg>',
    "conferir": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><path d="M12 34l14 14 26-30" fill="none" '
                'stroke="#2e7d32" stroke-width="8"/></svg>',
    "fases": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect x="8" y="30" width="12" height="26" '
             'fill="#c62828"/><rect x="26" y="22" width="12" height="34" fill="#f9a825"/><rect x="44" y="26" width="12" '
             'height="30" fill="#1565c0"/><path d="M6 18h52" stroke="black" stroke-width="3" stroke-dasharray="6 4"/></svg>',
    "configurar": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><circle cx="32" cy="32" r="12" '
                  'fill="none" stroke="black" stroke-width="6"/><path d="M32 6v10M32 48v10M6 32h10M48 32h10" '
                  'stroke="black" stroke-width="6"/><text x="32" y="38" font-size="14" text-anchor="middle">V</text></svg>',
    "unifilar": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><path d="M14 6v52" stroke="black" '
                'stroke-width="6"/><path d="M14 18h12l10-7M38 18h20M14 34h12l10-7M38 34h20M14 50h12l10-7M38 50h20" '
                'fill="none" stroke="black" stroke-width="3"/></svg>',
    "materiais": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect x="10" y="6" width="44" '
                 'height="52" fill="white" stroke="black" stroke-width="3"/><path d="M18 18h28M18 28h28M18 38h28M18 48h18" '
                 'stroke="black" stroke-width="3"/></svg>',
    "demanda": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><path d="M8 54h48" stroke="black" '
               'stroke-width="3"/><rect x="12" y="34" width="10" height="20" fill="#1565c0"/><rect x="27" y="22" '
               'width="10" height="32" fill="#1565c0"/><rect x="42" y="10" width="10" height="44" fill="#c62828"/></svg>',
    "instalacao": '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><path d="M36 4L14 36h16l-4 24 24-34H34z" '
                  'fill="#f9a825" stroke="black" stroke-width="2"/></svg>',
}


def icon(name):
    """Path of the icon `name` (a symbol key or an EXTRA_ICONS key), written on first use."""
    folder = os.path.join(App.getUserCachePath(), "Electrical", "icons")
    path = os.path.join(folder, f"{name}.svg")
    if not os.path.isfile(path):
        os.makedirs(folder, exist_ok=True)
        svg = EXTRA_ICONS.get(name)
        if svg is None and name in SYMBOLS:
            svg = geometry.svg_icon(SYMBOLS[name].build({}))
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg or EXTRA_ICONS["instalacao"])
    return path


# --- view providers ---------------------------------------------------------------------------


class _ViewProvider:
    icon_name = "instalacao"

    def __init__(self, vobj):
        vobj.Proxy = self

    def attach(self, vobj):
        self.Object = vobj.Object

    def getIcon(self):
        return icon(self.icon_name)

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps

    def __setstate__(self, state):
        return None


class ViewProviderInstallation(_ViewProvider):
    icon_name = "instalacao"

    def doubleClicked(self, vobj):
        InstallationDialog(App.ActiveDocument).exec()
        return True


def _black(vobj):
    if hasattr(vobj, "LineColor"):
        vobj.LineColor = BLACK
    if hasattr(vobj, "ShapeAppearance"):
        material = App.Material()
        material.DiffuseColor = BLACK
        vobj.ShapeAppearance = (material,)
    if hasattr(vobj, "LineWidth"):
        vobj.LineWidth = 1.5
    if hasattr(vobj, "PointSize"):
        vobj.PointSize = 1


class ViewProviderPoint(_ViewProvider):
    def __init__(self, vobj):
        super().__init__(vobj)
        _black(vobj)

    def getIcon(self):
        obj = getattr(self, "Object", None)
        return icon(obj.Symbol if obj is not None else "tomada_baixa")

    def doubleClicked(self, vobj):
        PointDialog(vobj.Object).exec()
        return True


class ViewProviderCircuit(_ViewProvider):
    icon_name = "circuito"

    def doubleClicked(self, vobj):
        CircuitDialog(vobj.Object.Document, vobj.Object).exec()
        return True


class ViewProviderConduit(_ViewProvider):
    icon_name = "eletroduto"

    def __init__(self, vobj):
        super().__init__(vobj)
        _black(vobj)

    def doubleClicked(self, vobj):
        ConduitDialog(vobj.Object.Document, vobj.Object).exec()
        return True


class ViewProviderSchedule(_ViewProvider):
    icon_name = "quadro_cargas"

    def doubleClicked(self, vobj):
        _open_sheet(vobj.Object)
        return True


class ViewProviderOutput(_ViewProvider):
    """Bill of materials (opens its table) and single-line diagram (opens its page)."""

    def getIcon(self):
        obj = getattr(self, "Object", None)
        return icon("materiais" if obj is not None and model.type_of(obj) == model.T_MATERIALS else "unifilar")

    def doubleClicked(self, vobj):
        obj = vobj.Object
        if model.type_of(obj) == model.T_MATERIALS:
            _open_sheet(obj)
        elif obj.View is not None:
            page = next((o for o in obj.View.InList if o.TypeId == "TechDraw::DrawPage"), None)
            if page is not None:
                page.ViewObject.show()
        return True


def _open_sheet(sched):
    sched.Document.recompute()
    Gui.ActiveDocument.setEdit(sched.Sheet.Name)


# --- remembered values ------------------------------------------------------------------------


def load_last(symbol):
    sym = SYMBOLS[symbol]
    default = {"circuit": "", "power": sym.power, "unit": sym.unit, "quantity": 1, "power_factor": model.DEFAULT_PF,
               "letter": "", "height": sym.height, "room": "", "rotation": 0.0, "demand_group": calc.AUTO_GROUP}
    text = App.ParamGet(f"{PARAMS}/Last").GetString(symbol, "")
    if text:
        try:
            default.update(json.loads(text))
        except ValueError:
            pass
    return default


def save_last(symbol, values):
    App.ParamGet(f"{PARAMS}/Last").SetString(symbol, json.dumps(values))


# --- data panel of a point ------------------------------------------------------------------------


def _circuit_choices(doc):
    return ["(nenhum)"] + [f"{c.Number} - {c.Description or c.Kind} ({c.Connection}, {c.Phases})"
                           for c in model.circuits(doc)] + ["Novo circuito"]


def _parse_circuit(text, doc):
    text = (text or "").strip()
    if not text or text.startswith("("):
        return None
    if text.startswith("Novo"):
        return max((c.Number for c in model.circuits(doc)), default=0) + 1
    digits = text.split(" ")[0]
    return int(digits) if digits.isdigit() else None


class PointForm:
    """Fields of an electrical item. `data` is kept current on every change, so it can be read
    after the Draft task panel that hosts the widget has closed."""

    def __init__(self, doc, symbol, values):
        from PySide import QtWidgets

        self.doc, self.symbol = doc, symbol
        sym = SYMBOLS[symbol]
        self.data = dict(values)
        self.widget = QtWidgets.QWidget()
        self.widget.setWindowTitle("Dados do item")
        form = QtWidgets.QFormLayout(self.widget)
        self.circuit = QtWidgets.QComboBox()
        self.circuit.setEditable(True)
        self.circuit.addItems(_circuit_choices(doc))
        self.circuit.setEditText(str(values.get("circuit") or "(nenhum)"))
        form.addRow("Circuito", self.circuit)
        if sym.kind:
            self.power = QtWidgets.QDoubleSpinBox()
            self.power.setRange(0, 1e6)
            self.power.setDecimals(1)
            self.power.setValue(float(values.get("power", sym.power)))
            self.unit = QtWidgets.QComboBox()
            self.unit.addItems(model.UNITS)
            self.unit.setCurrentText(values.get("unit", sym.unit))
            row = QtWidgets.QHBoxLayout()
            row.addWidget(self.power)
            row.addWidget(self.unit)
            form.addRow("Potência", row)
            self.quantity = QtWidgets.QSpinBox()
            self.quantity.setRange(1, 100)
            self.quantity.setValue(int(values.get("quantity", 1)))
            form.addRow("Quantidade", self.quantity)
            self.pf = QtWidgets.QDoubleSpinBox()
            self.pf.setRange(0.1, 1.0)
            self.pf.setSingleStep(0.01)
            self.pf.setValue(float(values.get("power_factor", model.DEFAULT_PF)))
            form.addRow("Fator de potência", self.pf)
            self.group = QtWidgets.QComboBox()
            self.group.addItems([calc.AUTO_GROUP] + calc.DEMAND_GROUPS)
            self.group.setCurrentText(values.get("demand_group", calc.AUTO_GROUP))
            self.group.setToolTip("Grupo do cálculo de demanda (norma da distribuidora)")
            form.addRow("Demanda", self.group)
        else:
            self.power = self.unit = self.quantity = self.pf = self.group = None
        self.letter = QtWidgets.QLineEdit(values.get("letter", ""))
        form.addRow("Letra do comando", self.letter)
        self.height = QtWidgets.QDoubleSpinBox()
        self.height.setRange(0, 10000)
        self.height.setSuffix(" mm")
        self.height.setValue(float(values.get("height", sym.height)))
        form.addRow("Altura do piso", self.height)
        self.room = QtWidgets.QLineEdit(values.get("room", ""))
        self.room.setPlaceholderText("pela posição")
        form.addRow("Cômodo", self.room)
        self.rotation = QtWidgets.QDoubleSpinBox()
        self.rotation.setRange(-360, 360)
        self.rotation.setSuffix(" °")
        self.rotation.setValue(float(values.get("rotation", 0.0)))
        form.addRow("Rotação", self.rotation)
        if sym.wall:
            form.addRow(QtWidgets.QLabel("Clique no cômodo, junto à parede: o item vai\n"
                                         "para a face da parede, virado para o cômodo."))
        for widget in (self.circuit, self.unit, self.group):
            if widget is not None:
                widget.currentTextChanged.connect(self._update)
        self.circuit.editTextChanged.connect(self._update)
        for widget in (self.power, self.quantity, self.pf, self.height, self.rotation):
            if widget is not None:
                widget.valueChanged.connect(self._update)
        for widget in (self.letter, self.room):
            widget.textChanged.connect(self._update)
        self._update()

    def _update(self, *args):
        d = self.data
        d["circuit"] = self.circuit.currentText()
        if self.power is not None:
            d["power"], d["unit"] = self.power.value(), self.unit.currentText()
            d["quantity"], d["power_factor"] = self.quantity.value(), self.pf.value()
            d["demand_group"] = self.group.currentText()
        d["letter"], d["room"] = self.letter.text().strip(), self.room.text().strip()
        d["height"], d["rotation"] = self.height.value(), self.rotation.value()

    def kwargs(self):
        d = self.data
        out = {"circuit": _parse_circuit(d.get("circuit"), self.doc), "letter": d.get("letter", ""),
               "height": d.get("height"), "room": d.get("room", "")}
        if SYMBOLS[self.symbol].kind:
            out.update(power=d.get("power"), unit=d.get("unit"), quantity=d.get("quantity"),
                       power_factor=d.get("power_factor"), demand_group=d.get("demand_group") or calc.AUTO_GROUP)
        return out


# --- placing items by hand -------------------------------------------------------------------------


def _ensure_draft_gui():
    """The Draft snapper and task bar (Gui.Snapper, Gui.draftToolBar)."""
    if not hasattr(Gui, "Snapper") or not hasattr(Gui, "draftToolBar"):
        import DraftTools  # noqa: F401


def nearest_wall(doc, point, limit=WALL_SEARCH_MM):
    """(foot, direction angle into the room) of the closest straight edge to `point`, or None.

    The item goes on the wall face (the foot of the perpendicular) and points from the wall
    toward the click, i.e. into the room the person clicked in.
    """
    import Part

    vertex = Part.Vertex(point)
    best = None
    for obj in doc.Objects:
        if model.type_of(obj) in (model.T_POINT, model.T_CONDUIT) or obj.TypeId.startswith("TechDraw::"):
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull() or not shape.Edges:
            continue
        if App.GuiUp and obj.ViewObject is not None and not obj.ViewObject.Visibility:
            continue
        if shape.BoundBox.DiagonalLength > 0 and not shape.BoundBox.isInside(point) \
                and shape.distToShape(vertex)[0] > limit:
            continue
        for edge in shape.Edges:
            if not isinstance(edge.Curve, Part.Line):
                continue
            dist, pairs, _ = edge.distToShape(vertex)
            if dist <= limit and (best is None or dist < best[0]):
                best = (dist, pairs[0][0], edge)
    if best is None:
        return None
    dist, foot, edge = best
    foot = App.Vector(foot.x, foot.y, point.z)
    if dist > 1e-3:
        direction = point - foot
    else:  # clicked on the wall line itself: perpendicular, toward +left of the edge
        d = edge.Vertexes[-1].Point - edge.Vertexes[0].Point
        direction = App.Vector(-d.y, d.x, 0)
    return foot, geometry.angle_of(direction)


class _PlacingSession:
    """Places items of one kind, one per click, until Esc."""

    def __init__(self, symbol):
        self.symbol = symbol
        self.doc = App.ActiveDocument

    def next(self):
        _ensure_draft_gui()
        self.form = PointForm(self.doc, self.symbol, load_last(self.symbol))
        Gui.Snapper.getPoint(callback=self.place, extradlg=self.form.widget,
                             title=SYMBOLS[self.symbol].label)

    def place(self, point, snapped=None):
        if point is None:  # Esc
            return
        values = dict(self.form.data)
        save_last(self.symbol, values)
        kwargs = self.form.kwargs()
        position, rotation = point, values.get("rotation", 0.0)
        if SYMBOLS[self.symbol].wall:
            found = nearest_wall(self.doc, point)
            if found is not None:
                position, rotation = found
        self.doc.openTransaction(f"Inserir {SYMBOLS[self.symbol].label}")
        try:
            model.make_point(self.doc, self.symbol, position, rotation, **kwargs)
        finally:
            self.doc.commitTransaction()
        self.doc.recompute()
        from PySide import QtCore

        QtCore.QTimer.singleShot(0, self.next)  # keep placing until Esc


class InsertSymbolCommand:
    def __init__(self, symbol):
        self.symbol = symbol

    def GetResources(self):
        sym = SYMBOLS[self.symbol]
        return {"Pixmap": icon(self.symbol), "MenuText": sym.label,
                "ToolTip": f"{sym.label}: clique na planta para inserir (Esc termina)"}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        _PlacingSession(self.symbol).next()


# --- dialogs ----------------------------------------------------------------------------------------


def _dialog(title):
    from PySide import QtWidgets

    dlg = QtWidgets.QDialog(Gui.getMainWindow())
    dlg.setWindowTitle(title)
    layout = QtWidgets.QVBoxLayout(dlg)
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    return dlg, layout, buttons


class PointDialog:
    """Edit the data of an item; the symbol is redrawn from it."""

    def __init__(self, obj):
        self.obj = obj
        values = {"circuit": str(obj.Circuit.Number) if obj.Circuit is not None else "", "power": obj.Power,
                  "unit": obj.PowerUnit, "quantity": obj.Quantity, "power_factor": obj.PowerFactor,
                  "letter": obj.Letter, "height": obj.Height.Value, "room": obj.Room,
                  "rotation": obj.Rotation.Value, "demand_group": obj.DemandGroup}
        self.form = PointForm(obj.Document, obj.Symbol, values)
        self.dlg, layout, buttons = _dialog(obj.Label)
        layout.addWidget(self.form.widget)
        layout.addWidget(buttons)

    def exec(self):
        if self.dlg.exec():
            doc = self.obj.Document
            doc.openTransaction("Editar item elétrico")
            kwargs = self.form.kwargs()
            kwargs["rotation"] = self.form.data["rotation"]
            model.set_point(self.obj, **kwargs)
            if kwargs["circuit"] is None:
                self.obj.Circuit = None
            doc.commitTransaction()
            doc.recompute()


class CircuitDialog:
    """Create a circuit, or edit one (`obj`)."""

    def __init__(self, doc, obj=None):
        from PySide import QtWidgets

        self.doc, self.obj = doc, obj
        self.dlg, layout, buttons = _dialog(obj.Label if obj else "Novo circuito")
        form = QtWidgets.QFormLayout()
        self.number = QtWidgets.QSpinBox()
        self.number.setRange(1, 999)
        self.number.setValue(obj.Number if obj else max((c.Number for c in model.circuits(doc)), default=0) + 1)
        self.description = QtWidgets.QLineEdit(obj.Description if obj else "")
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(calc.KINDS)
        self.connection = QtWidgets.QComboBox()
        self.connection.addItems(calc.CONNECTIONS)
        self.phases = QtWidgets.QLineEdit(obj.Phases if obj else "")
        self.phases.setPlaceholderText("automático (fase menos carregada)")
        self.panel = QtWidgets.QLineEdit(obj.Panel if obj else "QD1")
        self.breaker = QtWidgets.QSpinBox()
        self.breaker.setRange(0, 400)
        self.breaker.setSpecialValueText("sugerido")
        self.section = QtWidgets.QDoubleSpinBox()
        self.section.setRange(0, 240)
        self.section.setSpecialValueText("sugerida")
        self.feeds = QtWidgets.QLineEdit(obj.Feeds if obj else "")
        self.feeds.setPlaceholderText("vazio; ou o quadro que este circuito alimenta (ex.: QD2)")
        self.length = QtWidgets.QDoubleSpinBox()
        self.length.setRange(0, 10000)
        self.length.setSuffix(" m")
        self.length.setSpecialValueText("pelos eletrodutos")
        self.rcd = QtWidgets.QComboBox()
        self.rcd.addItems(model.RCD_MODES)
        if obj:
            self.length.setValue(obj.Length)
            self.rcd.setCurrentText(obj.RCD)
        if obj:
            self.kind.setCurrentText(obj.Kind)
            self.connection.setCurrentText(obj.Connection)
            self.breaker.setValue(obj.Breaker)
            self.section.setValue(obj.Section)
        for label, widget in (("Número", self.number), ("Descrição", self.description), ("Tipo", self.kind),
                              ("Ligação", self.connection), ("Fases (R, S, T)", self.phases),
                              ("Quadro", self.panel), ("Disjuntor (A)", self.breaker),
                              ("Seção (mm²)", self.section), ("Alimenta o quadro", self.feeds),
                              ("Comprimento", self.length), ("DR 30 mA", self.rcd)):
            form.addRow(label, widget)
        layout.addLayout(form)
        if obj:
            info = (f"{obj.Points:g} itens, {obj.PowerVA:.0f} VA, {obj.Current:.1f} A; sugeridos: disjuntor "
                    f"{obj.BreakerSuggested:g} A, seção {obj.SectionSuggested:g} mm² ({obj.SectionReason})\n"
                    f"Comprimento {obj.LengthUsed:.1f} m, queda de tensão {obj.VoltageDrop:.2f} %, "
                    f"{obj.Grouping} circuito(s) no eletroduto mais cheio (fator {obj.CorrectionFactor:.2f})"
                    + (f"\nDR exigido: {obj.RCDReason}" if obj.RCDReason else ""))
            layout.addWidget(QtWidgets.QLabel(info + ("\n" + "\n".join(obj.Warnings) if obj.Warnings else "")))
        layout.addWidget(buttons)

    def exec(self):
        if not self.dlg.exec():
            return None
        values = {"number": self.number.value(), "description": self.description.text().strip(),
                  "kind": self.kind.currentText(), "connection": self.connection.currentText(),
                  "phases": self.phases.text().strip().upper(), "panel": self.panel.text().strip() or "QD1",
                  "breaker": self.breaker.value(), "section": self.section.value(),
                  "feeds": self.feeds.text().strip(), "length": self.length.value(), "rcd": self.rcd.currentText()}
        self.doc.openTransaction("Circuito")
        try:
            if self.obj is None:
                self.obj = model.make_circuit(self.doc, **values)
            else:
                model.set_circuit(self.obj, **values)
        finally:
            self.doc.commitTransaction()
        self.doc.recompute()
        return self.obj


class ConduitDialog:
    """Circuits, installation and extra conductors of a conduit (new: `pts`; edit: `obj`)."""

    def __init__(self, doc, obj=None, pts=None):
        from PySide import QtWidgets

        self.doc, self.obj, self.pts = doc, obj, pts
        self.dlg, layout, buttons = _dialog(obj.Label if obj else "Eletroduto")
        form = QtWidgets.QFormLayout()
        self.installation = QtWidgets.QComboBox()
        self.installation.addItems(model.INSTALLATIONS)
        self.circuits = QtWidgets.QListWidget()
        chosen = {c.Name for c in obj.Circuits} if obj else set()
        from PySide import QtCore

        for c in model.circuits(doc):
            item = QtWidgets.QListWidgetItem(f"{c.Number} - {c.Description or c.Kind} ({c.Connection})")
            item.setData(QtCore.Qt.UserRole, c.Name)
            item.setCheckState(QtCore.Qt.Checked if c.Name in chosen else QtCore.Qt.Unchecked)
            self.circuits.addItem(item)
        self.returns = QtWidgets.QSpinBox()
        self.returns.setRange(0, 10)
        self.diameter = QtWidgets.QLineEdit(obj.Diameter if obj else "")
        self.diameter.setPlaceholderText("sugerido pela ocupação")
        self.automatic = QtWidgets.QCheckBox("Automático: circuitos e condutores pelos pontos ligados")
        self.automatic.setChecked(obj.Automatic if obj else True)
        self.automatic.toggled.connect(lambda on: (self.circuits.setEnabled(not on), self.returns.setEnabled(not on)))
        if obj:
            self.installation.setCurrentText(obj.Installation)
            self.returns.setValue(sum(1 for c in obj.ExtraConductors if c == "retorno"))
        form.addRow("Instalação", self.installation)
        form.addRow(self.automatic)
        form.addRow("Circuitos", self.circuits)
        form.addRow("Retornos", self.returns)
        form.addRow("Diâmetro", self.diameter)
        self.circuits.setEnabled(not self.automatic.isChecked())
        self.returns.setEnabled(not self.automatic.isChecked())
        layout.addLayout(form)
        layout.addWidget(buttons)

    def exec(self):
        from PySide import QtCore

        if not self.dlg.exec():
            return
        names = [self.circuits.item(i).data(QtCore.Qt.UserRole) for i in range(self.circuits.count())
                 if self.circuits.item(i).checkState() == QtCore.Qt.Checked]
        automatic = self.automatic.isChecked()
        chosen = [] if automatic else [self.doc.getObject(n) for n in names]
        extra = [] if automatic else ["retorno"] * self.returns.value()
        self.doc.openTransaction("Eletroduto")
        try:
            if self.obj is None:
                model.make_conduit(self.doc, self.pts, self.installation.currentText(), chosen, extra,
                                   self.diameter.text().strip(), automatic=automatic)
            else:
                self.obj.Installation = self.installation.currentText()
                self.obj.Automatic = automatic
                self.obj.Circuits = chosen
                self.obj.ExtraConductors = extra
                self.obj.Diameter = self.diameter.text().strip()
        finally:
            self.doc.commitTransaction()
        self.doc.recompute()


class InstallationDialog:
    def __init__(self, doc):
        from PySide import QtWidgets

        self.doc = doc
        self.inst = model.installation(doc)
        self.dlg, layout, buttons = _dialog("Instalação elétrica")
        form = QtWidgets.QFormLayout()
        self.system = QtWidgets.QComboBox()
        self.system.addItems(list(calc.SYSTEMS))
        self.system.setCurrentText(self.inst.System)
        self.scale = QtWidgets.QDoubleSpinBox()
        self.scale.setRange(1, 10000)
        self.scale.setPrefix("1:")
        self.scale.setValue(self.inst.Scale)
        self.pf = QtWidgets.QDoubleSpinBox()
        self.pf.setRange(0.1, 1.0)
        self.pf.setSingleStep(0.01)
        self.pf.setValue(self.inst.PowerFactor)
        form.addRow("Sistema", self.system)
        form.addRow("Escala do desenho", self.scale)
        form.addRow("Fator de potência padrão", self.pf)
        self.ceiling = QtWidgets.QDoubleSpinBox()
        self.ceiling.setRange(1000, 20000)
        self.ceiling.setSuffix(" mm")
        self.ceiling.setValue(self.inst.CeilingHeight.Value)
        self.temperature = QtWidgets.QDoubleSpinBox()
        self.temperature.setRange(10, 60)
        self.temperature.setSuffix(" °C")
        self.temperature.setValue(self.inst.AmbientTemperature)
        self.drop = QtWidgets.QDoubleSpinBox()
        self.drop.setRange(0.5, 10)
        self.drop.setSuffix(" %")
        self.drop.setValue(self.inst.MaxVoltageDrop)
        form.addRow("Pé-direito (eletrodutos no teto)", self.ceiling)
        form.addRow("Temperatura ambiente", self.temperature)
        form.addRow("Queda de tensão máxima", self.drop)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def exec(self):
        if self.dlg.exec():
            self.doc.openTransaction("Instalação elétrica")
            self.inst.System = self.system.currentText()
            self.inst.Scale = self.scale.value()
            self.inst.PowerFactor = self.pf.value()
            self.inst.CeilingHeight = self.ceiling.value()
            self.inst.AmbientTemperature = self.temperature.value()
            self.inst.MaxVoltageDrop = self.drop.value()
            self.doc.commitTransaction()
            self.doc.recompute()


def _report(title, lines):
    from PySide import QtWidgets

    dlg, layout, _ = _dialog(title)
    text = QtWidgets.QTextBrowser()
    text.setPlainText("\n".join(lines))
    layout.addWidget(text)
    close = QtWidgets.QPushButton("Fechar")
    close.clicked.connect(dlg.accept)
    layout.addWidget(close)
    dlg.resize(640, 480)
    dlg.exec()


# --- other commands ---------------------------------------------------------------------------------


class _Command:
    icon_name, text, tip = "instalacao", "", ""

    def GetResources(self):
        return {"Pixmap": icon(self.icon_name), "MenuText": self.text, "ToolTip": self.tip}

    def IsActive(self):
        return App.ActiveDocument is not None


class CircuitCommand(_Command):
    icon_name, text, tip = "circuito", "Novo circuito", "Cria um circuito (número, tipo, ligação, fases)"

    def Activated(self):
        CircuitDialog(App.ActiveDocument).exec()


class ConduitCommand(_Command):
    icon_name, text = "eletroduto", "Eletroduto"
    tip = "Clique os pontos do eletroduto; Esc termina e pergunta os circuitos"

    def Activated(self):
        self.doc = App.ActiveDocument
        self.pts = []
        self._next()

    def _next(self):
        _ensure_draft_gui()
        last = self.pts[-1] if self.pts else None
        Gui.Snapper.getPoint(last=last, callback=self._clicked, mode="wire",
                             title=f"Eletroduto: ponto {len(self.pts) + 1} (Esc termina)")

    def _clicked(self, point, snapped=None):
        from PySide import QtCore

        if point is None:
            if len(self.pts) >= 2:
                QtCore.QTimer.singleShot(0, lambda: ConduitDialog(self.doc, pts=list(self.pts)).exec())
            return
        self.pts.append(point)
        QtCore.QTimer.singleShot(0, self._next)


class ScheduleCommand(_Command):
    icon_name, text, tip = "quadro_cargas", "Quadro de cargas", "Abre a tabela do quadro de cargas"

    def Activated(self):
        doc = App.ActiveDocument
        doc.openTransaction("Quadro de cargas")
        sched = model.schedule(doc)
        doc.commitTransaction()
        _open_sheet(sched)


class SheetCommand(_Command):
    icon_name, text = "folha", "Folhas do projeto"
    tip = ("Cria as folhas da planta (uma por andar), do quadro de cargas, do diagrama unifilar e da lista de "
           "materiais, que se atualizam sozinhas")

    def Activated(self):
        doc = App.ActiveDocument
        doc.openTransaction("Folhas elétricas")
        try:
            pages = sheets.make_electrical_sheets(doc)
        finally:
            doc.commitTransaction()
        pages[0][1].ViewObject.show()


class DiagramCommand(_Command):
    icon_name, text, tip = "unifilar", "Diagrama unifilar", "Folha com o diagrama unifilar dos quadros"

    def Activated(self):
        from . import outputs

        doc = App.ActiveDocument
        existing = outputs.diagram(doc, create=False)
        if existing is None or existing.View is None:
            doc.openTransaction("Diagrama unifilar")
            try:
                sheets.make_diagram_page(doc)
            finally:
                doc.commitTransaction()
            existing = outputs.diagram(doc, create=False)
        doc.recompute()
        existing.ViewObject.Proxy.doubleClicked(existing.ViewObject)


class MaterialsCommand(_Command):
    icon_name, text, tip = "materiais", "Lista de materiais", "Cabos, eletrodutos, caixas, dispositivos e proteção"

    def Activated(self):
        from . import outputs

        doc = App.ActiveDocument
        doc.openTransaction("Lista de materiais")
        try:
            obj = outputs.materials(doc)
        finally:
            doc.commitTransaction()
        _open_sheet(obj)


def _demand_lines(dem):
    lines = [f"Quadro {dem['panel'] or '(todos)'}: carga instalada {dem['installed_kw']:.2f} kW, "
             f"demanda {dem['demand_kva']:.2f} kVA ({dem['demand_current_a']:.1f} A, {dem['connection']})"]
    if dem["category"]:
        lines.append(f"  Categoria {dem['category']} (por {dem['category_basis']}), disjuntor geral {dem['main_breaker_a']} A")
    else:
        lines.append(f"  {dem['category_basis']}")
    for g in dem["groups"]:
        lines.append(f"  {g['group']}: {g['count']} un., {g['installed_kw']:.2f} kW x {g['factor']:.2f} = "
                     f"{g['demand_kva']:.2f} kVA")
    return lines


class DemandCommand(_Command):
    icon_name, text, tip = "demanda", "Demanda e padrão de entrada", "Demanda e categoria de atendimento (Cosern)"

    def Activated(self):
        doc = App.ActiveDocument
        doc.recompute()
        lines = []
        for name in model.top_panels(doc) or [None]:
            dem = model.demand_summary(doc, name)
            lines += _demand_lines(dem) + [""]
        lines.append(model.demand_summary(doc)["note"])
        _report("Demanda e padrão de entrada", lines)


class CheckCommand(_Command):
    icon_name, text, tip = "conferir", "Conferir (NBR 5410)", "Mínimos por cômodo, circuitos e fases"

    def Activated(self):
        report = checks.check(App.ActiveDocument)
        lines = [report["note"], "", f"Sistema: {report['system']}", ""]
        lines += report["warnings"] or ["Fases e circuitos sem avisos gerais."]
        for dem in report.get("service_entrances", []):
            lines += [""] + _demand_lines(dem)
        for c in report["circuits"]:
            lines.append(f"Circuito {c['number']}: " + "; ".join(c["warnings"]))
        lines.append("")
        if not report["rooms"]:
            lines.append("Nenhum cômodo (BIM) encontrado para conferir os mínimos por cômodo.")
        for r in report["rooms"]:
            state = "ok" if r["ok"] else "; ".join(r["warnings"])
            lines.append(f"{r['room']} ({r['area_m2']} m², {r['perimeter_m']} m): {state}")
        _report("Conferência da instalação", lines)


class BalanceCommand(_Command):
    icon_name, text, tip = "fases", "Equilibrar fases", "Redistribui os circuitos entre R, S e T"

    def Activated(self):
        doc = App.ActiveDocument
        doc.openTransaction("Equilibrar fases")
        changes = model.rebalance_phases(doc)
        doc.commitTransaction()
        summary = model.load_summary(doc)
        lines = [f"Circuito {n}: {old or '-'} -> {new}" for n, (old, new) in changes.items()] or ["Nada a mudar."]
        lines += ["", "Carga por fase (VA): " + ", ".join(f"{p} {v:.0f}" for p, v in summary["phase_va"].items()),
                  f"Desequilíbrio: {summary['imbalance_percent']} %"]
        _report("Equilíbrio de fases", lines)


class InstallationCommand(_Command):
    icon_name, text, tip = "configurar", "Instalação (tensão e escala)", "Sistema 380/220 V ou 220/127 V, escala"

    def Activated(self):
        InstallationDialog(App.ActiveDocument).exec()


TOOLS = {"Eletrica_Circuito": CircuitCommand, "Eletrica_Eletroduto": ConduitCommand,
         "Eletrica_QuadroCargas": ScheduleCommand, "Eletrica_Unifilar": DiagramCommand,
         "Eletrica_Materiais": MaterialsCommand, "Eletrica_Demanda": DemandCommand, "Eletrica_Folhas": SheetCommand,
         "Eletrica_Conferir": CheckCommand, "Eletrica_EquilibrarFases": BalanceCommand,
         "Eletrica_Instalacao": InstallationCommand}


def command_name(symbol):
    return f"Eletrica_{symbol}"


def register_commands():
    for symbol in SYMBOLS:
        Gui.addCommand(command_name(symbol), InsertSymbolCommand(symbol))
    for name, cls in TOOLS.items():
        Gui.addCommand(name, cls())


def toolbars():
    """[(title, [command names])] in display order."""
    out = [(cat, [command_name(s) for s, sym in SYMBOLS.items() if sym.category == cat]) for cat in CATEGORIES]
    out.append(("Instalação", list(TOOLS)))
    return out
