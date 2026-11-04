# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sheet commands built on TechDraw: a page at the drawing scale, export to PDF/SVG/DXF."""

import os

import FreeCAD as App

from ..registry import CommandError, command
from .common import SETTINGS_NAME, active_document, get_object, recompute, settings

# Landscape ISO sheets: size in mm and template file.
SHEETS = {
    "A4": ((297, 210), "ISO/A4_Landscape_ISO5457_minimal.svg"),
    "A3": ((420, 297), "ISO/A3_Landscape_ISO5457_minimal.svg"),
    "A2": ((594, 420), "ISO/A2_Landscape_ISO5457_minimal.svg"),
    "A1": ((841, 594), "ISO/A1_Landscape_ISO5457_minimal.svg"),
    "A0": ((1189, 841), "ISO/A0_Landscape_ISO5457_minimal.svg"),
}
STANDARD_SCALES = [1, 2, 5, 10, 20, 25, 50, 75, 100, 125, 200, 250, 500, 1000, 2000, 5000]
# Margins (mm) of the area the drawing may use: left, right, bottom (title block strip), top.
MARGINS = (25, 15, 70, 15)


def _drawing_area(size):
    """(x0, y0, x1, y1) of the free area above the title block, in page mm."""
    left, right, bottom, top = MARGINS
    return (left, bottom, size[0] - right, size[1] - top)


def _fits(box, scale, size):
    x0, y0, x1, y1 = _drawing_area(size)
    return box.XLength / scale <= x1 - x0 and box.YLength / scale <= y1 - y0


def _type(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", None)


def _sources(doc, names):
    """Objects to put on the sheet: layers and levels as a whole, plus loose visible objects."""
    if names:
        return [get_object(doc, n) for n in names]
    from draftobjects.layer import get_layer

    sources = []
    for obj in doc.Objects:
        if obj.Name == SETTINGS_NAME or obj.TypeId.startswith("TechDraw::") or obj.TypeId == "App::LinkGroup":
            continue
        kind = _type(obj)
        if kind in ("Layer", "BuildingPart") or obj.TypeId == "App::DocumentObjectGroup":
            if not any(_type(p) == "BuildingPart" for p in obj.InList):
                sources.append(obj)
            continue
        if kind in ("LayerContainer", "SectionPlane"):
            continue
        if obj.InList or get_layer(obj) is not None:
            continue  # drawn through its layer, level or parent
        if App.GuiUp and obj.ViewObject and not obj.ViewObject.Visibility:
            continue
        sources.append(obj)
    return sources


def _extent(objs):
    box = None
    for obj in objs:
        members = getattr(obj, "Group", None) or [obj]
        for m in members:
            shape = getattr(m, "Shape", None)
            if shape is None or shape.isNull():
                continue
            if box is None:
                box = App.BoundBox(shape.BoundBox)
            else:
                box.add(shape.BoundBox)
    return box


def _fit_scale(box, size):
    for n in STANDARD_SCALES:
        if _fits(box, n, size):
            return n
    return STANDARD_SCALES[-1]


@command("create_sheet", transaction=True)
def create_sheet(sheet="auto", scale=None, objects=None, title=None, line_width=0.35, text_height=2.5):
    """A TechDraw page with the drawing at scale 1:N.

    sheet: A4..A0 (landscape) or "auto" (smallest that fits at the scale).
    scale: N for 1:N; defaults to the document drawing scale, else the largest standard scale that fits.
    objects: names to draw; default all layers, levels and loose visible objects.
    text_height: height of the texts on paper (mm). The Draft view draws every text at this one
    size, whatever the size of each text in the model.
    """
    doc = active_document(create=False)
    sources = _sources(doc, objects)
    if not sources:
        raise CommandError("Nothing to draw")
    box = _extent(sources)
    if box is None:
        raise CommandError("The objects have no geometry")
    stored = settings(doc, create=False)
    if scale is None and stored is not None and stored.ScaleDenominator > 1:
        scale = stored.ScaleDenominator
    if sheet == "auto":
        if scale is None:
            sheet = "A3"
        else:
            fits = [k for k, (size, _) in sorted(SHEETS.items(), key=lambda kv: kv[1][0][0])
                    if _fits(box, scale, size)]
            sheet = fits[0] if fits else "A0"
    if sheet not in SHEETS:
        raise CommandError(f"Unknown sheet {sheet!r}; use one of {sorted(SHEETS)} or 'auto'")
    size, template_file = SHEETS[sheet]
    if scale is None:
        scale = _fit_scale(box, size)
    scale = float(scale)

    page = doc.addObject("TechDraw::DrawPage", "Sheet")
    page.Label = title or f"Sheet {sheet} 1:{scale:g}"
    template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", template_file)
    page.Template = template
    page.Scale = 1 / scale
    _fill_title_block(template, title or doc.Label, scale)

    # One view of a link group holding every source: links leave the objects in their layers
    # and levels, and a single view needs no alignment with others.
    content = doc.addObject("App::LinkGroup", "SheetContent")
    content.Label = f"{page.Label} content"
    content.ElementList = sources
    if App.GuiUp and content.ViewObject:
        content.ViewObject.Visibility = False
    view = doc.addObject("TechDraw::DrawViewDraft", "View")
    view.Source = content
    view.ScaleType = "Custom"
    view.Scale = 1 / scale
    view.LineWidth = float(line_width)
    view.FontSize = 2.0 * float(text_height)  # Draft's get_svg draws texts at half of FontSize
    page.addView(view)
    # X, Y is the centre of the view; put it in the middle of the free area (after addView,
    # which centres new views on the page).
    x0, y0, x1, y1 = _drawing_area(size)
    view.X, view.Y = (x0 + x1) / 2, (y0 + y1) / 2
    recompute(doc)
    reply = {"page": page.Name, "label": page.Label, "sheet": sheet, "scale": f"1:{scale:g}",
             "view": view.Name, "drawing_size_on_paper_mm": [round(box.XLength / scale, 1),
                                                          round(box.YLength / scale, 1)]}
    if not _fits(box, scale, size):
        reply["warning"] = "The drawing is larger than the free area of the sheet; use a bigger sheet or scale"
    return reply


def _fill_title_block(template, title, scale):
    """Fill title, scale and date; blank the sample texts the template ships with."""
    import datetime

    try:
        texts = dict(template.EditableTexts)
    except Exception:
        return
    for key in texts:
        low = key.lower()
        if "scale" in low or "escala" in low:
            texts[key] = f"1:{scale:g}"
        elif "title" in low or "titulo" in low:
            texts[key] = title
        elif "date" in low or "data" in low:
            texts[key] = datetime.date.today().strftime("%d/%m/%Y")
        elif "sheet" in low:
            texts[key] = "1 / 1"
        else:
            texts[key] = ""
    template.EditableTexts = texts


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
