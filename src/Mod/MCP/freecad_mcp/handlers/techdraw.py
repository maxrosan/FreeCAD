# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sheet commands built on TechDraw: a page at the drawing scale, export to PDF/SVG/DXF."""

import os

import FreeCAD as App
from freecad_electrical import sheets

from ..registry import CommandError, command
from .common import SETTINGS_NAME, active_document, get_object, recompute, settings


@command("create_sheet", transaction=True)
def create_sheet(sheet="auto", scale=None, objects=None, title=None, line_width=0.35, text_height=2.5):
    """A TechDraw page with the drawing at scale 1:N, kept up to date with the document.

    sheet: A4..A0 (landscape) or "auto" (smallest that fits at the scale).
    scale: N for 1:N; defaults to the document drawing scale, else the largest standard scale that fits.
    objects: names to draw (a fixed list); default everything, following objects added later.
    text_height: height of Draft texts on paper (mm); the Draft view draws them all at this size.
    Electrical items draw their texts as geometry, at their own size.
    """
    doc = active_document(create=False)
    objs = [get_object(doc, n) for n in objects] if objects else None
    stored = settings(doc, create=False)
    if scale is None and stored is not None and stored.ScaleDenominator > 1:
        scale = stored.ScaleDenominator
    try:
        page, view, size_name, scale, box = sheets.make_sheet(doc, sheet, scale, objs, title, line_width,
                                                              text_height)
    except ValueError as e:
        raise CommandError(str(e)) from e
    size = sheets.SHEETS[size_name][0]
    reply = {"page": page.Name, "label": page.Label, "sheet": size_name, "scale": f"1:{scale:g}",
             "view": view.Name, "drawing_size_on_paper_mm": [round(box.XLength / scale, 1),
                                                          round(box.YLength / scale, 1)]}
    if not sheets.fits(box, scale, size):
        reply["warning"] = "The drawing is larger than the free area of the sheet; use a bigger sheet or scale"
    return reply


@command("export_sheet")
def export_sheet(path, page=None):
    """Export a TechDraw page to .pdf, .svg or .dxf (default: the last page)."""
    if not App.GuiUp:
        raise CommandError("Exporting sheets needs FreeCAD with its GUI")
    import TechDrawGui

    doc = active_document(create=False)
    if page:
        obj = get_object(doc, page)
    else:
        pages = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"]
        if not pages:
            raise CommandError("The document has no sheet; call create_sheet first")
        obj = pages[-1]
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".pdf", ".svg", ".dxf"):
        raise CommandError("Use a .pdf, .svg or .dxf path")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc.recompute()
    # PDF and SVG are rendered from the page's graphics scene, which exists only while the
    # page is open in a tab.
    import FreeCADGui as Gui

    obj.ViewObject.show()
    pending = wait_for_views(obj)
    reply = {"file": path, "page": obj.Label}
    if pending:
        reply["warning"] = f"Views still empty after waiting, exported without them: {pending}"
    if ext == ".dxf":
        import TechDraw

        TechDraw.writeDXFPage(obj, path)
    elif ext == ".pdf":
        TechDrawGui.exportPageAsPdf(obj, path)
    else:
        TechDrawGui.exportPageAsSvg(obj, path)
    Gui.activateView("Gui::View3DInventor", True)
    return reply


def _projected_views(page):
    """Views of a page computed by hidden-line removal (DrawViewPart), including projection group items."""
    views = []
    for view in page.Views:
        if view.isDerivedFrom("TechDraw::DrawProjGroup"):
            views.extend(view.Views)
        elif view.isDerivedFrom("TechDraw::DrawViewPart"):
            views.append(view)
    return views


def wait_for_views(page, timeout=180.0):
    """Wait until the projected views of a page have their geometry, then let the page repaint.

    TechDraw projects views in background threads after a recompute; exporting before they
    finish leaves them out of the file. Returns the labels of views still empty at the timeout.
    """
    import time

    from PySide import QtWidgets

    views = _projected_views(page)
    deadline = time.time() + timeout
    while True:
        QtWidgets.QApplication.processEvents()
        pending = [v.Label for v in views if not v.getVisibleEdges()]
        if not pending or time.time() > deadline:
            break
        time.sleep(0.2)
    # The scene redraws views from queued signals once their geometry arrives.
    page.requestPaint()
    end = time.time() + 0.5
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.05)
    return pending


@command("export_model")
def export_model(path, objects=None):
    """Export the model (or some objects) to .dxf, .svg, .ifc, .step, .stl ... by extension."""
    import importlib

    doc = active_document(create=False)
    recompute(doc, full=True)
    objs = [get_object(doc, n) for n in objects] if objects else [
        o for o in doc.Objects if o.Name != SETTINGS_NAME and not o.TypeId.startswith("TechDraw::")]
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    modules = {".dxf": "importDXF", ".svg": "importSVG", ".ifc": "importers.exportIFC",
               ".step": "Import", ".stp": "Import", ".stl": "Mesh", ".obj": "Mesh"}
    if ext not in modules:
        raise CommandError(f"Unsupported format {ext}; use one of {sorted(modules)}")
    module = importlib.import_module(modules[ext])
    module.export(objs, path)
    return {"file": path, "objects": len(objs)}
