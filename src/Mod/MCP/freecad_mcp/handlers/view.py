# SPDX-License-Identifier: LGPL-2.1-or-later

"""3D view commands: screenshots and camera."""

import os
import tempfile
import time

import FreeCAD as App

from ..registry import CommandError, command

_VIEWS = {
    "top": "viewTop",
    "bottom": "viewBottom",
    "front": "viewFront",
    "rear": "viewRear",
    "left": "viewLeft",
    "right": "viewRight",
    "iso": "viewIsometric",
    "axonometric": "viewAxonometric",
}


def _active_view():
    if not App.GuiUp:
        raise CommandError("FreeCAD is running without a GUI; screenshots are not available")
    import FreeCADGui as Gui

    if Gui.ActiveDocument is None:
        raise CommandError("No document is open")
    view = Gui.ActiveDocument.ActiveView
    if not hasattr(view, "saveImage"):
        # A sheet or another tab is in front; bring back (or open) the 3D view.
        Gui.activateView("Gui::View3DInventor", True)
        view = Gui.ActiveDocument.ActiveView
    if not hasattr(view, "saveImage"):
        raise CommandError("The active window is not a 3D view")
    return view


# --- plan cut ------------------------------------------------------------------------------
# A floor plan is a horizontal cut about 1,5 m above the floor: walls show their openings, doors
# their swing and windows their glass. Seen from above, the wall over a door or window hides it.
CUT_HEIGHT = 1500.0
_clip = {}  # id of the view's scene graph -> (clip node, height)


def _walls_base(doc):
    """Base of the lowest wall or column, or None when the document has none."""
    zs = [o.Shape.BoundBox.ZMin for o in doc.Objects
          if getattr(o, "IfcType", "") in ("Wall", "Column") and not o.Shape.isNull()]
    return min(zs) if zs else None


def _cut_height(doc, cut, direction):
    """Height of the cut for `cut` (True, a height in mm, False or None = automatic: on for the
    top view of a document with walls), or None for no cut."""
    if cut is False:
        return None
    if cut is None and direction not in ("top",):
        return None
    if isinstance(cut, (int, float)) and not isinstance(cut, bool):
        return float(cut)
    base = _walls_base(doc)
    if base is None:
        return None
    return base + CUT_HEIGHT


def _apply_cut(view, height):
    """Clip the view above `height` (None: no clip)."""
    from pivy import coin

    root = view.getSceneGraph()
    key = id(root)
    node, current = _clip.get(key, (None, None))
    if height is None:
        if node is not None:
            idx = root.findChild(node)
            if idx >= 0:
                root.removeChild(idx)
            _clip.pop(key, None)
        return
    if node is None or root.findChild(node) < 0:
        node = coin.SoClipPlane()
        root.insertChild(node, 0)
    # keeps what is below: the normal points down
    node.plane.setValue(coin.SbPlane(coin.SbVec3f(0, 0, -1), -float(height)))
    node.on.setValue(True)
    _clip[key] = (node, height)


def cut_active(view):
    node, height = _clip.get(id(view.getSceneGraph()), (None, None))
    return height if node is not None else None


def _set_view(view, direction, fit):
    if direction and direction != "current":
        if direction not in _VIEWS:
            raise CommandError(f"Unknown view {direction!r}; use one of {sorted(_VIEWS)} or 'current'")
        getattr(view, _VIEWS[direction])()
    if fit:
        view.fitAll()


@command("set_view")
def set_view(direction="top", fit=True, cut=None):
    """Camera direction. cut: plan cut of walls, doors and windows (True; a height in mm; False);
    by default on for the top view of a building, at 1,5 m above its lowest wall."""
    view = _active_view()
    _set_view(view, direction, fit)
    height = _cut_height(App.ActiveDocument, cut, direction)
    _apply_cut(view, height)
    return {"view": direction, "cut_mm": height}


@command("get_screenshot")
def get_screenshot(direction="top", width=1600, height=1200, fit=True, background="White", path=None,
                   cut=None):
    """Save the 3D view as PNG and return its path. cut: see set_view (with a cut, the image is
    the view as it is on screen, scaled to fit width x height: the off-screen renderer ignores
    clipping planes)."""
    import FreeCADGui as Gui

    from .common import recompute

    if App.ActiveDocument is not None:
        recompute(App.ActiveDocument, full=True)  # the 3D installation may be waiting for the pause
    view = _active_view()
    Gui.Selection.clearSelection()  # selected objects would be drawn in the highlight color
    _set_view(view, direction, fit)
    if direction == "current" and cut is None:
        cut_mm = cut_active(view)  # as it is on screen
    else:
        cut_mm = _cut_height(App.ActiveDocument, cut, direction)
    _apply_cut(view, cut_mm)
    if path is None:
        folder = os.path.join(tempfile.gettempdir(), "freecad_mcp")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"view_{int(time.time() * 1000)}.png")
    if cut_mm is None or not _grab(view, path, int(width), int(height)):
        view.saveImage(path, int(width), int(height), background)
    return {"path": path, "width": int(width), "height": int(height), "direction": direction, "cut_mm": cut_mm}


def _grab(view, path, width, height):
    """The view as drawn on screen (clipping planes included), scaled to fit width x height."""
    try:
        from PySide import QtCore, QtWidgets

        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        viewport = view.graphicsView().viewport()
        image = viewport.grabFramebuffer() if hasattr(viewport, "grabFramebuffer") else viewport.grab().toImage()
        if image.isNull():
            return False
        image = image.scaled(width, height, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        return image.save(path)
    except Exception as e:
        App.Console.PrintLog(f"MCP: screenshot of the screen failed ({e}); rendering off-screen\n")
        return False
