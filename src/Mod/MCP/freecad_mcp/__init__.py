# SPDX-License-Identifier: LGPL-2.1-or-later

"""Command server that lets an MCP bridge drive FreeCAD.

Commands are registered with ``registry.command`` in the ``handlers``
modules and called by name, with JSON parameters, by ``server``.
"""
