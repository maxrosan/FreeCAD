# SPDX-License-Identifier: LGPL-2.1-or-later

"""Command registry and dispatch for the MCP command server."""

import traceback

import FreeCAD as App

_commands = {}


class CommandError(Exception):
    """Error reported back to the caller without a traceback."""


def command(name, transaction=False):
    """Register a function as a command.

    With ``transaction=True`` the call runs inside an undo transaction on the
    active document, so each command is one Undo step.
    """

    def decorator(func):
        _commands[name] = (func, transaction)
        return func

    return decorator


def names():
    return sorted(_commands)


def call(name, params=None):
    """Run a command and return ``{"result": ...}`` or ``{"error": ...}``."""
    _load_handlers()
    if name not in _commands:
        return {"error": {"message": f"Unknown command: {name}"}}
    func, transaction = _commands[name]
    doc = App.ActiveDocument if transaction else None
    if doc:
        doc.openTransaction(f"MCP: {name}")
    try:
        result = func(**(params or {}))
    except Exception as e:
        if doc:
            doc.abortTransaction()
        error = {"message": f"{type(e).__name__}: {e}"}
        if not isinstance(e, (CommandError, TypeError, ValueError)):
            error["traceback"] = traceback.format_exc()
        return {"error": error}
    if doc:
        doc.commitTransaction()
    return {"result": result}


_loaded = False


def _load_handlers():
    global _loaded
    if _loaded:
        return
    from .handlers import bim3d, document, draft2d, python, techdraw, view  # noqa: F401

    _loaded = True
