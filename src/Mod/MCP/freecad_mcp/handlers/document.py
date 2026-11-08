# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document commands: create, inspect, delete, undo, save, drawing scale."""

import os

import FreeCAD as App

from .. import registry
from ..registry import CommandError, command
from .common import active_document, describe, get_object, recompute, settings, SETTINGS_NAME


@command("ping")
def ping():
    return {"freecad": ".".join(App.Version()[:3]), "gui": bool(App.GuiUp), "commands": registry.names()}


@command("new_document")
def new_document(name="Drawing"):
    doc = App.newDocument(name)
    App.setActiveDocument(doc.Name)
    if App.GuiUp:
        import FreeCADGui as Gui

        Gui.ActiveDocument = Gui.getDocument(doc.Name)
    return {"document": doc.Name}


@command("open_document")
def open_document(path):
    doc = App.openDocument(path)
    App.setActiveDocument(doc.Name)
    return {"document": doc.Name}


@command("list_documents")
def list_documents():
    active = App.ActiveDocument.Name if App.ActiveDocument else None
    return [{"name": d.Name, "label": d.Label, "file": d.FileName, "objects": len(d.Objects),
             "active": d.Name == active} for d in App.listDocuments().values()]


@command("set_active_document")
def set_active_document(name):
    if name not in App.listDocuments():
        raise CommandError(f"No document named {name!r}")
    App.setActiveDocument(name)
    return {"document": name}


@command("get_document_summary")
def get_document_summary(max_objects=500, types=None):
    """Objects of the active document with type, layer, bounding box and lengths."""
    doc = active_document(create=False)
    objects = [o for o in doc.Objects if o.Name != SETTINGS_NAME]
    if types:
        objects = [o for o in objects if any(t.lower() in describe(o)["type"].lower() for t in types)]
    items = [describe(o) for o in objects[:max_objects]]
    shapes = [o.Shape for o in objects if getattr(o, "Shape", None) is not None and not o.Shape.isNull()]
    scale = settings(doc, create=False)
    result = {"document": doc.Name, "count": len(objects), "objects": items,
              "scale_denominator": scale.ScaleDenominator if scale else None}
    if shapes:
        box = shapes[0].BoundBox
        for s in shapes[1:]:
            box.add(s.BoundBox)
        result["extent_mm"] = {"x": round(box.XLength, 3), "y": round(box.YLength, 3),
                               "z": round(box.ZLength, 3), "min": [box.XMin, box.YMin, box.ZMin]}
    if len(objects) > max_objects:
        result["truncated"] = True
    return result


@command("get_object")
def get_object_info(name):
    doc = active_document(create=False)
    obj = get_object(doc, name)
    info = describe(obj)
    info["properties"] = {}
    for prop in obj.PropertiesList:
        try:
            value = getattr(obj, prop)
        except Exception:
            continue
        if isinstance(value, (int, float, str, bool)):
            info["properties"][prop] = value
        elif hasattr(value, "Value"):
            info["properties"][prop] = value.Value
        elif isinstance(value, App.Vector):
            info["properties"][prop] = [value.x, value.y, value.z]
    return info


@command("delete_objects", transaction=True)
def delete_objects(names):
    doc = active_document(create=False)
    deleted = []
    for name in names:
        obj = get_object(doc, name)
        deleted.append(obj.Label)
        doc.removeObject(obj.Name)
    recompute(doc)
    return {"deleted": deleted}


@command("undo")
def undo(steps=1):
    doc = active_document(create=False)
    done = 0
    for _ in range(steps):
        if doc.UndoCount == 0:
            break
        doc.undo()
        done += 1
    recompute(doc)
    return {"undone": done}


@command("redo")
def redo(steps=1):
    doc = active_document(create=False)
    done = 0
    for _ in range(steps):
        if doc.RedoCount == 0:
            break
        doc.redo()
        done += 1
    recompute(doc)
    return {"redone": done}


@command("save_document")
def save_document(path=None):
    doc = active_document(create=False)
    if path:
        path = os.path.abspath(path)
        if not path.lower().endswith(".fcstd"):
            path += ".FCStd"
        doc.saveAs(path)
    elif doc.FileName:
        doc.save()
    else:
        raise CommandError("The document was never saved; pass a path")
    return {"file": doc.FileName}


@command("recompute")
def recompute_document():
    doc = active_document(create=False)
    return {"errors": recompute(doc)}


@command("set_drawing_scale", transaction=True)
def set_drawing_scale(denominator):
    """Store the drawing scale 1:denominator in the document (used by sheets and PDF tools)."""
    denominator = float(denominator)
    if denominator <= 0:
        raise CommandError("The scale denominator must be positive")
    doc = active_document()
    settings(doc).ScaleDenominator = denominator
    inst = doc.getObject("InstalacaoEletrica")  # the electrical items are drawn at the same scale
    if inst is not None:
        inst.Scale = denominator
    return {"scale": f"1:{denominator:g}"}


@command("get_drawing_scale")
def get_drawing_scale():
    doc = active_document(create=False)
    obj = settings(doc, create=False)
    return {"denominator": obj.ScaleDenominator if obj else None}
