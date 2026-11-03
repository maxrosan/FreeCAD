# SPDX-License-Identifier: LGPL-2.1-or-later

"""Helpers shared by the command handlers."""

import FreeCAD as App

from ..registry import CommandError
from ..units import vec

SETTINGS_NAME = "MCP_Settings"


def active_document(create=True):
    doc = App.ActiveDocument
    if doc is None:
        if not create:
            raise CommandError("No document is open")
        doc = App.newDocument("Drawing")
    return doc


def get_object(doc, name):
    """Find an object by internal name, then by label."""
    obj = doc.getObject(name)
    if obj is None:
        found = doc.getObjectsByLabel(name)
        obj = found[0] if found else None
    if obj is None:
        raise CommandError(f"No object named {name!r} in document {doc.Name}")
    return obj


def describe(obj):
    """Short JSON description of an object."""
    info = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    proxy = getattr(obj, "Proxy", None)
    if proxy is not None and hasattr(proxy, "Type"):
        info["type"] = proxy.Type
    shape = getattr(obj, "Shape", None)
    if shape is not None and not shape.isNull():
        box = shape.BoundBox
        info["bbox"] = {"min": vec(App.Vector(box.XMin, box.YMin, box.ZMin)),
                        "max": vec(App.Vector(box.XMax, box.YMax, box.ZMax))}
        if shape.Edges and not shape.Faces:
            info["length"] = round(shape.Length, 3)
        if shape.Faces and not shape.Solids:
            info["area"] = round(shape.Area, 3)
    for prop in ("Length", "Width", "Height"):
        if hasattr(obj, prop) and prop not in info:
            value = getattr(obj, prop)
            info[prop.lower()] = round(getattr(value, "Value", value), 3)
    if hasattr(obj, "Text") and isinstance(obj.Text, (list, tuple)):
        info["text"] = "\n".join(obj.Text)
    layer = find_layer_of(obj)
    if layer is not None:
        info["layer"] = layer.Label
    return info


def find_layer_of(obj):
    # Layers link their children through a hidden property, which InList does not report.
    from draftobjects.layer import get_layer

    return get_layer(obj)


def settings(doc, create=True):
    """Hidden object holding per-document MCP settings (drawing scale)."""
    obj = doc.getObject(SETTINGS_NAME)
    if obj is None and create:
        obj = doc.addObject("App::FeaturePython", SETTINGS_NAME)
        obj.addProperty("App::PropertyFloat", "ScaleDenominator", "MCP",
                        "Drawing scale used for sheets and PDF conversion, as 1:N").ScaleDenominator = 1.0
        if App.GuiUp and obj.ViewObject:
            obj.ViewObject.ShowInTree = False
    return obj


def recompute(doc):
    doc.recompute()
    errors = [o.Label for o in doc.Objects if "Invalid" in o.State or "Error" in o.State]
    return errors
