# SPDX-License-Identifier: LGPL-2.1-or-later

SET(MCP_SRCS
    Init.py
    InitGui.py
    TestMCP.py
    README.md
    freecad_mcp/__init__.py
    freecad_mcp/registry.py
    freecad_mcp/server.py
    freecad_mcp/units.py
    freecad_mcp/handlers/__init__.py
    freecad_mcp/handlers/common.py
    freecad_mcp/handlers/document.py
    freecad_mcp/handlers/draft2d.py
    freecad_mcp/handlers/electrical.py
    freecad_mcp/handlers/review.py
    freecad_mcp/review.py
    freecad_mcp/review_gui.py
    Resources/icons/MCP_Comment.svg
    freecad_mcp/handlers/bim3d.py
    freecad_mcp/handlers/techdraw.py
    freecad_mcp/handlers/python.py
    freecad_mcp/handlers/view.py
    mcp_server/freecad_mcp_server.py
    mcp_server/pdf_tools.py
    mcp_server/sample_plan.py
)
SOURCE_GROUP("" FILES ${MCP_SRCS})

ADD_CUSTOM_TARGET(MCP ALL
    SOURCES ${MCP_SRCS}
)

fc_copy_sources(MCP "${CMAKE_BINARY_DIR}/Mod/MCP" ${MCP_SRCS})

foreach(_file ${MCP_SRCS})
    get_filename_component(_dir ${_file} DIRECTORY)
    INSTALL(FILES ${_file} DESTINATION Mod/MCP/${_dir})
endforeach()
