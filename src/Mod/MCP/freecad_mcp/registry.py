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
    from . import live

    doc = App.ActiveDocument if transaction else None
    if doc:
        doc.openTransaction(f"MCP: {name}")
    live.begin(name)
    try:
        result = func(**(params or {}))
    except Exception as e:
        _end_transaction(doc, commit=False)
        error = {"message": f"{type(e).__name__}: {e}"}
        if not isinstance(e, (CommandError, TypeError, ValueError)):
            error["traceback"] = traceback.format_exc()
        return {"error": error}
    finally:
        live.end()
    _end_transaction(doc, commit=True)
    return {"result": result}


def _end_transaction(doc, commit):
    """Close the undo transaction, unless the command closed the document itself
    (run_python code may close it and open another with the same name)."""
    if doc is None or not _alive(doc):
        return
    if commit:
        doc.commitTransaction()
    else:
        doc.abortTransaction()


def _alive(doc):
    try:
        doc.Name  # any attribute access fails once the document is closed
    except Exception:
        return False
    return True


_loaded = False


def _load_handlers():
    global _loaded
    if _loaded:
        return
    from .handlers import bim3d, document, draft2d, electrical, python, review, techdraw, view  # noqa: F401

    _loaded = True
