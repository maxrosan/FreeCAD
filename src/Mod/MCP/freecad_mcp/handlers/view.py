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


def _set_view(view, direction, fit):
    if direction and direction != "current":
        if direction not in _VIEWS:
            raise CommandError(f"Unknown view {direction!r}; use one of {sorted(_VIEWS)} or 'current'")
        getattr(view, _VIEWS[direction])()
    if fit:
        view.fitAll()


@command("set_view")
def set_view(direction="top", fit=True):
    view = _active_view()
    _set_view(view, direction, fit)
    return {"view": direction}


@command("get_screenshot")
def get_screenshot(direction="top", width=1600, height=1200, fit=True, background="White", path=None):
    """Save the 3D view as PNG and return its path."""
    import FreeCADGui as Gui

    from .common import recompute

    if App.ActiveDocument is not None:
        recompute(App.ActiveDocument, full=True)  # the 3D installation may be waiting for the pause
    view = _active_view()
    Gui.Selection.clearSelection()  # selected objects would be drawn in the highlight color
    _set_view(view, direction, fit)
    if path is None:
        folder = os.path.join(tempfile.gettempdir(), "freecad_mcp")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"view_{int(time.time() * 1000)}.png")
    view.saveImage(path, int(width), int(height), background)
    return {"path": path, "width": int(width), "height": int(height), "direction": direction}
