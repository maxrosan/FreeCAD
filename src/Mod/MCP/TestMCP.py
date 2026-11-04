# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests for the MCP module. Run with: FreeCADCmd -t TestMCP"""

import json
import os
import sys
import tempfile
import unittest

import FreeCAD as App

from freecad_mcp import registry, units

_MCP_SERVER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server")


def call(_command, **params):
    reply = registry.call(_command, params)
    if "error" in reply:
        raise AssertionError(f"{_command} failed: {reply['error']}")
    return reply["result"]


class TestUnits(unittest.TestCase):
    def test_lengths(self):
        self.assertEqual(units.length(1500), 1500.0)
        self.assertAlmostEqual(units.length("3.5 m"), 3500.0)
        self.assertAlmostEqual(units.length("3,5 m"), 3500.0)
        self.assertAlmostEqual(units.length("35 cm"), 350.0)
        self.assertAlmostEqual(units.length("120"), 120.0)
        with self.assertRaises(ValueError):
            units.length("2 kg")

    def test_points_angles_colors(self):
        self.assertEqual(units.point([1, "2 m"]), App.Vector(1, 2000, 0))
        self.assertAlmostEqual(units.angle("90 deg"), 90.0)
        self.assertEqual(units.color("#ff0000"), (1.0, 0.0, 0.0))
        self.assertEqual(units.color([0, 0, 255]), (0.0, 0.0, 1.0))


class TestDraft2D(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("MCPTest")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_line_and_layer(self):
        info = call("draw_line", p1=[0, 0], p2=["4 m", 0], layer="Walls")
        self.assertAlmostEqual(info["length"], 4000.0, places=3)
        self.assertEqual(info["layer"], "Walls")

    def test_rectangle_rotation(self):
        info = call("draw_rectangle", origin=[0, 0], width=3000, height=2000, rotation=90)
        box = info["bbox"]
        self.assertAlmostEqual(box["max"][1] - box["min"][1], 3000.0, places=3)
        self.assertAlmostEqual(box["max"][0] - box["min"][0], 2000.0, places=3)

    def test_arc(self):
        info = call("draw_arc", center=[0, 0], radius=800, start_angle=0, end_angle=90)
        self.assertAlmostEqual(info["length"], 800 * 3.141592653589793 / 2, places=2)

    def test_batch_and_undo(self):
        res = call("draw_batch", items=[
            {"type": "polyline", "points": [[0, 0], [10000, 0], [10000, 6000], [0, 6000]], "closed": True},
            {"type": "text", "position": [2500, 3000], "text": "SALA", "height": 300},
            {"type": "dimension", "p1": [0, 0], "p2": [10000, 0], "offset": -800},
            {"type": "nonsense"},
        ], layer="Plan")
        self.assertEqual(res["created"], 3)
        self.assertEqual(len(res["errors"]), 1)
        summary = call("get_document_summary")
        self.assertAlmostEqual(summary["extent_mm"]["x"], 10000.0, places=1)
        call("undo")
        self.assertEqual(call("get_document_summary")["count"], 0)

    def test_drawing_scale(self):
        call("set_drawing_scale", denominator=50)
        self.assertEqual(call("get_drawing_scale")["denominator"], 50)

    def test_errors_are_reported(self):
        reply = registry.call("draw_line", {"p1": [0, 0]})
        self.assertIn("error", reply)
        reply = registry.call("no_such_command", {})
        self.assertIn("Unknown command", reply["error"]["message"])

    def test_run_python(self):
        res = call("run_python", code="print('hi')\nresult = 2 + 3")
        self.assertEqual(res["result"], 5)
        self.assertEqual(res["stdout"], "hi\n")

    def test_run_python_recreating_the_document(self):
        # A script that rebuilds its document closes the one the undo transaction was opened on.
        code = ("name = App.ActiveDocument.Name\n"
                "App.closeDocument(name)\n"
                "App.newDocument(name)\n"
                "result = 'rebuilt'")
        res = call("run_python", code=code)
        self.assertEqual(res["result"], "rebuilt")
        self.doc = App.getDocument("MCPTest")  # the new document, closed by tearDown

    def test_failing_run_python_after_closing_the_document(self):
        code = "App.closeDocument(App.ActiveDocument.Name)\nraise ValueError('boom')"
        reply = registry.call("run_python", {"code": code})
        self.assertIn("boom", reply["error"]["message"])
        self.doc = App.newDocument("MCPTest")


class TestBim(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("MCPTestBim")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _opening_inside(self, opening, wall, along, lo, hi):
        """The opening's solids lie within the wall thickness, between lo and hi along the wall."""
        from freecad_mcp.handlers.bim3d import solid_box

        win = solid_box(self.doc.getObject(opening["name"]).Shape)
        box = self.doc.getObject(wall["name"]).Shape.BoundBox
        if along == "x":
            self.assertGreaterEqual(win.YMin, box.YMin - 1)
            self.assertLessEqual(win.YMax, box.YMax + 1)
            self.assertAlmostEqual(win.XMin, lo, delta=1)
            self.assertAlmostEqual(win.XMax, hi, delta=1)
        else:
            self.assertGreaterEqual(win.XMin, box.XMin - 1)
            self.assertLessEqual(win.XMax, box.XMax + 1)
            self.assertAlmostEqual(win.YMin, lo, delta=1)
            self.assertAlmostEqual(win.YMax, hi, delta=1)

    def test_wall_door_window(self):
        level = call("make_level", name="Térreo")
        wall = call("make_wall", start=[0, 0], end=["4 m", 0], width=150, height=2800, level=level["name"])
        self.assertAlmostEqual(wall["volume_m3"], 4 * 2.8 * 0.15, places=3)
        door = call("add_opening", wall=wall["name"], kind="door", position=1000, width=800, height=2100)
        self.assertAlmostEqual(door["wall_volume_m3"], 1.68 - 0.8 * 2.1 * 0.15, places=3)
        self._opening_inside(door, wall, "x", 1000, 1800)
        window = call("add_opening", wall=wall["name"], kind="window", position=2500, width=1200,
                      height=1200, sill=1000)
        self.assertAlmostEqual(window["wall_volume_m3"], 1.428 - 1.2 * 1.2 * 0.15, places=3)
        self.assertAlmostEqual(self.doc.getObject(window["name"]).Shape.BoundBox.ZMin, 1000, delta=1)

    def test_aligned_and_vertical_walls(self):
        wall = call("make_wall", start=[0, 0], end=[0, 5000], width=200, height=3000, align="Left")
        door = call("add_opening", wall=wall["name"], position=2000, width=900)
        self.assertAlmostEqual(door["wall_volume_m3"], 5 * 3 * 0.2 - 0.9 * 2.1 * 0.2, places=3)
        self._opening_inside(door, wall, "y", 2000, 2900)

    def test_walls_along_outline_and_space(self):
        outline = [[0, 0], [10000, 0], [10000, 6000], [0, 6000]]
        walls = call("make_walls", points=outline, closed=True, width=150, height=2800)
        self.assertAlmostEqual(walls["baseline_length"], 32000, places=1)
        door = call("add_opening", wall=walls["name"], position=10000 + 2000, width=800)  # on the 2nd side
        self._opening_inside(door, walls, "y", 2000, 2800)
        space = call("make_space", name="SALA", boundary=[[75, 75], [5925, 75], [5925, 5925], [75, 5925]])
        self.assertAlmostEqual(space["area_m2"], 5.85 * 5.85, places=2)


class TestElectrical(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("MCPTestEletrica")
        call("set_drawing_scale", denominator=50)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _box(self, group):
        grp = self.doc.getObject(group)
        box = None
        for obj in grp.Group:
            shape = getattr(obj, "Shape", None)
            if shape is None or shape.isNull():
                continue
            if box is None:
                box = App.BoundBox(shape.BoundBox)
            else:
                box.add(shape.BoundBox)
        return box

    def test_every_symbol(self):
        from freecad_mcp.handlers.electrical import SYMBOLS

        for i, name in enumerate(SYMBOLS):
            res = call("insert_electrical_symbol", symbol=name, position=[i * 1000, 0], letra="a",
                       circuito=3, potencia="100VA")
            self.assertGreater(res["objects"], 1, name)
            self.assertEqual(res["scale"], "1:50")
        self.assertIn("Unknown symbol", registry.call("insert_electrical_symbol",
                                                      {"symbol": "nada", "position": [0, 0]})["error"]["message"])

    def test_size_follows_the_drawing_scale(self):
        res = call("insert_electrical_symbol", symbol="ponto_luz_teto", position=[2000, 1500])
        box = self._box(res["group"])
        self.assertAlmostEqual(box.XLength, 5.0 * 50, places=3)  # Ø5 mm on paper at 1:50
        self.assertAlmostEqual(box.Center.x, 2000, places=3)
        res = call("insert_electrical_symbol", symbol="ponto_luz_teto", position=[0, 0], scale=100)
        self.assertAlmostEqual(self._box(res["group"]).XLength, 500, places=3)

    def test_wall_symbol_points_into_the_room(self):
        # a socket on a wall at y = 0, the room on +Y
        res = call("insert_electrical_symbol", symbol="tomada_baixa", position=[1000, 0], rotation=90)
        box = self._box(res["group"])
        self.assertAlmostEqual(box.YMax, (2.0 + 2.4) * 50, places=3)  # stem + triangle
        self.assertAlmostEqual(box.YMin, -0.5 * 50, places=3)         # wall mark behind the wall face

    def test_conduit_with_conductors(self):
        res = call("draw_conduit", points=[[0, 0], [3000, 0], [3000, 2000]], installation="piso",
                   conductors=["fase", "neutro", "terra"], circuit=2, gauge=2.5, diameter=25)
        self.assertEqual(res["length_mm"], 5000.0)
        names = [o.TypeId for o in self.doc.getObject(res["group"]).Group]
        # wire + fase (1) + neutro (2) + terra (2) lines, and 3 texts
        self.assertEqual(len(names), 1 + 1 + 2 + 2 + 3)
        reply = registry.call("draw_conduit", {"points": [[0, 0], [1, 0]], "conductors": ["xyz"]})
        self.assertIn("Unknown conductor", reply["error"]["message"])


class TestSheet(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("MCPTestSheet")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_sheet_size_and_scale(self):
        call("draw_rectangle", origin=[0, 0], width=10000, height=6000, layer="Plan")
        call("set_drawing_scale", denominator=50)
        sheet = call("create_sheet")
        self.assertEqual(sheet["scale"], "1:50")
        self.assertEqual(sheet["sheet"], "A4")  # 200 x 120 mm fits above the A4 title block
        self.assertEqual(sheet["drawing_size_on_paper_mm"], [200.0, 120.0])
        self.assertNotIn("warning", sheet)
        sheet = call("create_sheet", scale=20)
        self.assertEqual(sheet["sheet"], "A2")  # 500 x 300 mm
        sheet = call("create_sheet", sheet="A4", scale=20, title="Planta")
        self.assertIn("warning", sheet)


class TestProtocol(unittest.TestCase):
    def test_handle_line(self):
        try:
            from freecad_mcp import server
        except ImportError as e:
            self.skipTest(f"Qt not available: {e}")
        reply = json.loads(server.handle_line(b'{"id": 7, "method": "ping"}'))
        self.assertEqual(reply["id"], 7)
        self.assertIn("draw_batch", reply["result"]["commands"])
        reply = json.loads(server.handle_line(b"not json"))
        self.assertIn("Bad request", reply["error"]["message"])


class TestPdf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, _MCP_SERVER_DIR)
        try:
            import pdf_tools  # noqa: F401
            import pdfplumber  # noqa: F401
        except ImportError as e:
            raise unittest.SkipTest(f"PDF libraries missing: {e}")
        import sample_plan

        cls.pdf = sample_plan.write(os.path.join(tempfile.mkdtemp(), "plan.pdf"))

    def test_info_and_scale(self):
        import pdf_tools

        page = pdf_tools.pdf_info(self.pdf)["pages"][0]
        self.assertEqual(page["kind"], "vector")
        self.assertEqual(page["scale_candidates"][0]["denominator"], 100)

    def test_extract_real_sizes(self):
        import pdf_tools

        data = pdf_tools.extract_mm(self.pdf)  # scale read from "ESCALA 1:100"
        self.assertEqual(data["scale"], 100)
        rects = [p for p in data["paths"] if p["kind"] == "rect"]
        widths = sorted(max(x for x, _ in p["points"]) - min(x for x, _ in p["points"]) for p in rects)
        self.assertAlmostEqual(widths[-1], 10000, delta=1)  # outside 10.00 m
        self.assertAlmostEqual(widths[0], 9700, delta=1)  # inside, walls of 150 mm
        arc = [p for p in data["paths"] if p["kind"] == "curve"][0]
        self.assertAlmostEqual(arc["points"][0][0] - arc["points"][-1][0], 800, delta=1)  # door 800 mm
        self.assertIn("SALA", [t["text"] for t in data["texts"]])

    def test_calibrate(self):
        import pdf_tools
        import sample_plan

        a, b = sample_plan.to_pt(0, 0), sample_plan.to_pt(10000, 0)
        self.assertAlmostEqual(pdf_tools.calibrate(a, b, 10000)["scale"], 100, delta=0.01)

    def test_render(self):
        import pdf_tools

        render = pdf_tools.render_page(self.pdf, dpi=72)
        self.assertTrue(os.path.isfile(render["path"]))
        q = pdf_tools.px_to_pt((0, 0), render)
        self.assertAlmostEqual(q[1], render["page_size_pt"][1], delta=0.01)


if __name__ == "__main__":
    unittest.main(exit=False)
