# Electrical module (Elétrica)

Building electrical plans in FreeCAD, Brazilian conventions:

* **NBR 5444 symbols that carry data.** Each outlet, light, switch, panel or motor is an object
  with its kind, position, rotation, power (VA, W or CV), quantity, power factor, circuit,
  command letter, mounting height and room. Its symbol, texts included, is drawn from that data
  at the drawing scale; change the data (or move/rotate it with Draft) and it is redrawn.
* **Circuits** on 380/220 V (default) or 220/127 V systems: single-phase 220 V (F+N), two-phase
  380 V (F+F), three-phase 380 V (3F or 3F+N). Each circuit computes its load, design current,
  suggested breaker and conductor section, and its load on phases R, S and T. New circuits go to
  the least loaded phase; "Equilibrar fases" redistributes them all.
* **Conduits** whose conductor marks (fase, neutro, terra, plus returns) come from the circuits
  in them; floor conduits are dashed.
* **Load schedule** ("quadro de cargas"): a spreadsheet rebuilt on every recompute.
* **Checks** (basic NBR 5410): lighting VA and number/VA of sockets per room (BIM spaces), circuit
  protection and conductors, phase balance. Estimates for review, not a design approval.
* **Sheets that stay up to date**: the plan sheet draws everything in the document and follows
  objects added or deleted later; the schedule page follows the table as it grows.

## Using it

Switch to the **Elétrica** workbench. Toolbars: Tomadas, Interruptores, Iluminação, Quadros e
outros (one button per symbol), Instalação (circuit, conduit, load schedule, sheets, checks,
phase balance, voltage system and scale).

Placing an item: click its button, fill its data in the task panel (the last values used for
that kind are remembered), then click in the plan; Esc ends. Wall items (sockets, wall lights)
go to the face of the nearest wall and point toward the side you clicked. Double-click any item,
circuit or conduit to edit it. Deleting an item updates its circuit, the schedule and sheets.

## Code

* `freecad_electrical/symbols.py`: the symbol library (data, paper mm).
* `freecad_electrical/model.py`: installation, points, circuits, conduits, load schedule.
* `freecad_electrical/calc.py`: currents, breakers, sections, phases, NBR 5410 minimums.
* `freecad_electrical/checks.py`, `sheets.py`, `gui.py`, `geometry.py`.

The MCP module (`src/Mod/MCP`) uses this module for its electrical tools.

Tests: `FreeCADCmd -t TestElectrical`.
