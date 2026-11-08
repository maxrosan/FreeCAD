# SPDX-License-Identifier: LGPL-2.1-or-later

# Electrical workbench for FreeCAD


class ElectricalWorkbench(Workbench):
    """Electrical plans: NBR 5444 symbols with data, circuits, load schedule, checks."""

    MenuText = "Elétrica"
    ToolTip = "Projeto elétrico predial: símbolos NBR 5444 com dados, circuitos 380/220 V, quadro de cargas"

    def __init__(self):
        import os

        import freecad_electrical

        self.__class__.Icon = os.path.join(os.path.dirname(freecad_electrical.__file__), "..", "Resources",
                                           "icons", "ElectricalWorkbench.svg")

    def Initialize(self):
        import DraftTools  # noqa: F401  (the snapper used to place items)
        from freecad_electrical import gui

        gui.register_commands()
        for title, commands in gui.toolbars():
            self.appendToolbar(title, commands)
            self.appendMenu(["&Elétrica", title], commands)
        self.appendToolbar("Encaixe", ["Draft_Snap_Lock", "Draft_Snap_Endpoint", "Draft_Snap_Midpoint",
                                       "Draft_Snap_Perpendicular", "Draft_Snap_Near", "Draft_Snap_Ortho"])

    def Activated(self):
        if hasattr(FreeCADGui, "Snapper"):
            FreeCADGui.Snapper.show()

    def Deactivated(self):
        if hasattr(FreeCADGui, "Snapper"):
            FreeCADGui.Snapper.hide()

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(ElectricalWorkbench())
