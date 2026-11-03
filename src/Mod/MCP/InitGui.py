# SPDX-License-Identifier: LGPL-2.1-or-later

# MCP module for FreeCAD
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA

"""MCP module for FreeCAD.

Starts a local command server that the FreeCAD MCP bridge
(mcp_server/freecad_mcp_server.py) talks to.
"""

p = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/MCP")

if p.GetBool("AutoStart", True):
    try:
        import freecad_mcp.server

        freecad_mcp.server.start(p.GetInt("Port", 9875))
    except Exception as e:
        FreeCAD.Console.PrintError(f"MCP: could not start the command server: {e}\n")
