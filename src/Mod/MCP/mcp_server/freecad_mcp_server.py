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

Electrical plans (Brazilian NBR 5444 symbols; Electrical workbench): items carry data (power,
circuit, room) and their symbol is drawn from it. set_drawing_scale first (e.g. 50); the default
system is 380/220 V (set_installation). Put a panel (quadro_terminal, label = its name, e.g. QD1)
on the plan. insert_electrical_symbol with potencia ('100VA', '2x100W', '3CV') and circuito
(number; created if missing, TUG/Iluminação by symbol); switches and lights share a letra.
create_circuit for two-phase 380 V (Bifásico) or three-phase circuits, and for feeders between
panels (feeds='QD2'). draw_conduit from the panel to the items, without circuits: the conduit
works out which circuits and conductors (phase, neutral, earth, returns) pass in it, and the
circuit lengths, voltage drop, grouping and conductor sections follow. Buildings with several
floors: give each floor's items and conduits its elevation (nivel: a BIM level name or a height)
and join the floors with circuito_sobe/circuito_desce at the same place (the riser).
load_summary gives totals, currents, phases and the service entrances; demand_summary the demand
and Cosern/Neoenergia entrance category; rebalance_phases spreads circuits over R, S, T;
check_installation runs the NBR 5410 checks (room minimums, RCDs, voltage drop);
bill_of_materials lists cables, conduits, boxes and protection; create_electrical_sheets makes
the plan (per floor), load schedule, single-line diagram and materials pages, which update
themselves. Change items with set_electrical_item.

Review comments ("X" balloons the person pins on the drawing with the X button):
when asked to apply the corrections, call list_review_comments (pending ones), then for each
comment focus_review_comment to see the area, and read its text, the object under the click
(target) and its surroundings. The comment is about that area, not only the target: consider
the nearby elements too. Fix it, check with a screenshot, then resolve_review_comment with a
note of what changed. Mark as fixed only what was really fixed; if something is unclear, leave
it pending and say why. Reopen (reopen_review_comment) only when the person asks.

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
                   width: int = 1600, height: int = 1200, fit: bool = True,
                   cut: bool | float | None = None) -> Image:
    """Image of the FreeCAD 3D view (top view by default, zoomed to fit), to compare with the source.
    cut: plan cut (walls cut at 1,5 m above the floor, showing door and window openings, as a floor
    plan): default on for the top view of a building; False for the roofline seen from above, or a
    height in mm."""
    result = fc("get_screenshot", direction=direction, width=width, height=height, fit=fit, cut=cut)
    return Image(path=result["path"])


@tool()
def set_view(direction: Literal["top", "bottom", "front", "rear", "left", "right", "iso",
                                "axonometric", "current"] = "top",
             fit: bool = True, cut: bool | float | None = None) -> dict:
    """Turn the camera of the person's 3D view (they watch it while you draw). cut: as in
    get_screenshot - the top view of a building shows the plan cut at 1,5 m."""
    return fc("set_view", direction=direction, fit=fit, cut=cut)


@tool()
def run_python(code: str) -> dict:
    """Run Python inside FreeCAD (App, Gui available). Assign to `result` to return a value.
    Use for anything the other tools do not cover. One undo step. In loops that create many
    objects, call `pulse()` (or `pulse("texto")`) so the person sees them appear as they are made."""
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
    "quadro_terminal", "quadro_geral", "motor", "caixa_passagem", "botao_campainha",
    "circuito_sobe", "circuito_desce", "padrao_entrada",
]
CircuitKind = Literal["Iluminação", "TUG", "TUE", "Motor", "Alimentador"]
Connection = Literal["Monofásico (F+N)", "Bifásico (F+F)", "Trifásico (3F)", "Trifásico (3F+N)"]


@tool()
def list_electrical_symbols() -> dict:
    """Electrical symbols (NBR 5444) with their default power, circuit kind and height; circuit kinds,
    connections and voltage systems."""
    return fc("list_electrical_symbols")


@tool()
def set_installation(system: Literal["380/220 V", "220/127 V"] | None = None, scale: float | None = None,
                     power_factor: float | None = None, ceiling_height: Length | None = None,
                     ambient_temperature: float | None = None, max_voltage_drop: float | None = None) -> dict:
    """Voltage system of the installation (default 380/220 V), drawing scale N (1:N) of the symbols,
    default power factor, ceiling height (ceiling conduits drop from it to the items; default 2.8 m),
    ambient temperature in °C (conductor correction; default 30) and maximum voltage drop in % (default 4)."""
    return fc("set_installation", system=system, scale=scale, power_factor=power_factor,
              ceiling_height=ceiling_height, ambient_temperature=ambient_temperature,
              max_voltage_drop=max_voltage_drop)


@tool()
def insert_electrical_symbol(symbol: ElectricalSymbol, position: Point, rotation: float = 0,
                             letra: str | None = None, circuito: int | None = None,
                             potencia: str | float | None = None, quantidade: int | None = None,
                             altura: Length | None = None, comodo: str | None = None,
                             fator_potencia: float | None = None, label: str | None = None,
                             nivel: str | Length | None = None, grupo_demanda: str | None = None) -> dict:
    """Insert an electrical item with its data; the NBR 5444 symbol (with power and circuit texts) is drawn
    from it at the drawing scale.

    Wall symbols (tomadas, ponto_luz_parede) start at `position` on the wall face and point along
    `rotation` (degrees; 0 = +X) into the room. potencia: '100VA', '600VA', '2x100W', '1,5CV' (default:
    the symbol's). circuito: circuit number (created if missing: TUG for sockets, Iluminação for lights).
    letra: command letter (switches 'a', 'ab'...; lights: the switch that commands them). altura: mounting
    height; comodo: room name (default: by position, from BIM spaces). nivel: floor (BIM level name or
    elevation) - the item goes to that height. label: for panels, the panel name (QD1, QD2...).
    grupo_demanda: demand group (default automatic, by circuit kind and names: chuveiro, forno,
    ar-condicionado...)."""
    return fc("insert_electrical_symbol", symbol=symbol, position=position, rotation=rotation, letra=letra,
              circuito=circuito, potencia=potencia, quantidade=quantidade, altura=altura, comodo=comodo,
              fator_potencia=fator_potencia, label=label, nivel=nivel, grupo_demanda=grupo_demanda)


@tool()
def set_electrical_item(name: str, symbol: ElectricalSymbol | None = None, position: Point | None = None,
                        rotation: float | None = None, circuito: int | None = None,
                        potencia: str | float | None = None, quantidade: int | None = None,
                        letra: str | None = None, altura: Length | None = None, comodo: str | None = None,
                        fator_potencia: float | None = None, nivel: str | Length | None = None,
                        grupo_demanda: str | None = None, label: str | None = None) -> dict:
    """Change an electrical item (only the fields given; circuito 0 removes it from its circuit). The symbol,
    the circuit totals, conduit routes, the load schedule and the sheets update."""
    return fc("set_electrical_item", name=name, symbol=symbol, position=position, rotation=rotation,
              circuito=circuito, potencia=potencia, quantidade=quantidade, letra=letra, altura=altura,
              comodo=comodo, fator_potencia=fator_potencia, nivel=nivel, grupo_demanda=grupo_demanda, label=label)


@tool()
def list_electrical_items(circuito: int | None = None, symbol: ElectricalSymbol | None = None) -> dict:
    """Electrical items with their data (optionally of one circuit or kind), all circuits, the conduits
    with the conductors in each, and the floors."""
    return fc("list_electrical_items", circuito=circuito, symbol=symbol)


@tool()
def create_circuit(number: int | None = None, description: str = "", kind: CircuitKind = "TUG",
                   connection: Connection = "Monofásico (F+N)", phases: str | None = None, panel: str = "QD1",
                   breaker: int = 0, section: float = 0, feeds: str = "", length_m: float = 0,
                   dr: bool | Literal["auto"] | None = None) -> dict:
    """Create a circuit. Monofásico = 220 V F+N; Bifásico = 380 V F+F (shower, AC); Trifásico = 380 V
    (motors, sub-panels). phases: 'R', 'ST', 'RST' (default: the least loaded). breaker (A) and
    section (mm²): 0 = use the suggested ones (by capacity with grouping and temperature factors, and
    voltage drop). feeds: name of the panel this circuit feeds (kind Alimentador; its load is that
    panel's, sized by demand). length_m: 0 = from the conduits. dr: RCD 30 mA (default: where NBR 5410
    requires it)."""
    return fc("create_circuit", number=number, description=description, kind=kind, connection=connection,
              phases=phases, panel=panel, breaker=breaker, section=section, feeds=feeds, length_m=length_m, dr=dr)


@tool()
def set_circuit(number: int, new_number: int | None = None, description: str | None = None,
                kind: CircuitKind | None = None, connection: Connection | None = None, phases: str | None = None,
                panel: str | None = None, breaker: int | None = None, section: float | None = None,
                feeds: str | None = None, length_m: float | None = None,
                dr: bool | Literal["auto"] | None = None) -> dict:
    """Change a circuit (only the fields given)."""
    return fc("set_circuit", number=number, new_number=new_number, description=description, kind=kind,
              connection=connection, phases=phases, panel=panel, breaker=breaker, section=section, feeds=feeds,
              length_m=length_m, dr=dr)


@tool()
def load_summary() -> dict:
    """Load of the installation: each circuit (VA, W, current, breaker, section, length, voltage drop,
    grouping, RCD, load per phase, warnings), totals, load and current per phase R/S/T, imbalance, per
    panel, service entrances (demand and category), floors, and items without a circuit."""
    return fc("load_summary")


@tool()
def demand_summary(panel: str | None = None) -> dict:
    """Installed load, demand (kVA) and service entrance category with main breaker, by the Neoenergia
    DIS-NOR-030 method (Cosern, Coelba, Celpe...), for each panel no feeder supplies (or `panel`)."""
    return fc("demand_summary", panel=panel)


@tool()
def bill_of_materials() -> dict:
    """Bill of materials: cables per section and function (from the conduit routes and the drops to the
    items), conduits, boxes, sockets/switches, breakers, RCDs, surge protectors and panels."""
    return fc("bill_of_materials")


@tool()
def rebalance_phases(panel: str | None = None) -> dict:
    """Redistribute the circuits over phases R, S, T to even out the load (all panels or one)."""
    return fc("rebalance_phases", panel=panel)


@tool()
def check_installation() -> dict:
    """NBR 5410 checks: lighting VA and sockets per room (BIM spaces, per floor), circuit protection,
    conductors with correction factors, voltage drop, RCDs, phase balance, items without a conduit,
    floor conduits indoors, sockets and switches placed in a door or window opening, and the service
    entrances. Estimates for review, not a design approval. Run it after placing items and fix what it
    reports before drawing sheets."""
    return fc("check_installation")


@tool()
def electrical_3d(page: bool = True, circuits: list[int] | None = None) -> dict:
    """3D model of the electrical installation, built from the plan and kept up to date: conduits above the
    ceiling (forro), in the floor or buried, drops down the walls to 4x2 boxes, octagonal boxes for the
    lights, the panel box, risers between floors and the service entrance (pole and meter box, place a
    "padrao_entrada" item and draw a conduit with installation "enterrado" to the panel). Coloured by
    circuit, fixed-radius bends. `page`: also an isometric TechDraw page. See it with
    get_screenshot(direction="iso"). `circuits`: show only these circuit numbers (one at a time or a
    few), in the 3D and on its page; [] shows them all again, None keeps the current choice."""
    return fc("electrical_3d", page=page, circuits=circuits)


@tool()
def create_electrical_sheets(sheet: Literal["auto", "A4", "A3", "A2", "A1", "A0"] = "auto",
                             title: str | None = None, schedule: bool = True, diagram: bool = True,
                             materials: bool = True, model_3d: bool = True) -> dict:
    """The plan pages (one per floor when items are on several floors), the load schedule, the
    single-line diagram, the bill of materials and an isometric page of the 3D installation; all update
    themselves when items change."""
    return fc("create_electrical_sheets", sheet=sheet, title=title, schedule=schedule, diagram=diagram,
              materials=materials, model_3d=model_3d)


@tool()
def draw_conduit(points: list[Point], installation: Literal["teto", "parede", "piso", "enterrado"] = "teto",
                 circuits: list[int] | None = None, extra: list[Literal["retorno", "fase", "neutro", "terra"]] | None = None,
                 diameter: str | int | None = None, marks_at: float | None = None, label: str | None = None,
                 automatic: bool | None = None, nivel: str | Length | None = None) -> dict:
    """Conduit (eletroduto) along points in mm: solid in ceiling/wall, dashed in the floor, dash-dot when
    buried ("enterrado": the service entrance run from a padrao_entrada item to the panel). Draw it from the
    panel to the items (ends on the items, or passing by them). Automatic (default when no circuits/extra
    are given): the circuits and conductors in it come from the items it reaches - phase, neutral and
    earth to loads, phase to switches, one return per switch letter between switch and lights - and the
    diameter is suggested. Manual: marks from `circuits` plus `extra` (e.g. ["retorno"]). Each group shows
    the circuit number above and its section below when it is not 2,5 mm²; the diameter is written when
    it is not 20 mm (the plan sheet carries a note). The marks go where they overlap no other text, or at
    `marks_at` (0..1 of the length) when given.
    Indoors (rooms with a ceramic floor) do not use "piso": run in the ceiling/forro and down the wall
    ("teto"/"parede"), even to sockets. "piso"/"enterrado" are for outdoor areas; the electrical check
    warns about floor conduits inside a room.
    nivel: floor (BIM level name or elevation)."""
    return fc("draw_conduit", points=points, installation=installation, circuits=circuits, extra=extra,
              diameter=diameter, marks_at=marks_at, label=label, automatic=automatic, nivel=nivel)


# --- review comments ("X") ------------------------------------------------------


@tool()
def list_review_comments(status: Literal["pendentes", "todos", "Aberto", "Corrigido", "Reaberto"] = "pendentes",
                         radius: Length | None = None) -> dict:
    """Review comments pinned on the drawing (X1, X2...): text, status, history, the object under the
    click (target), what was around it when the comment was made and what is around it now
    (within `radius` mm; default the radius recorded with each comment)."""
    return fc("list_review_comments", status=status, radius=radius)


@tool()
def focus_review_comment(number: int, size: Length | None = None) -> list:
    """Centre the top view on comment X<number> and return an image of the area (`size` mm high,
    default 4 x its surroundings radius) plus the comment details and surroundings."""
    info = fc("focus_review_comment", number=number, size=size)
    shot = fc("get_screenshot", direction="current", width=1400, height=1000, fit=False)
    return [Image(path=shot["path"]), json.dumps(info, ensure_ascii=False)]


@tool()
def resolve_review_comment(number: int, note: str = "") -> dict:
    """Mark comment X<number> as fixed (Corrigido), with a note of what was changed."""
    return fc("resolve_review_comment", number=number, note=note)


@tool()
def reopen_review_comment(number: int, reason: str = "") -> dict:
    """Reopen a fixed comment (Reaberto). Only when the person asks for it."""
    return fc("reopen_review_comment", number=number, reason=reason)


@tool()
def add_review_comment(position: Point, text: str, target: str | None = None, radius: Length = 1000) -> dict:
    """Pin a review comment yourself (e.g. a doubt for the person), at `position` mm, about `target`."""
    return fc("add_review_comment", position=position, text=text, target=target, radius=radius)


@tool()
def delete_review_comment(number: int) -> dict:
    """Delete comment X<number> (only when the person asks)."""
    return fc("delete_review_comment", number=number)


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
