# SPDX-License-Identifier: LGPL-2.1-or-later

"""Drawing live: the person watches the assistant draw instead of a frozen window.

While a command runs:

* the objects it creates are collected (a document observer), and ``pulse()`` - called by long
  commands between items, and available to run_python code - shows them at once: they are
  recomputed, the 3D view repaints, the newest ones are selected (highlighted) and the view pans
  (or zooms out) to keep them in sight. Mouse and keyboard input wait until the command is done.
* the status bar says what the assistant is doing.
* heavy outputs (TechDraw pages, spreadsheets, the electrical schedule, sheets and 3D) are left
  for later: ``recompute()`` refreshes only what the drawing needs. They are recomputed once the
  assistant pauses (IDLE_MS without commands). Commands that return results computed from
  them ask for a full recompute.

Without the GUI (FreeCADCmd, tests) everything is recomputed at once, as before.
"""

import time

import FreeCAD as App

IDLE_MS = 700     # without commands for this long: recompute what was left for later
PULSE_S = 0.06    # at most one repaint per this many seconds
FOLLOW = True     # pan the view to what is being drawn

_state = {
    "depth": 0,          # commands running (nested calls through run_python)
    "name": "",          # command running
    "created": [],       # objects created since the last pulse: [(doc name, object name)]
    "shown": [],         # objects highlighted by the last pulse
    "last": 0.0,         # time of the last repaint
    "docs": set(),       # documents with outputs left for later
    "held": False,       # electrical sheets held until the pause
    "observer": None,
    "timer": None,
    "count": 0,          # objects created by the command running
    "started": 0.0,
    "box": None,         # everything drawn by the command running (the view keeps it in sight)
    "timings": [],       # recent (what, seconds): commands and recomputes after the pause
}


def _timing(what, seconds):
    _state["timings"] = (_state["timings"] + [(what, round(seconds, 3))])[-40:]


def enabled():
    return bool(App.GuiUp) and _state["depth"] > 0


# --- what is heavy -----------------------------------------------------------------------


def _deferred_types():
    try:
        from freecad_electrical import model
    except Exception:
        return set()
    return set(model.OUTPUT_TYPES) | {model.T_MODEL3D, model.T_SCHEDULE, "SheetContent"}


def is_heavy(obj, types=None):
    """Outputs that can wait for the pause: pages, spreadsheets, the electrical schedule, sheets, 3D."""
    type_id = obj.TypeId
    if type_id.startswith("TechDraw::") or type_id.startswith("Spreadsheet::"):
        return True
    kind = getattr(getattr(obj, "Proxy", None), "Type", None)
    return kind in (types if types is not None else _deferred_types())


def recompute(doc, full=False):
    """Recompute the document; while drawing live, only what is not heavy (the rest waits for the
    pause). Returns the number of objects recomputed."""
    if full or not enabled():
        return doc.recompute()
    types = _deferred_types()
    touched = [o for o in doc.Objects if "Touched" in o.State or getattr(o, "MustExecute", False)]
    quick = [o for o in touched if not is_heavy(o, types)]
    if len(quick) < len(touched):
        _state["docs"].add(doc.Name)
    return doc.recompute(quick) if quick else 0


# --- a command running -------------------------------------------------------------------


class _Created:
    def slotCreatedObject(self, obj):
        doc = getattr(obj, "Document", None)
        if doc is not None:
            _state["created"].append((doc.Name, obj.Name))
            _state["count"] += 1


LABELS = {
    "draw_batch": "desenhando", "draw_line": "desenhando uma linha", "draw_polyline": "desenhando uma polilinha",
    "draw_rectangle": "desenhando um retângulo", "draw_circle": "desenhando um círculo", "draw_arc": "desenhando um arco",
    "draw_text": "escrevendo um texto", "draw_dimension": "cotando", "make_wall": "levantando uma parede",
    "make_walls": "levantando paredes", "make_walls_from_objects": "levantando paredes", "add_opening": "abrindo um vão",
    "make_space": "criando um cômodo", "make_level": "criando um pavimento",
    "insert_electrical_symbol": "colocando um ponto elétrico", "draw_conduit": "traçando um eletroduto",
    "create_circuit": "criando um circuito", "pdf_import_to_freecad": "importando o PDF",
    "import_reference_geometry": "importando a referência", "run_python": "trabalhando",
    "create_electrical_sheets": "montando as folhas", "electrical_3d": "montando o 3D",
}


def begin(name):
    """A command starts (called by the registry)."""
    if not App.GuiUp:
        return
    _state["depth"] += 1
    if _state["depth"] > 1:
        return
    _state.update(name=name, created=[], count=0, last=time.time(), started=time.time(), box=None)
    _stop_timer()
    _hold_sheets(True)
    if _state["observer"] is None:
        _state["observer"] = _Created()
        App.addDocumentObserver(_state["observer"])
    _status(LABELS.get(name, name.replace("_", " ")))


def end():
    """The command is done: show what it made, and recompute the rest once the assistant pauses."""
    if not App.GuiUp or _state["depth"] == 0:
        return
    _state["depth"] -= 1
    if _state["depth"] > 0:
        return
    try:
        _show(force=True)
    except Exception as e:
        App.Console.PrintLog(f"MCP live: {e}\n")
    if _state["observer"] is not None:
        App.removeDocumentObserver(_state["observer"])
        _state["observer"] = None
    _timing(_state["name"], time.time() - _state["started"])
    _start_timer()


def pulse(text=None):
    """Show what was drawn so far (throttled); `text` replaces the status message."""
    if not enabled():
        return
    if text:
        _status(text)
    _show(force=False)


# --- showing -----------------------------------------------------------------------------


def _new_objects():
    found = []
    for doc_name, name in _state["created"]:
        doc = App.listDocuments().get(doc_name)
        obj = doc.getObject(name) if doc is not None else None
        if obj is not None:
            found.append(obj)
    _state["created"] = []
    return found


def _show(force):
    now = time.time()
    if not force and now - _state["last"] < PULSE_S:
        return
    _state["last"] = now
    objs = _new_objects()
    docs = {o.Document.Name: o.Document for o in objs}
    for doc in docs.values():
        recompute(doc)
    visible = [o for o in objs if _drawn(o)]
    if visible:
        _highlight(visible)
        if FOLLOW:
            _follow(visible)
    if _state["count"] > 1 and _state["name"] in ("draw_batch", "make_walls", "pdf_import_to_freecad",
                                                    "run_python", "make_walls_from_objects"):
        _status(f"{LABELS.get(_state['name'], _state['name'])} ({_state['count']} objetos)")
    _repaint()


def _drawn(obj):
    if is_heavy(obj) or obj.TypeId.startswith("App::") and not hasattr(obj, "Shape"):
        return False
    shape = getattr(obj, "Shape", None)
    vobj = getattr(obj, "ViewObject", None)
    return shape is not None and not shape.isNull() and shape.BoundBox.isValid() and \
        vobj is not None and vobj.Visibility


def _highlight(objs):
    import FreeCADGui as Gui

    try:
        Gui.Selection.clearSelection()
        for obj in objs[-50:]:
            Gui.Selection.addSelection(obj.Document.Name, obj.Name)
        _state["shown"] = [(o.Document.Name, o.Name) for o in objs[-50:]]
    except Exception:
        pass


def _clear_highlight():
    import FreeCADGui as Gui

    if not _state["shown"]:
        return
    try:
        selected = {(s.DocumentName, s.ObjectName) for s in Gui.Selection.getSelectionEx("*")}
        if selected and selected <= set(_state["shown"]):
            Gui.Selection.clearSelection()  # only what we selected: the person's selection stays
    except Exception:
        pass
    _state["shown"] = []


def view_of(doc):
    """The 3D view of the document, if one is open (not a page)."""
    import FreeCADGui as Gui

    gdoc = Gui.getDocument(doc.Name)
    if gdoc is None:
        return None
    active = gdoc.activeView()
    if active is not None and hasattr(active, "getCameraNode"):
        return active
    views = gdoc.mdiViewsOfType("Gui::View3DInventor")
    return views[0] if views else None


def _follow(objs):
    """Keep what is being drawn in sight. When something new falls outside the view, the view
    moves to show everything drawn by the command so far (zooming out if needed, never in), so
    it opens up as the drawing grows instead of jumping from item to item."""
    try:
        from freecad_electrical import walk

        if walk.Walk.current is not None:
            return  # the walk owns the camera
    except Exception:
        pass
    doc = objs[-1].Document
    view = view_of(doc)
    if view is None:
        return
    new = App.BoundBox()
    for o in objs:
        new.add(o.Shape.BoundBox)
    box = _state["box"] if _state["box"] is not None else App.BoundBox()
    box.add(new)
    _state["box"] = box
    width, height = view.getSize()
    margin = 0.08

    def pixels_of(b):
        corners = [App.Vector(x, y, z) for x in (b.XMin, b.XMax) for y in (b.YMin, b.YMax) for z in (b.ZMin, b.ZMax)]
        return [view.getPointOnViewport(c) for c in corners]

    def inside(points):
        return all(margin * width <= p[0] <= (1 - margin) * width and margin * height <= p[1] <= (1 - margin) * height
                   for p in points)

    if inside(pixels_of(new)):
        return
    pixels = pixels_of(box)
    span_x = max(p[0] for p in pixels) - min(p[0] for p in pixels)
    span_y = max(p[1] for p in pixels) - min(p[1] for p in pixels)
    zoom = max(1.0, span_x / (width * (1 - 2 * margin)), span_y / (height * (1 - 2 * margin)))
    cam = view.getCameraNode()
    center = view.getPointOnFocalPlane(width // 2, height // 2)
    target = box.Center
    # move the camera parallel to the screen so the box centre comes to the view centre
    direction = cam.orientation.getValue().multVec(_coin_vec(0, 0, -1))
    d = App.Vector(*direction.getValue())
    shift = target - App.Vector(*center)
    shift = shift - d * shift.dot(d)
    pos = App.Vector(*cam.position.getValue().getValue())
    pos += shift
    if zoom > 1.0:
        if view.getCameraType() == "Orthographic":
            cam.height.setValue(cam.height.getValue() * zoom * 1.1)
        else:
            dist = cam.focalDistance.getValue()
            pos -= d * dist * (zoom * 1.1 - 1)
            cam.focalDistance.setValue(dist * zoom * 1.1)
    cam.position.setValue(pos.x, pos.y, pos.z)


def _coin_vec(x, y, z):
    from pivy import coin

    return coin.SbVec3f(x, y, z)


def _repaint():
    import FreeCADGui as Gui
    from PySide import QtCore, QtWidgets

    try:
        Gui.updateGui()
    except Exception:
        pass
    # the person's input waits for the command (and a command arriving meanwhile waits in the
    # server's queue)
    QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)


def _status(text):
    try:
        import FreeCADGui as Gui

        bar = Gui.getMainWindow().statusBar()
        bar.showMessage(f"Claude: {text}…" if text else "")
    except Exception:
        pass


# --- the pause ---------------------------------------------------------------------------


def _hold_sheets(on):
    """The electrical sheets refresh (a full recompute) waits for the pause too."""
    if on == _state["held"]:
        return
    try:
        from freecad_electrical import sheets
    except Exception:
        return
    if on:
        sheets.hold_updates()
    else:
        sheets.release_updates()
    _state["held"] = on


def _start_timer():
    from PySide import QtCore

    _stop_timer()
    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(_idle)
    timer.start(IDLE_MS)
    _state["timer"] = timer


def _stop_timer():
    timer = _state["timer"]
    if timer is not None:
        timer.stop()
        _state["timer"] = None


def _idle():
    """The assistant paused: recompute the outputs left for later, then let the sheets refresh."""
    _state["timer"] = None
    if _state["depth"] > 0:
        return
    docs = [App.listDocuments().get(n) for n in sorted(_state["docs"])]
    _state["docs"] = set()
    _clear_highlight()
    start = time.time()
    try:
        if any(d is not None for d in docs):
            _status("atualizando folhas, tabelas e 3D")
            _repaint()
            for doc in docs:
                if doc is not None:
                    doc.recompute()
    finally:
        _hold_sheets(False)
        _status("")
    if docs:
        _timing("pausa: folhas, tabelas e 3D", time.time() - start)


def settle():
    """Recompute what was left for later now (tests, or before reading the results)."""
    _stop_timer()
    if _state["depth"] == 0:
        _idle()
