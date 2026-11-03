# SPDX-License-Identifier: LGPL-2.1-or-later

"""Parsing of lengths, points, angles and colors given by MCP clients.

Lengths are returned in mm, the internal FreeCAD unit. A bare number is
already mm; a string may carry a unit ("3.5 m", "350 cm", "3,5 m").
"""

import re

import FreeCAD as App

_DECIMAL_COMMA = re.compile(r"(\d),(\d)")


def length(value, name="length"):
    """Return ``value`` as a float in mm."""
    if isinstance(value, bool):
        raise ValueError(f"{name}: expected a length, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = _DECIMAL_COMMA.sub(r"\1.\2", value.strip())
        try:
            quantity = App.Units.Quantity(text)
        except Exception as e:
            raise ValueError(f"{name}: cannot read {value!r} as a length") from e
        if quantity.Unit == App.Units.Unit():
            return float(quantity.Value)
        if quantity.Unit != App.Units.Unit("mm"):
            raise ValueError(f"{name}: {value!r} is not a length")
        return float(quantity.Value)
    raise ValueError(f"{name}: expected a number in mm or a string with a unit, got {value!r}")


def point(value, name="point"):
    """Return ``[x, y]``, ``[x, y, z]`` or ``{"x":..,"y":..,"z":..}`` as a Vector in mm."""
    if isinstance(value, dict):
        coords = [value.get("x", 0), value.get("y", 0), value.get("z", 0)]
    elif isinstance(value, (list, tuple)) and len(value) in (2, 3):
        coords = list(value) + [0] * (3 - len(value))
    else:
        raise ValueError(f"{name}: expected [x, y] or [x, y, z], got {value!r}")
    return App.Vector(*(length(c, name) for c in coords))


def points(values, name="points"):
    return [point(v, f"{name}[{i}]") for i, v in enumerate(values)]


def angle(value, name="angle"):
    """Return an angle in degrees. Strings may carry a unit ("90 deg", "1.57 rad")."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        quantity = App.Units.Quantity(_DECIMAL_COMMA.sub(r"\1.\2", value.strip()))
        if quantity.Unit == App.Units.Unit():
            return float(quantity.Value)
        return float(quantity.getValueAs("deg"))
    raise ValueError(f"{name}: expected an angle in degrees, got {value!r}")


def color(value, name="color"):
    """Return an (r, g, b) tuple in 0..1 from "#rrggbb", [r, g, b] (0..1 or 0..255)."""
    if isinstance(value, str):
        text = value.lstrip("#")
        if len(text) != 6:
            raise ValueError(f"{name}: expected '#rrggbb', got {value!r}")
        return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        rgb = [float(c) for c in value[:3]]
        if any(c > 1 for c in rgb):
            rgb = [c / 255 for c in rgb]
        return tuple(rgb)
    raise ValueError(f"{name}: expected '#rrggbb' or [r, g, b], got {value!r}")


def vec(v):
    """Vector as a JSON-friendly list, rounded to 1/1000 mm."""
    return [round(v.x, 3), round(v.y, 3), round(v.z, 3)]
