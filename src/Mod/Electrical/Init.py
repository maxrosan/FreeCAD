# SPDX-License-Identifier: LGPL-2.1-or-later

# Electrical module for FreeCAD

FreeCAD.__unit_test__ += ["TestElectrical"]

# Keep circuit totals and sheets up to date when items are added or deleted.
try:
    import freecad_electrical.model
    import freecad_electrical.sheets

    freecad_electrical.model.install_observer()
    freecad_electrical.sheets.install_observer()
except Exception as e:
    FreeCAD.Console.PrintError(f"Electrical: observers not installed: {e}\n")
