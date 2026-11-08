# SPDX-License-Identifier: LGPL-2.1-or-later

SET(Electrical_SRCS
    Init.py
    InitGui.py
    TestElectrical.py
    README.md
    freecad_electrical/__init__.py
    freecad_electrical/calc.py
    freecad_electrical/checks.py
    freecad_electrical/geometry.py
    freecad_electrical/gui.py
    freecad_electrical/model.py
    freecad_electrical/network.py
    freecad_electrical/outputs.py
    freecad_electrical/sheets.py
    freecad_electrical/symbols.py
    Resources/icons/ElectricalWorkbench.svg
)
SOURCE_GROUP("" FILES ${Electrical_SRCS})

ADD_CUSTOM_TARGET(Electrical ALL
    SOURCES ${Electrical_SRCS}
)

fc_copy_sources(Electrical "${CMAKE_BINARY_DIR}/Mod/Electrical" ${Electrical_SRCS})

foreach(_file ${Electrical_SRCS})
    get_filename_component(_dir ${_file} DIRECTORY)
    INSTALL(FILES ${_file} DESTINATION Mod/Electrical/${_dir})
endforeach()
