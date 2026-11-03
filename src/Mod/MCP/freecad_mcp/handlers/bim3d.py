# SPDX-License-Identifier: LGPL-2.1-or-later

"""3D building commands built on the BIM workbench: levels, walls, doors, windows, spaces."""

import FreeCAD as App

from ..registry import CommandError, command
from ..units import length, point
from ..units import points as to_points
from .common import active_document, describe, get_object, recompute
from .draft2d import annotation_scale

DOOR_PRESET = "Simple door"
WINDOW_PRESET = "Open 1-pane"
# Frame sizes of the presets (mm): frame width/thickness, offsets; the BIM defaults.
PRESET_FRAME = {"h1": 50, "h2": 50, "h3": 50, "w1": 100, "w2": 50, "o1": 0, "o2": 50}


def _arch():
    import Arch

    return Arch


def _type(obj):
    return getattr(getattr(obj, "Proxy", None), "Type", None)


def _add_to_level(doc, obj, level):
    if not level:
        return
    lvl = get_object(doc, level)
    if obj not in lvl.Group:
        lvl.Group = lvl.Group + [obj]


def solid_box(shape):
    """Bounding box of the solids of a shape, or None."""
    if shape.isNull() or not shape.Solids:
        return None
    box = App.BoundBox(shape.Solids[0].BoundBox)
    for solid in shape.Solids[1:]:
        box.add(solid.BoundBox)
    return box


def _wall_info(wall):
    info = describe(wall)
    info.update(width=round(wall.Width.Value, 3), height=round(wall.Height.Value, 3), align=wall.Align)
    if wall.Base and hasattr(wall.Base, "Shape"):
        info["baseline_length"] = round(wall.Base.Shape.Length, 3)
    if not wall.Shape.isNull():
        info["volume_m3"] = round(wall.Shape.Volume / 1e9, 4)
    return info


@command("make_level", transaction=True)
def make_level(name="Térreo", elevation=0, height=None, building=None):
    """A building level (storey) at an elevation; walls and openings can be placed in it."""
    Arch = _arch()
    doc = active_document()
    level = Arch.makeFloor(name=name)
    level.Label = name
    level.Placement.Base.z = length(elevation, "elevation")
    if height is not None:
        level.Height = length(height, "height")
    if building:
        try:
            bld = get_object(doc, building)
        except CommandError:
            bld = Arch.makeBuilding(name=building)
            bld.Label = building
        bld.Group = bld.Group + [level]
    recompute(doc)
    return describe(level)


@command("make_wall", transaction=True)
def make_wall(start, end, width=150, height=2800, align="Center", level=None, label=None):
    """Straight wall on the baseline start->end (mm). align: Center, Left or Right of the baseline."""
    import Draft

    doc = active_document()
    base = Draft.make_line(point(start, "start"), point(end, "end"))
    return _make_wall_from(doc, base, width, height, align, level, label)


@command("make_walls", transaction=True)
def make_walls(points, closed=False, width=150, height=2800, align="Center", level=None, label=None):
    """One wall following a polyline (e.g. the outline of a building), closed or open."""
    import Draft

    doc = active_document()
    pts = to_points(points, "points")
    if len(pts) < 2:
        raise CommandError("A wall path needs at least 2 points")
    base = Draft.make_wire(pts, closed=bool(closed), face=False)
    return _make_wall_from(doc, base, width, height, align, level, label)


@command("make_walls_from_objects", transaction=True)
def make_walls_from_objects(names, width=150, height=2800, align="Center", level=None):
    """Turn existing lines/polylines (e.g. wall centre lines traced over a PDF) into walls."""
    doc = active_document(create=False)
    walls = [_make_wall_from(doc, get_object(doc, n), width, height, align, level, None, recompute_doc=False)
             for n in names]
    recompute(doc)
    return {"walls": [_wall_info(doc.getObject(w["name"])) for w in walls]}


def _make_wall_from(doc, base, width, height, align, level, label, recompute_doc=True):
    if align not in ("Center", "Left", "Right"):
        raise CommandError("align must be Center, Left or Right")
    wall = _arch().makeWall(base, width=length(width, "width"), height=length(height, "height"), align=align)
    if label:
        wall.Label = label
    _add_to_level(doc, wall, level)
    if recompute_doc:
        recompute(doc)
        return _wall_info(wall)
    return {"name": wall.Name}


def _point_on_baseline(wall, position):
    """Point and direction at ``position`` mm along the wall baseline (all its edges in turn)."""
    if wall.Base is None or not hasattr(wall.Base, "Shape"):
        raise CommandError(f"Wall {wall.Label} has no baseline")
    remaining = position
    edges = wall.Base.Shape.Wires[0].OrderedEdges if wall.Base.Shape.Wires else wall.Base.Shape.Edges
    for edge in edges:
        if remaining <= edge.Length + 1e-6:
            a, b = edge.Vertexes[0].Point, edge.Vertexes[-1].Point
            d = (b - a).normalize()
            return a + d * remaining, d
        remaining -= edge.Length
    raise CommandError(f"position {position} mm is beyond the wall length ({wall.Base.Shape.Length:.0f} mm)")


@command("add_opening", transaction=True)
def add_opening(wall, kind="door", position=0, width=None, height=None, sill=None, preset=None,
                flip=False, label=None):
    """Door or window hosted in a wall, cutting its hole.

    position: distance (mm) along the wall baseline, from its start to the near side of the opening.
    sill: height of the bottom of the opening above the wall base (0 for doors).
    preset: one of Arch.WindowPresets; defaults to "Simple door" / "Open 1-pane".
    flip: open to the other side of the wall.
    """
    Arch = _arch()
    doc = active_document(create=False)
    host = get_object(doc, wall)
    if _type(host) != "Wall":
        raise CommandError(f"{host.Label} is not a wall")
    is_door = kind == "door"
    if kind not in ("door", "window"):
        raise CommandError("kind must be 'door' or 'window'")
    width = length(width if width is not None else (800 if is_door else 1200), "width")
    height = length(height if height is not None else (2100 if is_door else 1200), "height")
    sill = length(sill if sill is not None else (0 if is_door else 1000), "sill")
    preset = preset or (DOOR_PRESET if is_door else WINDOW_PRESET)
    if preset not in Arch.WindowPresets:
        raise CommandError(f"Unknown preset {preset!r}; use one of {Arch.WindowPresets}")

    start, d = _point_on_baseline(host, length(position, "position"))
    up = App.Vector(0, 0, 1)
    right = d.cross(up)  # to the right of the baseline
    # Move from the baseline to the wall centre line. With align "Left" the baseline is the
    # left face of the wall, so the wall extends to the right of it, and vice versa.
    thickness = host.Width.Value
    if host.Align == "Left":
        start = start + right * (thickness / 2)
    elif host.Align == "Right":
        start = start - right * (thickness / 2)
    x_axis = d
    if flip:
        x_axis = -d
        start = start + d * width
    rotation = App.Rotation(x_axis, up, x_axis.cross(up), "XYZ")
    base_z = host.Placement.Base.z
    placement = App.Placement(start + App.Vector(0, 0, base_z + sill), rotation)
    win = Arch.makeWindowPreset(preset, width=width, height=height, placement=placement, **PRESET_FRAME)
    if win is None:
        raise CommandError("BIM could not create the opening")
    win.HoleDepth = 0  # cut through the whole wall thickness
    if is_door and hasattr(win, "IfcType"):
        win.IfcType = "Door"
    if is_door and hasattr(win, "SymbolPlan"):
        win.SymbolPlan = True  # door swing in plan views and sheets
    if label:
        win.Label = label
    # The preset frame is offset from its placement plane (by o2 and w2); centre it in the wall.
    # Only the solids count: the plan symbol (door swing) sticks out of the wall.
    doc.recompute()
    box = solid_box(win.Shape)
    if box is not None and win.Base is not None:
        off = (box.Center - start).dot(right)
        if abs(off) > 1e-6:
            base_pl = win.Base.Placement
            win.Base.Placement = App.Placement(base_pl.Base - right * off, base_pl.Rotation)
    win.Hosts = [host]
    for parent in host.InList:
        if _type(parent) == "BuildingPart" and win not in parent.Group:
            parent.Group = parent.Group + [win]
    recompute(doc)
    info = describe(win)
    info.update(host=host.Label, width=width, height=height, sill=sill, preset=preset,
                wall_volume_m3=round(host.Shape.Volume / 1e9, 4))
    return info


@command("make_space", transaction=True)
def make_space(name, boundary=None, object_name=None, height=2800, level=None):
    """A room (BIM space) from a closed boundary [[x, y], ...] in mm or an existing closed object,
    extruded to `height` (BIM spaces are volumes; their Area is the floor area)."""
    import Draft

    doc = active_document()
    if object_name:
        outline = get_object(doc, object_name)
    elif boundary:
        outline = Draft.make_wire(to_points(boundary, "boundary"), closed=True, face=True)
    else:
        raise CommandError("Give a boundary or an object_name")
    base = doc.addObject("Part::Extrusion", "SpaceVolume")
    base.Base = outline
    base.Dir = App.Vector(0, 0, length(height, "height"))
    base.Solid = True
    for obj in (outline, base):
        if App.GuiUp and obj.ViewObject:
            obj.ViewObject.Visibility = False
    doc.recompute()
    if not base.Shape.Solids:
        raise CommandError("The boundary is not a closed outline; cannot make a room from it")
    space = _arch().makeSpace(base)
    space.Label = name
    if App.GuiUp and space.ViewObject and hasattr(space.ViewObject, "FontSize"):
        # Room name and area sized like other annotations (paper size x drawing scale).
        scale = annotation_scale(doc)
        space.ViewObject.FontSize = 2.5 * scale
        space.ViewObject.FirstLine = 3.5 * scale
    _add_to_level(doc, space, level)
    recompute(doc)
    info = describe(space)
    if hasattr(space, "Area"):
        info["area_m2"] = round(space.Area.Value / 1e6, 3)
    return info


@command("list_window_presets")
def list_window_presets():
    return {"presets": list(_arch().WindowPresets), "door_default": DOOR_PRESET, "window_default": WINDOW_PRESET}
