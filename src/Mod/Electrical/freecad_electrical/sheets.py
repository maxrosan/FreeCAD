# SPDX-License-Identifier: LGPL-2.1-or-later

"""TechDraw sheets that stay up to date.

A sheet draws one content object whose element list is "everything drawable": layers, levels,
groups (the electrical installation among them) and loose visible objects. The list is marked
AutoSources and rebuilt whenever objects are created or deleted, so items added later show up
on the sheet by themselves. The content only points at the objects: it does not hold them the
way a group does, so it never hides them in the 3D view. The load schedule goes on its own page, its range following the
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
LINE_SPACING = 1.5  # line pitch of the texts on plan sheets, in text heights


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


def is_content(obj):
    """A sheet content (the link groups of older files too)."""
    return _type(obj) == "SheetContent" or obj.TypeId == "App::LinkGroup"


class SheetContent:
    """What a plan sheet draws: a list of objects (ElementList), drawn by Draft's get_svg."""

    Type = "SheetContent"

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyLinkList", "ElementList", "Folha", "Objetos desenhados na folha")
        obj.addProperty("App::PropertyBool", AUTO, "Folha", "Acompanhar os objetos do documento")

    def svg_group(self, obj):
        return list(obj.ElementList)

    def execute(self, obj):
        pass

    def dumps(self):
        return None

    def loads(self, state):
        return None

    __getstate__ = dumps

    def __setstate__(self, state):
        return None


def _consumed(obj):
    """Used by another object as its base (the outline of a wall or a room): drawn by that one."""
    for user in obj.InList:
        if user.TypeId.startswith("TechDraw::") or is_content(user) or _type(user) in (
                "ReviewComment", "Layer", "LayerContainer", "BuildingPart") \
                or user.isDerivedFrom("App::DocumentObjectGroup"):
            continue
        return True
    return False


def sources(doc, keep=()):
    """What a sheet draws: layers, levels and groups as a whole, plus loose visible objects. Objects
    in `keep` (already on the sheet) stay even when hidden in the 3D view, unless another object
    now uses them as its base: hiding walls to look at the installation must not take them off
    the plan."""
    from draftobjects.layer import get_layer

    def has_parent(o):
        # The real container (group, level, part), not any object that links to it: sheet
        # contents and review comments link to objects too.
        return o.getParentGroup() is not None or o.getParentGeoFeatureGroup() is not None

    found = []
    for obj in doc.Objects:
        if obj.Name in SKIP_NAMES or _type(obj) == "ReviewComment" or obj.TypeId.startswith("TechDraw::") \
                or obj.TypeId == "Spreadsheet::Sheet" or is_content(obj):
            continue  # review comments, sheets and tables never go into the drawing
        kind = _type(obj)
        if kind in ("LayerContainer", "SectionPlane", model.T_MODEL3D):
            continue  # the 3D installation has its own (isometric) page
        if kind == "Layer":
            found.append(obj)  # layers live in the layer container but are drawn on their own
            continue
        if has_parent(obj) or get_layer(obj) is not None:
            continue  # drawn through its layer, level or group
        if kind != "BuildingPart" and not obj.isDerivedFrom("App::DocumentObjectGroup") \
                and App.GuiUp and obj.ViewObject and not obj.ViewObject.Visibility \
                and (obj.Name not in keep or _consumed(obj)):
            continue
        found.append(obj)
    return _electrical_last(found)


def _elevation(obj):
    shape = getattr(obj, "Shape", None)
    if shape is not None and not shape.isNull():
        return shape.BoundBox.ZMin
    placement = getattr(obj, "Placement", None)
    return placement.Base.z if placement is not None else None


def floor_ranges(doc):
    """[(name, zmin, zmax)] of the floors with electrical items (one floor: [])."""
    floors = model.floors(doc)
    if len(floors) < 2:
        return []
    tol = model.FLOOR_TOLERANCE
    out = []
    for i, (z, name) in enumerate(floors):
        top = floors[i + 1][0] - tol if i + 1 < len(floors) else 1e12
        out.append((name, z - tol, top))
    return out


def floor_sources(doc, zmin, zmax, keep=()):
    """What a sheet of one floor draws: the objects starting between zmin and zmax, taken out of
    their groups, levels and layers (a group may span several floors). `keep`: see sources."""
    found, seen = [], set()

    def walk(obj):
        if obj.Name in seen:
            return
        seen.add(obj.Name)
        members = getattr(obj, "Group", None)
        if members is not None and (obj.isDerivedFrom("App::DocumentObjectGroup") or _type(obj) in ("Layer", "BuildingPart")):
            for member in members:
                walk(member)
            return
        if obj.TypeId.startswith("TechDraw::") or obj.TypeId == "Spreadsheet::Sheet" or is_content(obj):
            return
        z = _elevation(obj)
        if z is not None and zmin <= z < zmax:
            found.append(obj)

    for obj in sources(doc, keep):
        walk(obj)
    return _electrical_last(found)


def _electrical_last(objs):
    """Draw order: the electrical installation goes last, on top of the filled rooms."""
    electrical = {model.T_INSTALLATION, model.T_POINT, model.T_CONDUIT}
    return sorted(objs, key=lambda o: _type(o) in electrical)


def extent(objs):
    box = None

    def add(o):
        nonlocal box
        for member in getattr(o, "Group", None) or []:
            add(member)
        for shape in (getattr(o, "Shape", None), getattr(o, "Labels", None)):
            if shape is None or shape.isNull():
                continue
            if box is None:
                box = App.BoundBox(shape.BoundBox)
            else:
                box.add(shape.BoundBox)

    for obj in objs:
        add(obj)
    return box


DOCUMENT_TYPE = "Projeto de instalações elétricas"
# Field names of the ISO 5457 title block and their Portuguese (Brazilian practice) captions.
PT_CAPTIONS = {
    "Title:": "Título:",
    "Created by:": "Responsável técnico:",
    "Approved by:": "Aprovado por:",
    "Document type:": "Tipo de documento:",
    "Drawing number:": "Prancha nº:",
    "Issue date:": "Data:",
    "Language:": "Idioma:",
    "Owner:": "Proprietário:",
    "Part Material:": "Obra:",
    "General tolerances:": "Local:",
    "Revision:": "Revisão:",
    "Scale:": "Escala:",
    "Sheet:": "Folha:",
}


def fill_title_block(template, title, scale_text, sheet_text="1 / 1"):
    """Fill title, scale, date, document type and language; blank the sample texts the template
    ships with."""
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
        elif "document_type" in low:
            texts[key] = DOCUMENT_TYPE
        elif "language" in low:
            texts[key] = "PT-BR"
        else:
            texts[key] = ""
    template.EditableTexts = texts


def template_pt(template_file):
    """The ISO template with its captions in Portuguese: a copy in the user data folder, written
    again when the original changes. Falls back to the original if it cannot be written."""
    src = os.path.join(App.getResourceDir(), "Mod", "TechDraw", "Templates", template_file)
    folder = os.path.join(App.getUserAppDataDir(), "Electrical", "templates")
    dst = os.path.join(folder, os.path.basename(template_file).replace(".svg", "_pt.svg"))
    try:
        with open(src, encoding="utf-8") as f:
            text = f.read()
        for en, pt in PT_CAPTIONS.items():
            text = text.replace(f">{en}<", f">{pt}<")
        old = None
        if os.path.exists(dst):
            with open(dst, encoding="utf-8") as f:
                old = f.read()
        if old != text:
            os.makedirs(folder, exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(text)
        return dst
    except OSError:
        return src


def _page(doc, name, label, sheet):
    size, template_file = SHEETS[sheet]
    page = doc.addObject("TechDraw::DrawPage", name)
    page.Label = label
    template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
    template.Template = template_pt(template_file)
    page.Template = template
    page.KeepUpdated = True
    return page, template, size


# ------------------------------------------------------------------------------- windows on plans
WINDOW_MARK = "JanelaPlanta"  # property that marks (and links) the plan symbol of a window


def _is_window(obj):
    return obj.TypeId == "Part::FeaturePython" and "Hosts" in obj.PropertiesList \
        and getattr(obj, "IfcType", "") == "Window" and obj.Hosts


def window_marks(doc):
    """Plan symbol of every window: a white band across its host wall, over the opening, with two
    lines for the glass. The plan draws the walls from above, where the wall over the window hides
    the opening; doors already draw their swing. Updated on each call: a window keeps its mark
    (sheets already drawing it keep it), marks of removed windows go. Only windows in walls along X
    or Y are drawn. Returns the marks."""
    import Part

    existing = {}
    for old in [o for o in doc.Objects if WINDOW_MARK in o.PropertiesList]:
        win = getattr(old, WINDOW_MARK, None)
        if win is not None and win.Name not in existing:
            existing[win.Name] = old
        else:
            doc.removeObject(old.Name)
    marks = []
    for win in [o for o in doc.Objects if _is_window(o)]:
        shape = getattr(win, "Shape", None)
        if shape is None or shape.isNull():
            continue
        box = shape.BoundBox
        thick = getattr(win.Hosts[0], "Width", None)
        thick = float(getattr(thick, "Value", thick or 0.0))
        if thick <= 0:
            continue
        z = (getattr(win.Hosts[0], "Shape", None) or shape).BoundBox.ZMin + 50.0  # inside the wall, under the sill
        if box.XLength >= box.YLength:  # wall along X
            c, h = box.Center.y, thick / 2
            def pt(a, b): return App.Vector(a, c + b, z)
            a0, a1 = box.XMin, box.XMax
        else:  # wall along Y
            c, h = box.Center.x, thick / 2
            def pt(a, b): return App.Vector(c + b, a, z)
            a0, a1 = box.YMin, box.YMax
        if a1 - a0 < 1.0:
            continue
        band = Part.Face(Part.makePolygon([pt(a0, -h), pt(a1, -h), pt(a1, h), pt(a0, h), pt(a0, -h)]))
        glass = [Part.LineSegment(pt(a0, d), pt(a1, d)).toShape() for d in (-0.15 * thick, 0.15 * thick)]
        mark = existing.pop(win.Name, None)
        if mark is None:
            mark = doc.addObject("Part::Feature", "JanelaPlanta")
            mark.addProperty("App::PropertyLink", WINDOW_MARK, "Planta", "Janela representada")
            setattr(mark, WINDOW_MARK, win)
        mark.Shape = Part.makeCompound([band] + glass)
        mark.Label = f"Janela (planta) - {win.Label}"
        if App.GuiUp and mark.ViewObject is not None:
            mark.ViewObject.ShapeColor = (1.0, 1.0, 1.0)
            mark.ViewObject.LineColor = (0.0, 0.0, 0.0)
        marks.append(mark)
    for orphan in existing.values():
        doc.removeObject(orphan.Name)
    return marks


# ------------------------------------------------------------------------------- legend
LEGEND_TEXT = 2.5  # paper mm
LEGEND_ROW = 7.0


def _legend_prims(svg, prims, x, y):
    """Lines and fills of a symbol (texts left out) with its origin at page point (x, y); symbol
    coordinates are paper mm with y up."""
    for prim in prims:
        kind = prim[0]
        if kind == "line":
            (ax, ay), (bx, by) = prim[1], prim[2]
            svg.append(f'<line x1="{x + ax:.2f}" y1="{y - ay:.2f}" x2="{x + bx:.2f}" y2="{y - by:.2f}" '
                       f'stroke="black" stroke-width="0.25"/>')
        elif kind == "poly":
            d = " ".join(f"{x + px:.2f},{y - py:.2f}" for px, py in prim[1])
            tag = "polygon" if prim[2] else "polyline"
            fill = "black" if prim[3] else "none"
            svg.append(f'<{tag} points="{d}" fill="{fill}" stroke="black" stroke-width="0.25"/>')
        elif kind == "circle":
            (cx, cy), r = prim[1], prim[2]
            fill = "black" if prim[3] else "none"
            svg.append(f'<circle cx="{x + cx:.2f}" cy="{y - cy:.2f}" r="{r:.2f}" fill="{fill}" '
                       f'stroke="black" stroke-width="0.25"/>')


def legend_rows(doc):
    """[(kind, key, description)] of what the plan of `doc` uses: symbols, conductor marks and
    conduit installations, in the order of the symbol table."""
    from .symbols import CONDUCTORS, SYMBOLS

    used = {p.Symbol for p in model.points(doc)}
    rows = [("symbol", key, sym.label) for key, sym in SYMBOLS.items() if key in used]
    conduits = model.items(doc, model.T_CONDUIT)
    installs = {getattr(c, "Installation", "teto") for c in conduits}
    names = {"teto": "Eletroduto embutido no teto ou na parede", "parede": None,
             "piso": "Eletroduto embutido no piso", "enterrado": "Eletroduto enterrado"}
    for inst in ("teto", "piso", "enterrado"):
        if inst in installs or (inst == "teto" and "parede" in installs):
            rows.append(("conduit", inst, names[inst]))
    if conduits:
        captions = {"fase": "Condutor fase", "neutro": "Condutor neutro", "retorno": "Condutor retorno",
                    "terra": "Condutor de proteção (terra)"}
        rows += [("conductor", k, captions[k]) for k in ("fase", "neutro", "retorno", "terra") if k in CONDUCTORS]
    return rows


def legend_svg(doc):
    """(svg, width, height) of the legend of the plan: one row per symbol, conduit and conductor
    mark in use, drawn as on the plan, and a line on the texts beside the symbols."""
    from xml.sax.saxutils import escape

    from .symbols import CONDUCTORS, SYMBOLS

    rows = legend_rows(doc)
    if not rows:
        return None, 0.0, 0.0
    notes = ["Junto aos símbolos: potência, -nº do circuito- e",
             "letra do comando (interruptor e ponto de luz)."]
    longest = max([len(r[2]) for r in rows] + [len(n) for n in notes])
    width = 24.0 + longest * LEGEND_TEXT * 0.52 + 4.0
    height = 10.0 + LEGEND_ROW * len(rows) + 4.0 + LEGEND_TEXT * 1.4 * len(notes) + 3.0
    svg = [f'<rect x="0.5" y="0.5" width="{width - 1:.2f}" height="{height - 1:.2f}" fill="white" '
           f'stroke="black" stroke-width="0.35"/>',
           f'<text x="{width / 2:.2f}" y="7" font-family="osifont" font-size="3.5" text-anchor="middle" '
           f'font-weight="bold" fill="black">LEGENDA</text>']
    y = 10.0 + LEGEND_ROW / 2
    for kind, key, caption in rows:
        if kind == "symbol":
            prims = SYMBOLS[key].build({})
            xs = [q[0] for p in prims if p[0] == "poly" for q in p[1]] + \
                 [v for p in prims if p[0] == "line" for v in (p[1][0], p[2][0])] + \
                 [p[1][0] + s * p[2] for p in prims if p[0] == "circle" for s in (-1, 1)]
            cx = 12.0 - ((min(xs) + max(xs)) / 2 if xs else 0.0)
            _legend_prims(svg, prims, cx, y)
            if key.startswith("interruptor"):
                svg.append(f'<text x="{cx + 2.0:.2f}" y="{y - 1.4:.2f}" font-family="osifont" font-size="2" '
                           f'fill="black">a</text>')
        elif kind == "conduit":
            dash = {"teto": "", "piso": ' stroke-dasharray="2,1"', "enterrado": ' stroke-dasharray="4,0.8,0.4,0.8"'}[key]
            svg.append(f'<line x1="4" y1="{y:.2f}" x2="20" y2="{y:.2f}" stroke="black" stroke-width="0.35"{dash}/>')
        else:
            svg.append(f'<line x1="4" y1="{y:.2f}" x2="20" y2="{y:.2f}" stroke="black" stroke-width="0.35"/>')
            _legend_prims(svg, CONDUCTORS[key], 12.0, y)
        svg.append(f'<text x="24" y="{y + LEGEND_TEXT * 0.35:.2f}" font-family="osifont" '
                   f'font-size="{LEGEND_TEXT}" fill="black">{escape(caption)}</text>')
        y += LEGEND_ROW
    y += 1.0
    for line in notes:
        y += LEGEND_TEXT * 1.4
        svg.append(f'<text x="4" y="{y:.2f}" font-family="osifont" font-size="{LEGEND_TEXT}" '
                   f'fill="black">{escape(line)}</text>')
    body = "\n".join(svg)
    text = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.1f}mm" height="{height:.1f}mm" '
            f'viewBox="0 0 {width:.1f} {height:.1f}">\n{body}\n</svg>')
    return text, width, height


def add_legend(doc, page, plan_view=None):
    """The legend at the top right of the drawing area; the plan moves left to make room when it
    fits there. Returns the legend view (None when the plan uses no symbol)."""
    svg, width, height = legend_svg(doc)
    if not svg:
        return None
    view = doc.addObject("TechDraw::DrawViewSymbol", "Legenda")
    view.Label = "Legenda"
    page.addView(view)
    view.Symbol = svg
    view.ScaleType = "Custom"
    view.Scale = 1.0
    size = page.Template.Width.Value, page.Template.Height.Value
    x0, y0, x1, y1 = drawing_area(size)
    view.X, view.Y = x1 - width / 2 - 2.0, y1 - height / 2 - 2.0
    if plan_view is not None:
        source = getattr(plan_view, "Source", None)
        box = extent(list(getattr(source, "ElementList", []) or [])) if source is not None else None
        free = x1 - width - 6.0
        if box is not None and box.XLength * plan_view.Scale <= free - x0:
            plan_view.X = (x0 + free) / 2
    doc.recompute()
    return view


def _content_sources(doc, content):
    keep = {o.Name for o in getattr(content, "ElementList", []) or []}
    if getattr(content, "FloorMax", 0.0) > getattr(content, "FloorMin", 0.0):
        return floor_sources(doc, content.FloorMin, content.FloorMax, keep)
    return sources(doc, keep)


def make_content(doc, objs=None, label="Conteúdo da folha", floor=None):
    """Link group drawn by a sheet. Without `objs` it follows the document (AutoSources), or the
    objects of one floor when `floor` is (zmin, zmax)."""
    content = doc.addObject("App::FeaturePython", "SheetContent")
    SheetContent(content)
    content.Label = label
    setattr(content, AUTO, objs is None)
    if floor is not None:
        content.addProperty("App::PropertyFloat", "FloorMin", "Folha", "Andar: cota inicial (mm)")
        content.addProperty("App::PropertyFloat", "FloorMax", "Folha", "Andar: cota final (mm)")
        content.FloorMin, content.FloorMax = floor
    content.ElementList = _content_sources(doc, content) if objs is None else list(objs)
    return content


def _migrate_link_groups(doc):
    """Sheets of older files drew a hidden link group, which hid its objects in the 3D view:
    replace it with a SheetContent and show the objects again. True if something changed."""
    changed = False
    for old in [o for o in doc.Objects if o.TypeId == "App::LinkGroup" and getattr(o, AUTO, None) is not None]:
        floor = (old.FloorMin, old.FloorMax) if "FloorMax" in old.PropertiesList else None
        members = list(old.ElementList)
        new = make_content(doc, members, old.Label, floor)
        setattr(new, AUTO, getattr(old, AUTO))
        for view in doc.findObjects("TechDraw::DrawViewDraft"):
            if view.Source is not None and view.Source.Name == old.Name:
                view.Source = new
        old.ElementList = []
        doc.removeObject(old.Name)
        if App.GuiUp:
            for member in members:  # they were visible when they went in; the group hid them
                if member.ViewObject is not None:
                    member.ViewObject.Visibility = True
        changed = True
    return changed


def make_sheet(doc, sheet="auto", scale=None, objs=None, title=None, line_width=0.35, text_height=2.5, floor=None):
    """A page with the drawing at 1:scale, kept up to date. `floor` (zmin, zmax) limits it to one
    floor. Returns (page, view, sheet, scale, box)."""
    if objs is None:
        window_marks(doc)
    if objs is not None:
        content_objs = objs
    elif floor is not None:
        content_objs = floor_sources(doc, *floor)
    else:
        content_objs = sources(doc)
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
    content = make_content(doc, objs, f"{page.Label} - conteúdo", floor)
    view = doc.addObject("TechDraw::DrawViewDraft", "View")
    view.Source = content
    view.ScaleType = "Custom"
    view.Scale = 1 / scale
    view.LineWidth = float(line_width)
    view.FontSize = 2.0 * float(text_height)  # Draft's get_svg draws texts at half of FontSize
    view.LineSpacing = LINE_SPACING * float(text_height)  # room name and area apart (paper mm)
    page.addView(view)
    # X, Y is the centre of the view: the middle of the free area (after addView, which centres it).
    x0, y0, x1, y1 = drawing_area(size)
    view.X, view.Y = (x0 + x1) / 2, (y0 + y1) / 2
    doc.recompute()
    return page, view, sheet, scale, box


def _table_page(doc, owner, name, view_name, title, widths, sheet="A3", text_mm=2.5):
    """A page with the spreadsheet of `owner` (schedule or materials), following it as it grows."""
    doc.recompute()
    page, template, size = _page(doc, name, f"Folha - {title}", sheet)
    fill_title_block(template, title, "-", "")
    table = doc.addObject("TechDraw::DrawViewSpreadsheet", view_name)
    table.Source = owner.Sheet
    table.CellStart = "A1"
    table.CellEnd = owner.LastCell or "D5"
    page.addView(table)
    x0, y0, x1, y1 = drawing_area(size)
    # The view draws column widths and text sizes in pixels at 96 dpi: scale the table to the
    # free width, and size the text so it prints `text_mm` high.
    px_mm = 25.4 / 96
    scale = (x1 - x0) / (float(sum(widths)) * px_mm)
    table.ScaleType = "Custom"
    table.Scale = scale
    table.TextSize = text_mm / (px_mm * scale)
    table.X, table.Y = (x0 + x1) / 2, (y0 + y1) / 2
    doc.recompute()
    return page, table


def make_schedule_page(doc, title="Quadro de cargas", sheet="A3"):
    """A page with the load schedule table, following the table as it grows."""
    return _table_page(doc, model.schedule(doc), "SheetQuadroDeCargas", "VistaQuadroDeCargas", title, model.WIDTHS,
                       sheet, 2.2)


def make_materials_page(doc, title="Lista de materiais", sheet="A3"):
    from . import outputs

    owner = outputs.materials(doc)
    return _table_page(doc, owner, "SheetMateriais", "VistaMateriais", title, outputs.Materials.WIDTHS, sheet, 2.2)


def make_diagram_page(doc, title="Diagrama unifilar", sheet="A3"):
    """A page with the single-line diagram of the panels, redrawn when the circuits change."""
    from . import outputs

    diagram = outputs.diagram(doc)
    page, template, size = _page(doc, "SheetUnifilar", f"Folha - {title}", sheet)
    fill_title_block(template, title, "-", "")
    view = doc.addObject("TechDraw::DrawViewSymbol", "VistaUnifilar")
    page.addView(view)
    diagram.View = view
    doc.recompute()
    return page, view


def follow_schedule(doc):
    """Keep the range of table views (schedule, materials) on the whole table."""
    owners = [model.schedule(doc, create=False)]
    owners += model.items(doc, model.T_MATERIALS)
    for owner in owners:
        if owner is None or not owner.LastCell:
            continue
        for obj in doc.Objects:
            if obj.TypeId == "TechDraw::DrawViewSpreadsheet" and obj.Source == owner.Sheet \
                    and obj.CellEnd != owner.LastCell:
                obj.CellEnd = owner.LastCell


def sync(doc):
    """Rebuild the element list of every AutoSources sheet content; True if something changed."""
    changed = _migrate_link_groups(doc)
    for view in doc.findObjects("TechDraw::DrawViewDraft"):
        # Sheets made before the line spacing was set draw the room name over its area.
        source = view.Source
        spacing = LINE_SPACING * view.FontSize / 2
        if source is not None and getattr(source, AUTO, None) is not None and view.LineSpacing < spacing - 1e-6:
            view.LineSpacing = spacing
            changed = True
    for obj in doc.Objects:
        if _type(obj) == "SheetContent" and getattr(obj, AUTO, False):
            current = _content_sources(doc, obj)
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
        if getattr(doc, "Restoring", False):
            self._schedule(doc)  # still loading: try again once it is open
            return
        try:
            changed = sync(doc)
            from . import layout

            old = layout.stale(doc)
            for obj in old:
                obj.touch()
            if changed or old:
                doc.recompute()
        except Exception as e:
            App.Console.PrintWarning(f"Elétrica: folhas não atualizadas: {e}\n")

    def slotCreatedObject(self, obj):
        if not obj.TypeId.startswith("TechDraw::") and not is_content(obj):
            self._schedule(obj.Document)

    def slotDeletedObject(self, obj):
        if not obj.TypeId.startswith("TechDraw::") and not is_content(obj):
            self._schedule(obj.Document)

    def slotActivateDocument(self, doc):
        # Opening a file creates no objects as far as observers go: the sheets saved with it may
        # be out of date (an object moved to another floor, a file from an older version).
        self._schedule(doc)

    def slotRecomputedDocument(self, doc):
        if App.GuiUp:
            try:
                follow_schedule(doc)
            except Exception:
                pass
            # An object moved, raised to another floor or hidden changes what each sheet draws.
            self._schedule(doc)


_observer = None


def install_observer():
    global _observer
    if _observer is None:
        _observer = _Observer()
        App.addDocumentObserver(_observer)


def make_3d_page(doc, title=None, sheet="A3"):
    """A page with the isometric view of the 3D installation, coloured by circuit and kept up to date
    (drawn by model3d.iso_svg)."""
    from . import model3d

    obj = model3d.model3d(doc)
    page, template, size = _page(doc, "Sheet3D", f"Folha - {title or 'Instalação 3D'}", sheet)
    view = doc.addObject("TechDraw::DrawViewSymbol", "Vista3D")
    page.addView(view)
    obj.View = view
    obj.touch()
    doc.recompute()
    return page, view


def plan_notes():
    """What the plan leaves unwritten, to go on its sheet."""
    section = f"{model.DEFAULT_SECTION:g}".replace(".", ",")
    return ["NOTAS:",
            f"Condutores sem indicação de seção: {section} mm².",
            f"Eletrodutos sem indicação de diâmetro: ø{model.DEFAULT_CONDUIT} mm."]


def add_notes(doc, page, lines, text_mm=2.5):
    """Notes in the free strip left of the title block."""
    note = doc.addObject("TechDraw::DrawViewAnnotation", "Notas")
    note.Text = lines
    note.TextSize = text_mm
    page.addView(note)
    width = max(len(t) for t in lines) * text_mm * 0.55
    note.X, note.Y = MARGINS[0] + 2 + width / 2, MARGINS[2] - 15 - len(lines) * text_mm * 0.8
    return note


def make_electrical_sheets(doc, title=None, sheet="auto", scale=None, schedule=True, diagram=True, materials=True,
                           model_3d=True):
    """Every page of the electrical design, all kept up to date: the plan (one page per floor
    when items are on several floors), the load schedule, the single-line diagram and the bill of
    materials. Returns [(kind, page)]."""
    title = title or doc.Label
    pages, plans = [], []
    ranges = floor_ranges(doc)
    if ranges:
        for name, zmin, zmax in ranges:
            if not floor_sources(doc, zmin, zmax):
                continue
            page, view = make_sheet(doc, sheet, scale, title=f"{title} - planta elétrica - {name}",
                                    floor=(zmin, zmax))[:2]
            pages.append(("planta", page))
            plans.append((page, view))
    else:
        page, view = make_sheet(doc, sheet, scale, title=f"{title} - planta elétrica")[:2]
        pages.append(("planta", page))
        plans.append((page, view))
    for page, view in plans:
        add_notes(doc, page, plan_notes())
        add_legend(doc, page, view)
    if schedule:
        pages.append(("quadro_de_cargas", make_schedule_page(doc)[0]))
    if diagram:
        pages.append(("unifilar", make_diagram_page(doc)[0]))
    if materials:
        pages.append(("materiais", make_materials_page(doc)[0]))
    if model_3d:
        pages.append(("3d", make_3d_page(doc, title)[0]))
    return pages
