# MCP module

Lets an AI assistant that speaks the Model Context Protocol (for example Claude) draw in FreeCAD:
2D drawings with Draft, buildings with BIM, sheets at scale with TechDraw, and floor plans
reproduced from PDF files.

## How it works

```
Claude ──stdio (MCP)──► mcp_server/freecad_mcp_server.py ──TCP 127.0.0.1:9875──► FreeCAD
                         PDF reading runs here                 freecad_mcp/server.py
```

* `InitGui.py` starts a command server inside FreeCAD, listening only on 127.0.0.1.
  Requests are one JSON object per line: `{"id", "method", "params"}`.
  Commands run on the GUI thread, each in its own undo transaction.
* `mcp_server/freecad_mcp_server.py` is the MCP server the assistant runs. It forwards
  drawing tools to FreeCAD, and reads PDFs itself with pdfplumber and pypdfium2.
  When FreeCAD is not running it starts `FREECAD_EXE`.

Everything is modelled at real size in mm. The drawing scale (1:N) only converts PDF
measurements to real sizes and sets the scale of TechDraw sheets.

## Preferences

`User parameter:BaseApp/Preferences/Mod/MCP`: `AutoStart` (bool, default true),
`Port` (int, default 9875).

## Registering with Claude Code

```
claude mcp add freecad --scope user \
  --env FREECAD_EXE=<path to FreeCAD.exe or a launcher> \
  -- <python of the FreeCAD environment> <install dir>/Mod/MCP/mcp_server/freecad_mcp_server.py
```

The Python running the MCP server needs the `mcp`, `pdfplumber` and `pypdfium2` packages
(they are in the pixi environment of this repository). `mcp` is pinned below 2: the 2.x SDK
locks a connection to the 2026-07-28 protocol when the client opens with it and then refuses
the `initialize` fallback that current clients send, so they fail to connect. The server also
runs on mcp 2.x for clients that speak only the new protocol.

On Windows, MCP clients kill the server's process tree when they disconnect; the server
starts FreeCAD outside that tree (through WMI if needed), so the drawing stays open.

## Tools

* Documents and view: `new_document`, `get_document_summary`, `get_screenshot`, `undo`, `run_python`...
* 2D (Draft): `draw_line`, `draw_polyline`, `draw_rectangle`, `draw_arc`, `draw_text`,
  `draw_dimension`, `draw_batch`, layers and styles.
* 3D (BIM): `make_level`, `make_wall`, `make_walls`, `make_walls_from_objects`, `add_opening`
  (doors and windows hosted in walls), `make_space` (rooms with area).
* Electrical plans: `insert_electrical_symbol` and `draw_conduit`, with the symbols of the
  Brazilian NBR 5444 (outlets low/mid/high and in the floor, switches simple/two-way/intermediate,
  ceiling/wall/fluorescent lights, panels, junction box, bell button, rising/falling circuits)
  and the conductor marks (fase, neutro, retorno, terra). Symbols are data in
  `freecad_mcp/handlers/electrical.py`, in paper mm, scaled by the drawing scale on insertion.
  NBR 5444 was cancelled in 2014 without replacement; its symbols are still the ones in use.
* Review comments: the **X** button (File toolbar and Tools menu of every workbench) pins a
  comment where you click next: a dot with "X<n>: text", red when open, green when fixed,
  orange when reopened. Each comment records the object under the click, what was around it
  (20 mm on paper, 1 m at 1:50) and a history; double-click it to edit, mark fixed or reopen.
  The assistant works through them with `list_review_comments`, `focus_review_comment` (an
  image of the area), `resolve_review_comment` and `reopen_review_comment`. Comments are saved
  with the document and never go on sheets. Code: `freecad_mcp/review.py`, `review_gui.py`.
* Sheets (TechDraw): `create_sheet` (ISO A4 to A0 at a scale), `export_sheet` (PDF, SVG, DXF),
  `export_model` (DXF, IFC, STEP...).
* PDF: `pdf_info`, `pdf_render_page`, `pdf_extract`, `pdf_calibrate`, `pdf_set_context`,
  `pdf_import_to_freecad`. Vector PDFs are imported exactly; for scans the assistant reads the
  rendered image, calibrates the scale with a known dimension and draws with
  `draw_batch(coordinates="pdf_px")`.

## Tests

`FreeCADCmd -t TestMCP`
