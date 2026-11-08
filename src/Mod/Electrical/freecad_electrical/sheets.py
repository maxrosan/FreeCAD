# SPDX-License-Identifier: LGPL-2.1-or-later

"""TechDraw sheets that stay up to date.

A sheet draws one link group whose element list is "everything drawable": layers, levels,
groups (the electrical installation among them) and loose visible objects. The list is marked
AutoSources and rebuilt whenever objects are created or deleted, so items added later show up
on the sheet by themselves. The load schedule goes on its own page, its range following the
table as it grows.
"""

import datetime
import os

import FreeCAD as App

from . import model

AUTO = "AutoSources"
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
SKIP_NAMES = ("MCP_Settings", "Revisao")


def drawing_area(size):
    """(x0, y0, x1, y1) of the free area above the title block, in page mm."""
    left, right, bottom, top = MARGINS
    return (left, bottom, size[0] - right, size[1] - top)


def fits(box, scale, size):
    x0, y0, x1, y1 = drawing_area(size)
    return box.XLength / scale <= x1 - x0 and box.YLength / scale <= y1 - y0


def fit_scale(box, size):
    for n in STANDARD_SCALES:
        if fits(box, n, size):
            return n
    return STANDARD_SCALES[-1]


def _type(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", None)


def sources(doc):
    """What a sheet draws: layers, levels and groups as a whole, plus loose visible objects."""
    from draftobjects.layer import get_layer

    def has_parent(o):
        # The real container (group, level, part), not any object that links to it: sheet
        # contents and review comments link to objects too.
        return o.getParentGroup() is not None or o.getParentGeoFeatureGroup() is not None

    found = []
    for obj in doc.Objects:
        if obj.Name in SKIP_NAMES or _type(obj) == "ReviewComment" or obj.TypeId.startswith("TechDraw::") \
                or obj.TypeId in ("App::LinkGroup", "Spreadsheet::Sheet"):
            continue  # review comments, sheets and tables never go into the drawing
        kind = _type(obj)
        if kind in ("LayerContainer", "SectionPlane"):
            continue
        if kind == "Layer":
            found.append(obj)  # layers live in the layer container but are drawn on their own
            continue
        if has_parent(obj) or get_layer(obj) is not None:
            continue  # drawn through its layer, level or group
        if kind != "BuildingPart" and not obj.isDerivedFrom("App::DocumentObjectGroup") \
                and App.GuiUp and obj.ViewObject and not obj.ViewObject.Visibility:
            continue
        found.append(obj)
    return found


def extent(objs):
    box = None

    def add(o):
        nonlocal box
        for member in getattr(o, "Group", None) or []:
            add(member)
        shape = getattr(o, "Shape", None)
        if shape is None or shape.isNull():
            return
        if box is None:
            box = App.BoundBox(shape.BoundBox)
        else:
            box.add(shape.BoundBox)

    for obj in objs:
        add(obj)
    return box


def fill_title_block(template, title, scale_text, sheet_text="1 / 1"):
    """Fill title, scale and date; blank the sample texts the template ships with."""
    try:
        texts = dict(template.EditableTexts)
    except Exception:
        return
    for key in texts:
        low = key.lower()
        if "scale" in low or "escala" in low:
            texts[key] = scale_text
        elif "title" in low or "titulo" in low:
            texts[key] = title
        elif "date" in low or "data" in low:
            texts[key] = datetime.date.today().strftime("%d/%m/%Y")
        elif "sheet" in low:
            texts[key] = sheet_text
        else:
            texts[key] = ""
    template.EditableTexts = texts


def _page(doc, name, label, sheet):
    size, template_file = SHEETS[sheet]
    page = doc.addObject("TechDraw::DrawPage", name)
    page.Label = label
    template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", template_file)
    page.Template = template
    page.KeepUpdated = True
    return page, template, size


def make_content(doc, objs=None, label="Conteúdo da folha"):
    """Link group drawn by a sheet. Without `objs` it follows the document (AutoSources)."""
    content = doc.addObject("App::LinkGroup", "SheetContent")
    content.Label = label
    content.addProperty("App::PropertyBool", AUTO, "Folha", "Acompanhar os objetos do documento")
    setattr(content, AUTO, objs is None)
    content.ElementList = sources(doc) if objs is None else list(objs)
    if App.GuiUp and content.ViewObject:
        content.ViewObject.Visibility = False
    return content


def make_sheet(doc, sheet="auto", scale=None, objs=None, title=None, line_width=0.35, text_height=2.5):
    """A page with the drawing at 1:scale, kept up to date. Returns (page, view, sheet, scale, box)."""
    content_objs = objs if objs is not None else sources(doc)
    if not content_objs:
        raise ValueError("Nothing to draw")
    box = extent(content_objs)
    if box is None:
        raise ValueError("The objects have no geometry")
    if scale is None:
        inst = model.installation(doc, create=False)
        mcp = doc.getObject("MCP_Settings")
        if inst is not None:
            scale = inst.Scale
        elif mcp is not None and getattr(mcp, "ScaleDenominator", 0) > 1:
            scale = mcp.ScaleDenominator
    if sheet == "auto":
        if scale is None:
            sheet = "A3"
        else:
            ordered = sorted(SHEETS.items(), key=lambda kv: kv[1][0][0])
            fitting = [k for k, (size, _) in ordered if fits(box, scale, size)]
            sheet = fitting[0] if fitting else "A0"
    if sheet not in SHEETS:
        raise ValueError(f"Unknown sheet {sheet!r}; use one of {sorted(SHEETS)} or 'auto'")
    if scale is None:
        scale = fit_scale(box, SHEETS[sheet][0])
    scale = float(scale)
    page, template, size = _page(doc, "Sheet", title or f"Folha {sheet} 1:{scale:g}", sheet)
    page.Scale = 1 / scale
    fill_title_block(template, title or doc.Label, f"1:{scale:g}")
    # One view of a link group holding every source: links leave the objects in their layers
    # and levels, and a single view needs no alignment with others.
    content = make_content(doc, objs, f"{page.Label} - conteúdo")
    view = doc.addObject("TechDraw::DrawViewDraft", "View")
    view.Source = content
    view.ScaleType = "Custom"
    view.Scale = 1 / scale
    view.LineWidth = float(line_width)
    view.FontSize = 2.0 * float(text_height)  # Draft's get_svg draws texts at half of FontSize
    page.addView(view)
    # X, Y is the centre of the view: the middle of the free area (after addView, which centres it).
    x0, y0, x1, y1 = drawing_area(size)
    view.X, view.Y = (x0 + x1) / 2, (y0 + y1) / 2
    doc.recompute()
    return page, view, sheet, scale, box


def make_schedule_page(doc, title="Quadro de cargas", sheet="A3"):
    """A page with the load schedule table, following the table as it grows."""
    sched = model.schedule(doc)
    doc.recompute()
    page, template, size = _page(doc, "SheetQuadroDeCargas", title, sheet)
    fill_title_block(template, title, "-", "")
    table = doc.addObject("TechDraw::DrawViewSpreadsheet", "VistaQuadroDeCargas")
    table.Source = sched.Sheet
    table.CellStart = "A1"
    table.CellEnd = sched.LastCell or "N5"
    table.TextSize = 3.0
    page.addView(table)
    x0, y0, x1, y1 = drawing_area(size)
    table.X, table.Y = (x0 + x1) / 2, (y0 + y1) / 2 + 20
    doc.recompute()
    return page, table


def follow_schedule(doc):
    """Keep the range of schedule views on the whole table."""
    sched = model.schedule(doc, create=False)
    if sched is None or not sched.LastCell:
        return
    for obj in doc.Objects:
        if obj.TypeId == "TechDraw::DrawViewSpreadsheet" and obj.Source == sched.Sheet \
                and obj.CellEnd != sched.LastCell:
            obj.CellEnd = sched.LastCell


def sync(doc):
    """Rebuild the element list of every AutoSources sheet content; True if something changed."""
    changed = False
    current = None
    for obj in doc.Objects:
        if obj.TypeId == "App::LinkGroup" and getattr(obj, AUTO, False):
            current = current if current is not None else sources(doc)
            if [o.Name for o in obj.ElementList] != [o.Name for o in current]:
                obj.ElementList = current
                changed = True
    follow_schedule(doc)
    return changed


class _Observer:
    """Created or deleted objects: refresh the sheets once the change is done."""

    def __init__(self):
        self.pending = set()

    def _schedule(self, doc):
        if not App.GuiUp or doc is None or doc.Name in self.pending:
            return
        from PySide import QtCore

        self.pending.add(doc.Name)
        QtCore.QTimer.singleShot(0, lambda name=doc.Name: self._run(name))

    def _run(self, name):
        self.pending.discard(name)
        doc = App.listDocuments().get(name)
        if doc is None:
            return
        try:
            if sync(doc):
                doc.recompute()
        except Exception as e:
            App.Console.PrintWarning(f"Elétrica: folhas não atualizadas: {e}\n")

    def slotCreatedObject(self, obj):
        if not obj.TypeId.startswith("TechDraw::") and obj.TypeId != "App::LinkGroup":
            self._schedule(obj.Document)

    def slotDeletedObject(self, obj):
        if not obj.TypeId.startswith("TechDraw::") and obj.TypeId != "App::LinkGroup":
            self._schedule(obj.Document)

    def slotRecomputedDocument(self, doc):
        if App.GuiUp:
            try:
                follow_schedule(doc)
            except Exception:
                pass


_observer = None


def install_observer():
    global _observer
    if _observer is None:
        _observer = _Observer()
        App.addDocumentObserver(_observer)
