# SPDX-License-Identifier: LGPL-2.1-or-later

"""MCP server (stdio) that lets Claude draw in FreeCAD.

Drawing tools are forwarded to the command server that FreeCAD's MCP module
runs on 127.0.0.1 (see freecad_mcp/server.py); PDF tools run here.

Environment:
  FREECAD_MCP_PORT  port of the FreeCAD command server (default 9875)
  FREECAD_EXE       FreeCAD executable or launcher, started when FreeCAD is not running

Run with the Python of the FreeCAD pixi environment:
  python freecad_mcp_server.py
"""

import functools
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Literal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdf_tools  # noqa: E402

try:  # mcp 1.x: classic initialize handshake, which every MCP client speaks
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp import Image
    from mcp.server.fastmcp.exceptions import ToolError
except ModuleNotFoundError:  # mcp 2.x
    from mcp.server.mcpserver import Image, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

PORT = int(os.environ.get("FREECAD_MCP_PORT", "9875"))
TIMEOUT = float(os.environ.get("FREECAD_MCP_TIMEOUT", "300"))

INSTRUCTIONS = """\
Draw in FreeCAD. Everything is modelled at real size, in mm: a 4 m wall is 4000 mm long,
whatever the drawing scale. Lengths accept numbers (mm) or strings with units ("3,5 m").

Reproducing a floor plan from a PDF:
1. pdf_info: is the page vector or raster, and is a scale written on it?
2. pdf_render_page to look at it.
3. Find the scale: written on the sheet (ESC 1:100), given by the user, or calibrated with
   pdf_calibrate from a dimension whose value you can read (two points + real length).
   Check it against at least one other written dimension before drawing.
4. Vector PDF: pdf_import_to_freecad reproduces every stroke exactly at real size.
   Raster PDF: read the coordinates on the rendered image and call draw_batch with
   coordinates="pdf_px" (or measure in mm and use coordinates="mm").
5. get_screenshot and compare with the PDF; get_document_summary to check sizes.
Each drawing command is one undo step (undo).

Electrical plans (Brazilian NBR 5444 symbols): set_drawing_scale first (e.g. 50), then
insert_electrical_symbol for outlets, switches, lights and panels, and draw_conduit for the conduits
with their conductors (fase, neutro, retorno, terra), circuit number and gauge.

Drawing tools take an optional layer (name, created if missing) and style:
{color: '#rrggbb', line_width: px, draw_style: Solid|Dashed|Dotted|Dashdot, font_size: mm}."""

mcp = MCPServer("freecad", instructions=INSTRUCTIONS)


def tool():
    """mcp.tool() that reports bad input and missing files as plain tool errors."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (ValueError, OSError, KeyError) as e:
                raise ToolError(f"{type(e).__name__}: {e}") from e

        return mcp.tool()(wrapper)

    return decorator

# PDF context of this session: the last PDF page, scale, origin and render used.
CTX: dict[str, Any] = {}


# --- FreeCAD connection ----------------------------------------------------


class FreeCADError(ToolError):
    """An error reported by FreeCAD; shown to the assistant as a plain tool error."""


def _send(method, params):
    with socket.create_connection(("127.0.0.1", PORT), timeout=5) as sock:
        sock.settimeout(TIMEOUT)
        sock.sendall(json.dumps({"id": 1, "method": method, "params": params}).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
    reply = json.loads(buf)
    if "error" in reply:
        err = reply["error"]
        raise FreeCADError(err["message"] + ("\n" + err["traceback"] if err.get("traceback") else ""))
    return reply.get("result")


def _ensure_running():
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
        return
    except OSError:
        pass
    exe = os.environ.get("FREECAD_EXE")
    if not exe:
        raise FreeCADError(f"FreeCAD is not running (nothing on 127.0.0.1:{PORT}) and FREECAD_EXE is not set")
    _launch_detached(exe)
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=1).close()
            return
        except OSError:
            time.sleep(1)
    raise FreeCADError("FreeCAD was started but its MCP command server did not answer within 3 minutes")


def _launch_detached(exe):
    """Start FreeCAD so that it outlives this server.

    MCP clients on Windows kill the server's whole process tree (a job object) when they
    disconnect; FreeCAD must break away from that job or the drawing would close with it.
    """
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  shell=exe.lower().endswith((".bat", ".cmd")))
    if os.name != "nt":
        subprocess.Popen([exe], start_new_session=True, **kwargs)
        return
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen([exe], creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        return
    except OSError:
        pass
    # The job does not allow breaking away. A process created through WMI is a child of the
    # WMI service, outside any job of ours.
    command = f'cmd /c "{exe}"' if kwargs["shell"] else f'"{exe}"'
    script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
              f"-Arguments @{{CommandLine='{command.replace(chr(39), chr(39) * 2)}'; "
              f"CurrentDirectory='{os.path.dirname(exe).replace(chr(39), chr(39) * 2)}'}}; "
              "exit $r.ReturnValue")
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise FreeCADError(f"Could not start FreeCAD through WMI (code {result.returncode}): {result.stderr.strip()}")


def fc(method, **params):
    _ensure_running()
    return _send(method, {k: v for k, v in params.items() if v is not None})


# --- documents and view -----------------------------------------------------


@tool()
def freecad_status() -> dict:
    """Check that FreeCAD is running (starting it if needed) and list its commands."""
    return fc("ping")


@tool()
def new_document(name: str = "Drawing") -> dict:
    """Create a new FreeCAD document and make it active."""
    return fc("new_document", name=name)


@tool()
def open_document(path: str) -> dict:
    """Open an .FCStd file."""
    return fc("open_document", path=path)


@tool()
def list_documents() -> list:
    """List open documents."""
    return fc("list_documents")


@tool()
def set_active_document(name: str) -> dict:
    """Make an open document the active one."""
    return fc("set_active_document", name=name)


@tool()
def save_document(path: str | None = None) -> dict:
    """Save the active document (to `path` if given, as .FCStd)."""
    return fc("save_document", path=path)


@tool()
def get_document_summary(max_objects: int = 500, types: list[str] | None = None) -> dict:
    """Objects of the active document: type, layer, bounding box (mm), length, area; plus the drawing extent."""
    return fc("get_document_summary", max_objects=max_objects, types=types)


@tool()
def get_object(name: str) -> dict:
    """Details and simple properties of one object (by name or label)."""
    return fc("get_object", name=name)


@tool()
def delete_objects(names: list[str]) -> dict:
    """Delete objects by name or label."""
    return fc("delete_objects", names=names)


@tool()
def undo(steps: int = 1) -> dict:
    """Undo the last commands (each drawing command is one step)."""
    return fc("undo", steps=steps)


@tool()
def redo(steps: int = 1) -> dict:
    """Redo undone commands."""
    return fc("redo", steps=steps)


@tool()
def set_drawing_scale(denominator: float) -> dict:
    """Store the drawing scale 1:denominator in the document. The model stays at real size;
    the scale is used for sheets and as the default for PDF conversion."""
    return fc("set_drawing_scale", denominator=denominator)


@tool()
def get_screenshot(direction: Literal["top", "bottom", "front", "rear", "left", "right", "iso",
                                      "axonometric", "current"] = "top",
                   width: int = 1600, height: int = 1200, fit: bool = True) -> Image:
    """Image of the FreeCAD 3D view (top view by default, zoomed to fit), to compare with the source."""
    result = fc("get_screenshot", direction=direction, width=width, height=height, fit=fit)
    return Image(path=result["path"])


@tool()
def run_python(code: str) -> dict:
    """Run Python inside FreeCAD (App, Gui available). Assign to `result` to return a value.
    Use for anything the other tools do not cover. One undo step."""
    return fc("run_python", code=code)


# --- 2D drawing -------------------------------------------------------------

Point = list[float | str]
Length = float | str
Style = dict[str, Any]


@tool()
def draw_line(p1: Point, p2: Point, layer: str | None = None, style: Style | None = None,
              label: str | None = None) -> dict:
    """Straight line between two points [x, y] in mm."""
    return fc("draw_line", p1=p1, p2=p2, layer=layer, style=style, label=label)


@tool()
def draw_polyline(points: list[Point], closed: bool = False, filled: bool = False, layer: str | None = None,
                  style: Style | None = None, label: str | None = None) -> dict:
    """Polyline through points [[x, y], ...] in mm; closed=True joins the last point to the first;
    filled=True also makes a face."""
    return fc("draw_polyline", points=points, closed=closed, filled=filled, layer=layer, style=style, label=label)


@tool()
def draw_rectangle(origin: Point, width: Length, height: Length, rotation: float = 0, filled: bool = False,
                   layer: str | None = None, style: Style | None = None, label: str | None = None) -> dict:
    """Rectangle with its bottom-left corner at origin, width along X, height along Y, rotated (degrees) about origin."""
    return fc("draw_rectangle", origin=origin, width=width, height=height, rotation=rotation, filled=filled,
              layer=layer, style=style, label=label)


@tool()
def draw_circle(center: Point, radius: Length, filled: bool = False, layer: str | None = None,
                style: Style | None = None, label: str | None = None) -> dict:
    """Circle."""
    return fc("draw_circle", center=center, radius=radius, filled=filled, layer=layer, style=style, label=label)


@tool()
def draw_arc(center: Point, radius: Length, start_angle: float, end_angle: float, layer: str | None = None,
             style: Style | None = None, label: str | None = None) -> dict:
    """Arc counter-clockwise from start_angle to end_angle (degrees, 0 = +X). E.g. a door swing."""
    return fc("draw_arc", center=center, radius=radius, start_angle=start_angle, end_angle=end_angle,
              layer=layer, style=style, label=label)


@tool()
def draw_text(position: Point, text: str, height: Length | None = None, rotation: float = 0,
              layer: str | None = None, style: Style | None = None, label: str | None = None) -> dict:
    """Text with its baseline start at position. height: real size in mm (e.g. 200 for a room name);
    without it the default paper font size is enlarged by the drawing scale (set_drawing_scale)."""
    return fc("draw_text", position=position, text=text, height=height, rotation=rotation,
              layer=layer, style=style, label=label)


@tool()
def draw_dimension(p1: Point, p2: Point, offset: Length | None = None, dim_point: Point | None = None,
                   layer: str | None = None, style: Style | None = None, label: str | None = None) -> dict:
    """Linear dimension between p1 and p2, its line offset to the left of p1->p2 by `offset` mm
    (default 500), or passing through dim_point. Text and arrows are sized for the drawing scale."""
    return fc("draw_dimension", p1=p1, p2=p2, offset=offset, dim_point=dim_point, layer=layer,
              style=style, label=label)


_POINT_KEYS = ("p1", "p2", "p3", "center", "origin", "position", "dim_point")
_LENGTH_KEYS = ("radius", "width", "height", "offset")


def _converter(coordinates):
    if coordinates == "mm":
        return None, None
    if "scale" not in CTX:
        raise ValueError("No PDF context: call pdf_set_context, pdf_calibrate or pdf_import_to_freecad first")
    scale, origin = CTX["scale"], CTX.get("origin_pt", [0.0, 0.0])
    if coordinates == "pdf_px":
        render = CTX.get("render")
        if render is None:
            raise ValueError("coordinates='pdf_px' needs a pdf_render_page call first")

        def to_pt(q):
            return pdf_tools.px_to_pt(q, render)

        unit = render["pt_per_px"]
    elif coordinates == "pdf_pt":
        def to_pt(q):
            return (q[0], q[1])

        unit = 1.0
    else:
        raise ValueError("coordinates must be 'mm', 'pdf_pt' or 'pdf_px'")

    def point(q):
        return pdf_tools.pt_to_mm(to_pt(q), origin, scale)

    def length(v):
        return float(v) * unit * pdf_tools.MM_PER_PT * scale

    return point, length


def _convert_item(item, point, length):
    item = dict(item)
    for key in _POINT_KEYS:
        if key in item:
            item[key] = point(item[key])
    if "points" in item:
        item["points"] = [point(q) for q in item["points"]]
    for key in _LENGTH_KEYS:
        if key in item and isinstance(item[key], (int, float)):
            item[key] = length(item[key])
    return item


@tool()
def draw_batch(items: list[dict], layer: str | None = None, style: Style | None = None,
               coordinates: Literal["mm", "pdf_pt", "pdf_px"] = "mm") -> dict:
    """Draw many items in one call (one undo step). Prefer this for whole plans.

    Each item: {"type": "line", "p1", "p2"} | {"type": "polyline", "points", "closed", "filled"} |
    {"type": "rectangle", "origin", "width", "height", "rotation", "filled"} | {"type": "circle", "center", "radius"} |
    {"type": "arc", "center", "radius", "start_angle", "end_angle"} | {"type": "arc_3points", "p1", "p2", "p3"} |
    {"type": "text", "position", "text", "height"} | {"type": "dimension", "p1", "p2", "offset"};
    any item may add "layer", "style", "label".

    coordinates: "mm" (real size), or "pdf_pt" / "pdf_px" to give positions read on the PDF page or on the
    last pdf_render_page image; they are converted with the current PDF scale and origin.
    """
    point, length = _converter(coordinates)
    if point:
        items = [_convert_item(i, point, length) for i in items]
    return fc("draw_batch", items=items, layer=layer, style=style)


@tool()
def create_layer(name: str, color: str | None = None, line_width: float | None = None) -> dict:
    """Create a layer (or update its color '#rrggbb' and line width)."""
    return fc("create_layer", name=name, color=color, line_width=line_width)


@tool()
def move_to_layer(names: list[str], layer: str) -> dict:
    """Move objects to a layer."""
    return fc("move_to_layer", names=names, layer=layer)


@tool()
def set_style(names: list[str], style: Style) -> dict:
    """Change the look of objects."""
    return fc("set_style", names=names, style=style)


@tool()
def transform_objects(names: list[str], move: Point | None = None, rotate: float = 0,
                      center: Point | None = None, scale: float | None = None, copy: bool = False) -> dict:
    """Move by a vector, rotate (degrees about `center`) and/or scale (factor about `center`) objects."""
    return fc("transform_objects", names=names, move=move, rotate=rotate, center=center, scale=scale, copy=copy)


# --- 3D building (BIM) -------------------------------------------------------


@tool()
def make_level(name: str = "Térreo", elevation: Length = 0, height: Length | None = None,
               building: str | None = None) -> dict:
    """Create a building level (storey) at an elevation; pass its name as `level` to walls, rooms..."""
    return fc("make_level", name=name, elevation=elevation, height=height, building=building)


@tool()
def make_wall(start: Point, end: Point, width: Length = 150, height: Length = 2800,
              align: Literal["Center", "Left", "Right"] = "Center", level: str | None = None,
              label: str | None = None) -> dict:
    """Straight wall on the baseline start->end (mm). width = thickness. align: the baseline is the wall
    centre line (Center), its left face (Left: wall extends to the right) or its right face (Right)."""
    return fc("make_wall", start=start, end=end, width=width, height=height, align=align, level=level, label=label)


@tool()
def make_walls(points: list[Point], closed: bool = False, width: Length = 150, height: Length = 2800,
               align: Literal["Center", "Left", "Right"] = "Center", level: str | None = None,
               label: str | None = None) -> dict:
    """One continuous wall along a polyline (e.g. the whole outer wall from its centre line, closed=True)."""
    return fc("make_walls", points=points, closed=closed, width=width, height=height, align=align,
              level=level, label=label)


@tool()
def make_walls_from_objects(names: list[str], width: Length = 150, height: Length = 2800,
                            align: Literal["Center", "Left", "Right"] = "Center", level: str | None = None) -> dict:
    """Turn existing 2D lines/polylines (e.g. wall centre lines drawn over a PDF) into walls."""
    return fc("make_walls_from_objects", names=names, width=width, height=height, align=align, level=level)


@tool()
def add_opening(wall: str, kind: Literal["door", "window"] = "door", position: Length = 0,
                width: Length | None = None, height: Length | None = None, sill: Length | None = None,
                preset: str | None = None, flip: bool = False, label: str | None = None) -> dict:
    """Door or window in a wall, cutting its hole. position: distance along the wall baseline from its start
    to the near jamb (for multi-segment walls, measured along the whole path). Defaults: door 800 x 2100,
    window 1200 x 1200 with sill 1000. preset: see list_window_presets. flip: open to the other side."""
    return fc("add_opening", wall=wall, kind=kind, position=position, width=width, height=height, sill=sill,
              preset=preset, flip=flip, label=label)


@tool()
def list_window_presets() -> dict:
    """Door and window types available for add_opening."""
    return fc("list_window_presets")


@tool()
def make_space(name: str, boundary: list[Point] | None = None, object_name: str | None = None,
               height: Length = 2800, level: str | None = None) -> dict:
    """A room with a name (e.g. "SALA") from its inner outline [[x, y], ...] or a closed object; returns its area."""
    return fc("make_space", name=name, boundary=boundary, object_name=object_name, height=height, level=level)


# --- electrical plans (NBR 5444) --------------------------------------------------

ElectricalSymbol = Literal[
    "tomada_baixa", "tomada_media", "tomada_alta", "tomada_piso",
    "interruptor_simples", "interruptor_simples_2", "interruptor_simples_3",
    "interruptor_paralelo", "interruptor_intermediario",
    "ponto_luz_teto", "ponto_luz_teto_embutido", "ponto_luz_parede", "luminaria_fluorescente",
    "quadro_terminal", "quadro_geral", "caixa_passagem", "botao_campainha",
    "circuito_sobe", "circuito_desce",
]


@tool()
def list_electrical_symbols() -> dict:
    """Electrical plan symbols available (Brazilian NBR 5444 convention) and their parameters."""
    return fc("list_electrical_symbols")


@tool()
def insert_electrical_symbol(symbol: ElectricalSymbol, position: Point, rotation: float = 0,
                             letra: str | None = None, circuito: str | int | None = None,
                             potencia: str | None = None, scale: float | None = None,
                             layer: str = "Elétrica", label: str | None = None) -> dict:
    """Insert an NBR 5444 symbol in a floor plan, sized for the drawing scale (set_drawing_scale; or
    `scale` N for 1:N).

    Wall symbols (tomadas, ponto_luz_parede) start at `position` on the wall face and point along
    `rotation` (degrees; 0 = +X) into the room. letra: command letter (switches: 'a', or 'ab'/'abc'
    for 2/3 sections; lights: the switch letter that commands them); circuito: circuit number;
    potencia: '300VA', '2x100W'... Switches: simples = empty circle, paralelo = filled,
    intermediario = half filled."""
    return fc("insert_electrical_symbol", symbol=symbol, position=position, rotation=rotation, letra=letra,
              circuito=circuito, potencia=potencia, scale=scale, layer=layer, label=label)


@tool()
def draw_conduit(points: list[Point], installation: Literal["teto", "parede", "piso"] = "teto",
                 diameter: str | int | None = None,
                 conductors: list[Literal["fase", "neutro", "retorno", "terra"]] | None = None,
                 circuit: str | int | None = None, gauge: str | float | None = None, marks_at: float = 0.5,
                 scale: float | None = None, layer: str = "Elétrica", label: str | None = None) -> dict:
    """Conduit (eletroduto) along points in mm: solid in ceiling/wall, dashed in the floor. The conductors
    inside are marked across it at `marks_at` (0..1 of the length) per NBR 5444: fase crosses the
    line, neutro crosses with a flag, retorno and terra (T) stand on one side; with the circuit number
    above and the gauge in mm² below (e.g. 2.5)."""
    return fc("draw_conduit", points=points, installation=installation, diameter=diameter,
              conductors=conductors, circuit=circuit, gauge=gauge, marks_at=marks_at, scale=scale,
              layer=layer, label=label)


# --- sheets -------------------------------------------------------------------


@tool()
def create_sheet(sheet: Literal["auto", "A4", "A3", "A2", "A1", "A0"] = "auto", scale: float | None = None,
                 objects: list[str] | None = None, title: str | None = None, text_height: float = 2.5) -> dict:
    """A TechDraw sheet with the drawing at 1:scale (default: the document drawing scale, else the largest
    standard scale that fits). sheet "auto" picks the smallest landscape ISO sheet that fits.
    objects: what to draw (default every layer, level and loose visible object).
    text_height: paper height (mm) of every text on the sheet (the sheet uses one size for all texts)."""
    return fc("create_sheet", sheet=sheet, scale=scale, objects=objects, title=title, text_height=text_height)


@tool()
def export_sheet(path: str, page: str | None = None) -> dict:
    """Export a sheet (default: the last one) to .pdf, .svg or .dxf, at its scale."""
    return fc("export_sheet", path=path, page=page)


@tool()
def export_model(path: str, objects: list[str] | None = None) -> dict:
    """Export the model at real size to .dxf, .svg, .ifc, .step, .stl or .obj (by extension)."""
    return fc("export_model", path=path, objects=objects)


# --- PDF ----------------------------------------------------------------------


@tool()
def pdf_info(path: str) -> dict:
    """Pages of a PDF: size, vector or raster, counts of lines/curves/images, and scales written in the text."""
    return pdf_tools.pdf_info(path)


@tool()
def pdf_render_page(path: str, page: int = 1, dpi: int = 100, crop: list[float] | None = None,
                    max_pixels: int = 2400) -> list:
    """Render a PDF page (or a crop [x0, y0, x1, y1] in PDF points, y up from the bottom-left) as an image.

    Also returns the pixel <-> PDF point mapping; this render becomes the reference for
    coordinates="pdf_px" and for pdf_calibrate(space="px"). Zoom in with crop + higher dpi to read details.
    """
    render = pdf_tools.render_page(path, page, dpi, crop, max_pixels)
    CTX.update(pdf=os.path.abspath(path), page=page, render=render)
    return [Image(path=render["path"]), json.dumps({k: v for k, v in render.items() if k != "path"})]


@tool()
def pdf_extract(path: str, page: int = 1, scale: float | None = None, crop: list[float] | None = None,
                min_length_mm: float = 0, linewidths: list[float] | None = None,
                colors: list[str] | None = None, include_text: bool = True, max_paths: int = 300) -> dict:
    """Vector strokes and texts of a PDF page converted to real mm (scale N for 1:N; the scale written
    on the sheet is used if unique). Returns groups by line width/color, texts and up to max_paths paths."""
    data = pdf_tools.extract_mm(path, page, scale, crop, None, min_length_mm, linewidths, colors, include_text)
    CTX.update(pdf=os.path.abspath(path), page=page, scale=data["scale"], origin_pt=data["origin_pt"])
    paths = data["paths"]
    data["path_count"] = len(paths)
    data["paths"] = paths[:max_paths]
    if len(paths) > max_paths:
        data["truncated"] = True
    return data


@tool()
def pdf_calibrate(p1: list[float], p2: list[float], real_length: Length,
                  space: Literal["pt", "px"] = "pt", apply: bool = True) -> dict:
    """Find the drawing scale from two points whose real distance is known (e.g. the ends of a
    dimension line reading 3,50). Points in PDF points ("pt") or pixels of the last render ("px").
    With apply=True the scale becomes the current PDF scale."""
    real_mm = _length_mm(real_length)
    result = pdf_tools.calibrate(p1, p2, real_mm, space, CTX.get("render"))
    if apply:
        CTX["scale"] = result["scale"]
    return result


@tool()
def pdf_set_context(path: str | None = None, page: int | None = None, scale: float | None = None,
                    origin_pt: list[float] | None = None) -> dict:
    """Set the PDF scale (N for 1:N) and origin (PDF point that becomes (0, 0) mm) used by
    draw_batch(coordinates="pdf_pt"|"pdf_px")."""
    for key, value in (("pdf", path), ("page", page), ("scale", scale), ("origin_pt", origin_pt)):
        if value is not None:
            CTX[key] = os.path.abspath(value) if key == "pdf" else value
    return {k: v for k, v in CTX.items() if k != "render"}


@tool()
def pdf_import_to_freecad(path: str, page: int = 1, scale: float | None = None, crop: list[float] | None = None,
                          min_length_mm: float = 1, linewidths: list[float] | None = None,
                          colors: list[str] | None = None, include_text: bool = True,
                          mode: Literal["auto", "draft", "reference"] = "auto", layer_prefix: str = "PDF") -> dict:
    """Reproduce the vector drawing of a PDF page in FreeCAD at real size.

    Strokes are grouped in layers by line width and color ("PDF lw0.70 #000000"), texts go to "PDF Text".
    mode "draft" makes editable Draft lines/polylines; "reference" makes one compound per layer (fast, for
    big drawings, to trace over); "auto" picks draft up to 2000 strokes. Crop/filters limit what is imported.
    """
    data = pdf_tools.extract_mm(path, page, scale, crop, None, min_length_mm, linewidths, colors, include_text)
    CTX.update(pdf=os.path.abspath(path), page=page, scale=data["scale"], origin_pt=data["origin_pt"])
    paths = data["paths"]
    if mode == "auto":
        mode = "draft" if len(paths) <= 2000 else "reference"
    fc("set_drawing_scale", denominator=data["scale"])
    created = 0
    errors = []
    by_layer: dict[str, list] = {}
    for p in paths:
        by_layer.setdefault(f"{layer_prefix} lw{p['linewidth']:.2f} {p['color']}", []).append(p)
    for layer, group in by_layer.items():
        if mode == "reference":
            res = fc("import_reference_geometry", polylines=[p["points"] for p in group], layer=layer)
            created += res.get("edges", 0)
            continue
        items = []
        for p in group:
            pts = p["points"]
            if len(pts) == 2 and not p["closed"]:
                items.append({"type": "line", "p1": pts[0], "p2": pts[1]})
            else:
                items.append({"type": "polyline", "points": pts, "closed": p["closed"]})
        for i in range(0, len(items), 500):
            res = fc("draw_batch", items=items[i:i + 500], layer=layer)
            created += res["created"]
            errors += res["errors"]
    texts = 0
    if include_text and data["texts"]:
        items = [{"type": "text", "position": t["position"], "text": t["text"],
                  "height": max(t["height"] * 0.7, 1)} for t in data["texts"] if t["text"].strip()]
        res = fc("draw_batch", items=items, layer=f"{layer_prefix} Text")
        texts = res["created"]
        errors += res["errors"]
    summary = fc("get_document_summary", max_objects=0)
    return {"scale": f"1:{data['scale']:g}", "origin_pt": data["origin_pt"], "mode": mode,
            "strokes_created": created, "texts_created": texts, "layers": sorted(by_layer),
            "errors": errors[:20], "extent_mm": summary.get("extent_mm")}


def _length_mm(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    units = {"mm": 1, "cm": 10, "m": 1000, "km": 1e6, "in": 25.4, "ft": 304.8}
    for unit in sorted(units, key=len, reverse=True):
        if text.endswith(unit):
            return float(text[: -len(unit)]) * units[unit]
    return float(text)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
