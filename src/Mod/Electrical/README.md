# Electrical module (Elétrica)

Building electrical plans in FreeCAD, Brazilian conventions:

* **NBR 5444 symbols that carry data.** Each outlet, light, switch, panel or motor is an object
  with its kind, position, rotation, power (VA, W or CV), quantity, power factor, circuit,
  command letter, mounting height, room and demand group. Its symbol, texts included, is drawn
  from that data at the drawing scale; change the data (or move/rotate it with Draft) and it is
  redrawn.
* **Circuits** on 380/220 V (default) or 220/127 V systems: single-phase 220 V (F+N), two-phase
  380 V (F+F), three-phase 380 V (3F or 3F+N). Each circuit computes its load, design current,
  suggested breaker, its load on phases R, S and T, and a conductor section that meets the
  minimum for its kind, carries the breaker current after the grouping and temperature factors
  (NBR 5410 tables 40 and 42; lightly loaded circuits are left out of the grouping, 6.2.5.5.6)
  and keeps the voltage drop within 4 %. A 30 mA RCD is added where NBR 5410 5.1.3.2.2 requires
  it (bathrooms, kitchens and service areas, outdoor sockets). New circuits go to the least
  loaded phase; "Equilibrar fases" redistributes them all.
* **Conduits that know what they carry.** Drawn from the panel to the items, an automatic conduit
  works out which circuits pass in it and their conductors: phase, neutral and earth to the
  loads, phase to the switches, one return per switch letter between switch and lights. Circuit
  lengths (with the drops from the ceiling to each item) give the voltage drop; the most crowded
  run gives the grouping; the conductors give the suggested diameter. Floor and ceiling runs meet
  only at items (panels, boxes). Manual conduits keep the circuits and conductors you give them.
* **Several floors and panels.** Items and conduits sit at their floor's elevation; risers
  ("circuito sobe/desce" at the same place) join the floors. A feeder circuit (`Feeds` = QD2)
  carries the load of the panel it feeds, sized by its demand, and is routed through the risers.
* **Demand and service entrance** by the Neoenergia DIS-NOR-030 method (Cosern, Coelba, Celpe,
  Brasília, Elektro): demand factors per group (lighting and sockets, showers and heaters,
  appliances, AC, motors...) and the entrance category with its main breaker (M2/M3 or T5 to T8
  on 380/220 V). Each panel no feeder supplies is a consumer unit.
* **Generated documents, kept up to date**: the load schedule ("quadro de cargas", grouped by
  panel, with lengths, voltage drop, RCDs and the entrance), the single-line diagram ("diagrama
  unifilar", an SVG on a TechDraw page) and the bill of materials (cables per section and
  function, conduits, boxes, devices, breakers, RCDs, surge protectors, panels with spare ways).
* **Checks** (NBR 5410): lighting VA and number/VA of sockets per room (BIM spaces of each floor),
  circuit protection, conductors and voltage drop, RCDs, phase balance, items no conduit reaches.
  Estimates for review, not a design approval.
* **Sheets that stay up to date**: one plan page per floor (or one for the whole document), and
  pages for the schedule, the single-line diagram and the materials.
* **Legible plans.** Texts are filled glyphs without an outline (an outline makes small text look
  bold), in the 3D view and on sheets. The power and circuit of each item, and the conductor
  marks of each conduit, go where they overlap the least: around the symbol, along the conduit or
  on its other side, clear of symbols, conduits, walls and other texts. A room name that falls on
  an item moves aside (unless it was moved by hand). Sections of 2,5 mm² and ø20 conduits are not
  written; a note on the plan sheet says so.

## Using it

Switch to the **Elétrica** workbench. Toolbars: Tomadas, Interruptores, Iluminação, Quadros e
outros (one button per symbol), Instalação (circuit, conduit, load schedule, single-line diagram,
materials, demand, sheets, checks, phase balance, installation settings).

Placing an item: click its button, fill its data in the task panel (the last values used for
that kind are remembered), then click in the plan; Esc ends. Wall items (sockets, wall lights)
go to the face of the nearest wall and point toward the side you clicked. Name panels after
their circuits' panel (QD1, QD2...). Draw conduits from the panel to the items with
"Eletroduto" (automatic by default). Double-click any item, circuit or conduit to edit it.
Deleting an item updates its circuit, the routes, the schedule and the sheets.

## Code

* `freecad_electrical/symbols.py`: the symbol library (data, paper mm).
* `freecad_electrical/model.py`: installation, points, circuits, conduits, load schedule, demand.
* `freecad_electrical/network.py`: the conduit graph and the routes of the circuits.
* `freecad_electrical/calc.py`: currents, breakers, sections with corrections, voltage drop, RCDs,
  phases, NBR 5410 minimums, demand and entrance categories, conduit sizes.
* `freecad_electrical/outputs.py`: bill of materials and single-line diagram.
* `freecad_electrical/layout.py`: where the movable texts go, floor by floor.
* `freecad_electrical/checks.py`, `sheets.py`, `gui.py`, `geometry.py`.

The MCP module (`src/Mod/MCP`) uses this module for its electrical tools.

Tests: `FreeCADCmd -t TestElectrical`.
