# SPDX-License-Identifier: LGPL-2.1-or-later

"""Walking inside the model: the view of a person standing on a floor, moved with the keyboard.

W/S or Up/Down walk, A/D step sideways, Left/Right turn, R/F look up/down, PageUp/PageDown go
to the floor above/below, Shift walks faster, Esc ends the walk. With the mouse in the 3D view:
drag to look around, the wheel walks forward and back. Walls stop the person unless
"atravessar paredes" is on. While walking, the other floors, the BIM rooms (volumes that would
fill the view) and the plan symbols can be hidden; everything is shown again at the end, and
the camera goes back to where it was.

The geometry helpers (floors, start point, walls at eye height, collisions) work without the
GUI and are tested; the Walk class drives the 3D view.
"""

import math

import FreeCAD as App

from . import model

EYE = 1600.0      # mm above the floor
STEP = 250.0      # mm per key press (x3 with Shift)
TURN = 10.0       # degrees per key press
LOOK = 8.0        # degrees per key press, up or down
RADIUS = 250.0    # mm: how close to a wall the person gets
FOV = 60.0        # degrees, vertical field of view
DRAG = 0.25       # degrees of turn per pixel dragged


# --- geometry (no GUI) --------------------------------------------------------------------------


def floors(doc):
    """[(elevation mm, name)] of the floors: those of the electrical items and the BIM storeys."""
    out = list(model.floors(doc))
    for o in doc.Objects:
        if getattr(o, "IfcType", "") in ("Building Storey", "Floor") and hasattr(o, "Placement"):
            z = float(o.Placement.Base.z)
            if not any(abs(z - f) < model.FLOOR_TOLERANCE for f, _ in out):
                out.append((round(z, 1), o.Label))
    out.sort()
    return out or [(0.0, "Térreo")]


def _on_floor(shape, z):
    return shape is not None and not shape.isNull() and abs(shape.BoundBox.ZMin - z) < model.FLOOR_TOLERANCE


def start_point(doc, z):
    """Where a person starts on the floor at `z`: the middle of its largest room, or of its items."""
    rooms = [o for o in doc.Objects if getattr(o, "IfcType", "") == "Space" and _on_floor(getattr(o, "Shape", None), z)]
    if rooms:
        room = max(rooms, key=lambda o: o.Shape.Area)
        c = room.Shape.CenterOfMass
        return c.x, c.y
    pts = [p.Position for p in model.points(doc) if abs(p.Position.z - z) < model.FLOOR_TOLERANCE]
    if pts:
        return sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)
    return 0.0, 0.0


def inside_a_room(doc, z, x, y):
    """Is (x, y) inside a room of the floor at `z`? True when that floor has no rooms."""
    rooms = [o for o in doc.Objects if getattr(o, "IfcType", "") == "Space" and _on_floor(getattr(o, "Shape", None), z)]
    if not rooms:
        return True
    p = App.Vector(x, y, z + 10.0)
    return any(r.Shape.isInside(p, 1.0, True) for r in rooms)


def wall_segments(doc, z):
    """[((x1, y1), (x2, y2))] of the wall outlines cut at height `z`."""
    out = []
    for o in doc.Objects:
        if getattr(o, "IfcType", "") not in ("Wall", "Column", "Structure"):
            continue
        shape = getattr(o, "Shape", None)
        if shape is None or shape.isNull() or not (shape.BoundBox.ZMin <= z <= shape.BoundBox.ZMax):
            continue
        try:
            wires = shape.slice(App.Vector(0, 0, 1), z)
        except Exception:
            continue
        for wire in wires:
            for e in wire.Edges:
                pts = e.discretize(2) if "Line" in type(e.Curve).__name__ else e.discretize(Deflection=20.0)
                out += [((a.x, a.y), (b.x, b.y)) for a, b in zip(pts, pts[1:])]
    return out


def _dist_to_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _cross(p, q, a, b):
    def orient(u, v, w):
        return (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])

    d1, d2 = orient(a, b, p), orient(a, b, q)
    d3, d4 = orient(p, q, a), orient(p, q, b)
    return d1 * d2 < 0 and d3 * d4 < 0


def blocked(segments, p, q, radius=RADIUS):
    """Would walking from p to q (x, y) go through a wall, or closer to one than `radius`?
    Moving away from a wall one already stands too close to is allowed."""
    for a, b in segments:
        if _cross(p, q, a, b):
            return True
        dq = _dist_to_segment(q, a, b)
        if dq < radius and dq < _dist_to_segment(p, a, b) - 1e-6:
            return True
    return False


def camera_rotation(yaw, pitch):
    """Rotation of a camera looking along `yaw` (degrees from +X) and `pitch` (up), Z up."""
    y, p = math.radians(yaw), math.radians(pitch)
    forward = App.Vector(math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p))
    right = forward.cross(App.Vector(0, 0, 1))
    if right.Length < 1e-9:
        right = App.Vector(math.sin(y), -math.cos(y), 0)
    right.normalize()
    up = right.cross(forward).normalize()
    m = App.Matrix(right.x, up.x, -forward.x, 0,
                   right.y, up.y, -forward.y, 0,
                   right.z, up.z, -forward.z, 0,
                   0, 0, 0, 1)
    return App.Placement(m).Rotation


# --- walking in the 3D view ----------------------------------------------------------------------


class Walk:
    """A walk through the active document's 3D view."""

    current = None

    def __init__(self, doc, floor=0):
        import FreeCADGui as Gui

        self.doc = doc
        self.gui_doc = Gui.getDocument(doc.Name)
        self.view = self.gui_doc.activeView()
        self.floors = floors(doc)
        self.index = max(0, min(int(floor), len(self.floors) - 1))
        self.x, self.y = start_point(doc, self.z_floor)
        self.yaw, self.pitch = 90.0, 0.0
        self.through_walls = False
        self.only_this_floor = True
        self.hide_rooms = True
        self.hide_plan = True
        self.show_ceiling = True
        self._dragging = None
        self._walls = {}
        self._visibility = {}
        self._ceiling_before = None  # the 3D's forro setting before the walk
        self._saved = None
        self._filter = None
        self._observer = None
        self.panel = None

    @property
    def z_floor(self):
        return self.floors[self.index][0]

    # -- start and stop --------------------------------------------------------------------------

    @classmethod
    def start(cls, doc, floor=0):
        if cls.current is not None:
            cls.current.stop()
        walk = cls(doc, floor)
        cls.current = walk
        cam = walk.view.getCameraNode()
        walk._saved = (walk.view.getCameraType(), walk.view.getCamera())
        walk.view.setCameraType("Perspective")
        cam = walk.view.getCameraNode()
        try:
            cam.heightAngle.setValue(math.radians(FOV))
        except Exception:
            pass
        walk.apply_visibility()
        walk.place()
        walk._watch_saves()
        walk._install_keys()
        walk._open_panel()
        return walk

    def stop(self):
        from PySide import QtWidgets

        if self._filter is not None:
            QtWidgets.QApplication.instance().removeEventFilter(self._filter)
            self._filter = None
        if self._observer is not None:
            App.removeDocumentObserver(self._observer)
            self._observer = None
        self._restore_visibility()
        self._model_floor(None)
        if self._saved is not None:
            kind, camera = self._saved
            try:
                self.view.setCameraType(kind)
                self.view.setCamera(camera)
            except Exception:
                pass
        if self.panel is not None:
            panel, self.panel = self.panel, None
            panel.close()
        if Walk.current is self:
            Walk.current = None

    def _watch_saves(self):
        """What the walk hides (and the 3D showing one floor) must not go into the file: saving
        during the walk saves the document as it is outside it, then the walk goes on. Closing
        the document ends the walk."""
        walk = self

        class Saves:
            def slotStartSaveDocument(self, doc, filename):
                if doc is walk.doc and Walk.current is walk:
                    walk._restore_visibility()
                    walk._model_floor(None, refresh=False)

            def slotFinishSaveDocument(self, doc, filename):
                if doc is walk.doc and Walk.current is walk:
                    walk.apply_visibility()
                    walk.place()

            def slotDeletedDocument(self, doc):
                if doc is walk.doc and Walk.current is walk:
                    walk._saved = None  # its view is gone
                    walk._visibility = {}
                    walk.stop()

        self._observer = Saves()
        App.addDocumentObserver(self._observer)

    # -- what is shown ---------------------------------------------------------------------------

    def _restore_visibility(self):
        for name, visible in self._visibility.items():
            obj = self.doc.getObject(name)
            if obj is not None and obj.ViewObject is not None:
                obj.ViewObject.Visibility = visible
        self._visibility = {}
        self._record_hidden()

    def _record_hidden(self):
        """The objects the walk hides, kept in the installation: a recovery file written during
        the walk shows them again when opened."""
        inst = model.installation(self.doc, create=False)
        if inst is None or "WalkHidden" not in inst.PropertiesList:
            return
        names = sorted(self._visibility)
        if list(inst.WalkHidden) != names:
            inst.WalkHidden = names

    def _hide(self, obj):
        vobj = getattr(obj, "ViewObject", None)
        if vobj is None or not vobj.Visibility:
            return
        self._visibility.setdefault(obj.Name, True)
        vobj.Visibility = False

    def _model_floor(self, z, refresh=True):
        """The 3D installation is one object: it shows only the floor walked on (or all)."""
        from . import model3d

        obj = model3d.model3d(self.doc, create=False)
        if obj is None or "VisibleFloor" not in obj.PropertiesList:
            return
        if self._ceiling_before is None and "ShowCeiling" in obj.PropertiesList:
            self._ceiling_before = obj.ShowCeiling
        value = "" if z is None else f"{z:g}"
        ceiling = self._ceiling_before if z is None else self.show_ceiling
        changed = obj.VisibleFloor != value
        if changed:
            obj.VisibleFloor = value
        if "ShowCeiling" in obj.PropertiesList and ceiling is not None and obj.ShowCeiling != bool(ceiling):
            obj.ShowCeiling = bool(ceiling)
            changed = True
        if changed:
            obj.recompute()
            if refresh:
                self._refresh_clipping()

    def _refresh_clipping(self):
        """The view keeps clipping to the old size of the scene after the 3D changed (the walk
        would look into a blank screen): frame everything once, the camera is set again next."""
        try:
            self.view.fitAll()
        except Exception:
            pass

    def apply_visibility(self):
        self._restore_visibility()
        z = self.z_floor
        self._model_floor(z if self.only_this_floor else None)
        for obj in self.doc.Objects:
            kind = model.type_of(obj)
            if obj.TypeId.startswith("TechDraw::") or kind == model.T_MODEL3D:
                continue
            if self.hide_plan and kind in (model.T_POINT, model.T_CONDUIT):
                self._hide(obj)
                continue
            if self.hide_rooms and getattr(obj, "IfcType", "") == "Space":
                self._hide(obj)
                continue
            shape = getattr(obj, "Shape", None)
            if self.only_this_floor and shape is not None and not shape.isNull() and shape.BoundBox.isValid() \
                    and not _on_floor(shape, z) and kind not in (model.T_INSTALLATION,) \
                    and not obj.isDerivedFrom("App::DocumentObjectGroup"):
                if shape.BoundBox.ZMin > z + model.FLOOR_TOLERANCE:  # floors above: they would cover the view
                    self._hide(obj)
        self._record_hidden()

    # -- moving ----------------------------------------------------------------------------------

    def walls(self):
        key = round(self.z_floor, 1)
        if key not in self._walls:
            self._walls[key] = wall_segments(self.doc, self.z_floor + EYE)
        return self._walls[key]

    def place(self):
        cam = self.view.getCameraNode()
        eye = App.Vector(self.x, self.y, self.z_floor + EYE)
        q = camera_rotation(self.yaw, self.pitch).Q
        cam.position.setValue(eye.x, eye.y, eye.z)
        cam.orientation.setValue(q[0], q[1], q[2], q[3])
        try:
            cam.focalDistance.setValue(2000.0)
            cam.nearDistance.setValue(50.0)
        except Exception:
            pass
        self._update_panel()

    def move(self, forward, side, fast=False):
        step = STEP * (3 if fast else 1)
        y = math.radians(self.yaw)
        dx = math.cos(y) * forward * step + math.sin(y) * side * step
        dy = math.sin(y) * forward * step - math.cos(y) * side * step
        p, q = (self.x, self.y), (self.x + dx, self.y + dy)
        if not self.through_walls and blocked(self.walls(), p, q):
            return False
        self.x, self.y = q
        self.place()
        return True

    def turn(self, degrees):
        self.yaw = (self.yaw + degrees) % 360.0
        self.place()

    def look(self, degrees):
        self.pitch = max(-80.0, min(80.0, self.pitch + degrees))
        self.place()

    def drag(self, dx, dy):
        """Mouse dragged by (dx, dy) pixels: turn and look like a head (right turns right)."""
        self.yaw = (self.yaw - dx * DRAG) % 360.0
        self.pitch = max(-80.0, min(80.0, self.pitch - dy * DRAG))
        self.place()

    def wheel(self, steps, fast=False):
        """Mouse wheel: walk forward (positive) or back."""
        for _ in range(abs(int(steps))):
            if not self.move(1 if steps > 0 else -1, 0, fast):
                break

    def go_floor(self, step):
        index = self.index + step
        if not 0 <= index < len(self.floors):
            return False
        self.index = index
        if not inside_a_room(self.doc, self.z_floor, self.x, self.y):
            self.x, self.y = start_point(self.doc, self.z_floor)  # over the void: into the main room
        self.apply_visibility()
        self.place()
        return True

    # -- keyboard --------------------------------------------------------------------------------

    def key(self, key, shift=False):
        """Handle one key (Qt key code); True if it was a walking key."""
        from PySide import QtCore

        K = QtCore.Qt
        if key in (K.Key_W, K.Key_Up):
            self.move(1, 0, shift)
        elif key in (K.Key_S, K.Key_Down):
            self.move(-1, 0, shift)
        elif key == K.Key_A:
            self.move(0, -1, shift)
        elif key == K.Key_D:
            self.move(0, 1, shift)
        elif key == K.Key_Left:
            self.turn(TURN * (3 if shift else 1))
        elif key == K.Key_Right:
            self.turn(-TURN * (3 if shift else 1))
        elif key == K.Key_R:
            self.look(LOOK)
        elif key == K.Key_F:
            self.look(-LOOK)
        elif key == K.Key_PageUp:
            self.go_floor(1)
        elif key == K.Key_PageDown:
            self.go_floor(-1)
        elif key == K.Key_Escape:
            self.stop()
        else:
            return False
        return True

    def _install_keys(self):
        from PySide import QtCore, QtWidgets

        def Gui_main():
            import FreeCADGui as Gui

            return Gui.getMainWindow()

        walk = self

        mdi = Gui_main().findChild(QtWidgets.QMdiArea)
        sub = mdi.activeSubWindow() if mdi is not None else None
        view_widget = sub.widget() if sub is not None else None
        E = QtCore.QEvent

        def point(event):
            pos = event.position() if hasattr(event, "position") else event.pos()
            return pos.x(), pos.y()

        class Keys(QtCore.QObject):
            def eventFilter(self, obj, event):
                if Walk.current is not walk:
                    return False
                kind = event.type()
                if kind in (E.MouseButtonPress, E.MouseMove, E.MouseButtonRelease, E.MouseButtonDblClick, E.Wheel):
                    if view_widget is None or not isinstance(obj, QtWidgets.QWidget) or \
                            not (obj is view_widget or view_widget.isAncestorOf(obj)):
                        return False  # the mouse is elsewhere (the walk window, a menu)
                    if kind == E.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                        walk._dragging = point(event)
                    elif kind == E.MouseMove and walk._dragging is not None:
                        x, y = point(event)
                        walk.drag(x - walk._dragging[0], y - walk._dragging[1])
                        walk._dragging = (x, y)
                    elif kind == E.MouseButtonRelease:
                        walk._dragging = None
                    elif kind == E.Wheel:
                        notches = event.angleDelta().y() / 120.0
                        if notches:
                            shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
                            walk.wheel(round(notches) or (1 if notches > 0 else -1), shift)
                    return True  # the view's own navigation stays still while walking
                if kind != E.KeyPress:
                    return False
                focus = QtWidgets.QApplication.focusWidget()
                if isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QAbstractSpinBox, QtWidgets.QTextEdit,
                                      QtWidgets.QPlainTextEdit)):
                    return False  # typing somewhere else
                shift = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
                return walk.key(event.key(), shift)

        self._filter = Keys()
        QtWidgets.QApplication.instance().installEventFilter(self._filter)

    # -- panel -----------------------------------------------------------------------------------

    def _open_panel(self):
        import FreeCADGui as Gui
        from PySide import QtCore, QtWidgets

        dlg = QtWidgets.QDialog(Gui.getMainWindow())
        dlg.setWindowTitle("Passeio")
        dlg.setWindowFlags(dlg.windowFlags() | QtCore.Qt.Tool)
        layout = QtWidgets.QVBoxLayout(dlg)
        self._floor_label = QtWidgets.QLabel()
        font = self._floor_label.font()
        font.setBold(True)
        self._floor_label.setFont(font)
        layout.addWidget(self._floor_label)
        row = QtWidgets.QHBoxLayout()
        for text, step in (("▲ Andar de cima", 1), ("▼ Andar de baixo", -1)):
            button = QtWidgets.QPushButton(text)
            button.setFocusPolicy(QtCore.Qt.NoFocus)
            button.clicked.connect(lambda checked=False, s=step: self.go_floor(s))
            row.addWidget(button)
        layout.addLayout(row)
        for text, attr in (("Só este andar (esconde os de cima)", "only_this_floor"),
                           ("Esconder os cômodos (volumes BIM)", "hide_rooms"),
                           ("Esconder os símbolos da planta", "hide_plan"),
                           ("Mostrar o forro (translúcido)", "show_ceiling"),
                           ("Atravessar paredes", "through_walls")):
            box = QtWidgets.QCheckBox(text)
            box.setFocusPolicy(QtCore.Qt.NoFocus)
            box.setChecked(getattr(self, attr))
            box.toggled.connect(lambda on, a=attr: self._option(a, on))
            layout.addWidget(box)
        help_text = QtWidgets.QLabel(
            "W/S ou ↑/↓: andar · A/D: de lado · ←/→: virar · R/F: olhar para cima/baixo\n"
            "PageUp/PageDown: andar de cima/de baixo · Shift: mais rápido · Esc: sair\n"
            "Mouse na vista: arraste para olhar em volta, roda para andar")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        leave = QtWidgets.QPushButton("Sair do passeio")
        leave.setFocusPolicy(QtCore.Qt.NoFocus)
        leave.clicked.connect(self.stop)
        layout.addWidget(leave)
        dlg.finished.connect(lambda result: self.stop() if Walk.current is self else None)
        self.panel = dlg
        self._update_panel()
        dlg.show()

    def _option(self, attr, on):
        setattr(self, attr, bool(on))
        if attr != "through_walls":
            self.apply_visibility()

    def _update_panel(self):
        label = getattr(self, "_floor_label", None)
        if label is not None:
            name = self.floors[self.index][1]
            label.setText(f"{name}  ({self.index + 1} de {len(self.floors)})")
