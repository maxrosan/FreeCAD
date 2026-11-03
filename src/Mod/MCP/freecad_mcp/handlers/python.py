# SPDX-License-Identifier: LGPL-2.1-or-later

"""Run arbitrary Python inside FreeCAD, for anything the other commands do not cover."""

import contextlib
import io
import json

import FreeCAD as App

from ..registry import command


@command("run_python", transaction=True)
def run_python(code):
    """Execute ``code``. Assign to ``result`` to return a value; print() output is returned too."""
    namespace = {"App": App, "FreeCAD": App, "__name__": "__mcp__"}
    if App.GuiUp:
        import FreeCADGui

        namespace.update(Gui=FreeCADGui, FreeCADGui=FreeCADGui)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(compile(code, "<mcp>", "exec"), namespace)
    if App.ActiveDocument:
        App.ActiveDocument.recompute()
    reply = {"stdout": out.getvalue()}
    if "result" in namespace:
        value = namespace["result"]
        try:
            json.dumps(value)
            reply["result"] = value
        except TypeError:
            reply["result"] = repr(value)
    return reply
