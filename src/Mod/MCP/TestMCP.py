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

        obj = self.doc.getObject(opening["name"])
        if obj.IfcType == "Door":
            self.assertEqual(obj.Opening, 100)  # open at 90°, as plans draw doors
            obj.Opening = 0  # closed, its leaf lies in the wall
            self.doc.recompute()
        win = solid_box(obj.Shape)
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

        # Plans cut the building at 1,5 m: the wall breaks at the door and at the window, the
        # door swing is drawn dashed, and sheets draw the cut instead of the wall seen from above.
        from freecad_electrical import sheets
        from freecad_mcp.handlers import view

        self.assertTrue(sheets.plan_cut(self.doc, force=True))
        cut = {o.PlantaCorte: o for o in self.doc.Objects if sheets.PLAN_CUT in o.PropertiesList}
        self.assertEqual(sorted(cut), ["aberturas", "giros", "paredes"])
        self.assertEqual(len(cut["paredes"].Shape.Faces), 3)  # before the door, between, after the window
        self.assertAlmostEqual(cut["paredes"].Shape.BoundBox.ZMin, 1, delta=0.01)  # on the floor
        self.assertTrue(any(e.Curve.__class__.__name__ in ("Circle", "ArcOfCircle")
                            for e in cut["giros"].Shape.Edges))
        self.assertFalse(sheets.plan_cut(self.doc))  # nothing changed: not rebuilt
        content = sheets.make_content(self.doc)
        drawn = {o.Name for o in content.Proxy.svg_group(content)}
        self.assertNotIn(wall["name"], drawn)
        self.assertNotIn(door["name"], drawn)
        self.assertTrue({o.Name for o in cut.values()} <= drawn)
        # the 3D view: top view of a building cut at 1,5 m above its lowest wall, unless asked
        self.assertEqual(view._cut_height(self.doc, None, "top"), 1500)
        self.assertIsNone(view._cut_height(self.doc, None, "iso"))
        self.assertIsNone(view._cut_height(self.doc, False, "top"))
        self.assertEqual(view._cut_height(self.doc, 2400, "iso"), 2400)

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

    def test_walls_from_a_rectangle_are_walls_not_a_block(self):
        rect = call("draw_rectangle", origin=[0, 0], width=4000, height=3000)
        wall = call("make_walls_from_objects", names=[rect["name"]], width=150, height=2800)["walls"][0]
        doc = App.ActiveDocument
        volume = doc.getObject(wall["name"]).Shape.Volume
        self.assertAlmostEqual(volume, 14000 * 150 * 2800, delta=0.02 * 14000 * 150 * 2800)


class TestElectrical(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("MCPTestEletrica")
        call("set_drawing_scale", denominator=50)

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_every_symbol(self):
        from freecad_electrical.symbols import SYMBOLS

        for i, name in enumerate(SYMBOLS):
            res = call("insert_electrical_symbol", symbol=name, position=[i * 1000, 0], letra="a",
                       circuito=3 if SYMBOLS[name].kind else None)
            self.assertEqual(res["symbol"], name)
            self.assertEqual(res["scale"], "1:50")
            self.assertFalse(self.doc.getObject(res["name"]).Shape.isNull(), name)
        self.assertIn("Unknown symbol", registry.call("insert_electrical_symbol",
                                                      {"symbol": "nada", "position": [0, 0]})["error"]["message"])

    def test_size_follows_the_drawing_scale(self):
        res = call("insert_electrical_symbol", symbol="ponto_luz_teto", position=[2000, 1500])
        circle = [e for e in self.doc.getObject(res["name"]).Shape.Edges
                  if type(e.Curve).__name__ == "Circle"][0]
        self.assertAlmostEqual(circle.Curve.Radius * 2, 5.0 * 50, places=3)  # Ø5 mm on paper at 1:50
        self.assertAlmostEqual(circle.Curve.Center.x, 2000, places=3)
        call("set_drawing_scale", denominator=100)  # the symbols follow the drawing scale
        self.doc.recompute()
        circle = [e for e in self.doc.getObject(res["name"]).Shape.Edges
                  if type(e.Curve).__name__ == "Circle"][0]
        self.assertAlmostEqual(circle.Curve.Radius * 2, 500, places=3)

    def test_wall_symbol_points_into_the_room(self):
        # a socket on a wall at y = 0, the room on +Y
        res = call("insert_electrical_symbol", symbol="tomada_baixa", position=[1000, 0], rotation=90,
                   potencia=0)  # no power and no circuit: no texts, only the symbol
        box = self.doc.getObject(res["name"]).Shape.BoundBox
        self.assertAlmostEqual(box.YMax, (2.0 + 2.4) * 50, places=3)  # stem + triangle
        self.assertAlmostEqual(box.YMin, -0.5 * 50, places=3)         # wall mark behind the wall face

    def test_power_text_and_circuits(self):
        luz = call("insert_electrical_symbol", symbol="ponto_luz_teto", position=[0, 0], circuito=1,
                   potencia="2x100W")
        self.assertEqual((luz["quantity"], luz["power"], luz["unit"]), (2, 100, "W"))
        call("create_circuit", number=5, description="Chuveiro", kind="TUE", connection="Bifásico (F+F)")
        ch = call("insert_electrical_symbol", symbol="tomada_alta", position=[0, 3000], rotation=-90,
                  circuito=5, potencia="5400VA")
        summary = call("load_summary")
        c5 = [c for c in summary["circuits"] if c["number"] == 5][0]
        self.assertAlmostEqual(c5["current_a"], round(5400 / 380, 2), places=2)  # 380 V two-phase
        self.assertEqual(len(c5["phases"]), 2)
        self.assertEqual(summary["system"], "380/220 V")
        call("set_electrical_item", name=ch["name"], potencia="6800VA")
        c5 = [c for c in call("load_summary")["circuits"] if c["number"] == 5][0]
        self.assertAlmostEqual(c5["power_va"], 6800)
        self.assertEqual(c5["breaker_a"], 20)
        self.assertIn("changes", call("rebalance_phases"))

    def test_conduit_with_circuits(self):
        call("create_circuit", number=2, kind="TUG")
        res = call("draw_conduit", points=[[0, 0], [3000, 0], [3000, 2000]], installation="piso",
                   circuits=[2], extra=["retorno"], diameter=25)
        self.assertEqual(res["length_mm"], 5000.0)
        self.assertEqual(res["circuits"], [2])
        self.assertEqual(res["extra"], ["retorno"])
        old = call("draw_conduit", points=[[0, 0], [1000, 0]], conductors=["fase", "retorno"], circuit=2)
        self.assertEqual(old["extra"], ["retorno"])  # older calls still work
        reply = registry.call("draw_conduit", {"points": [[0, 0], [1, 0]], "extra": ["xyz"]})
        self.assertIn("Unknown conductor", reply["error"]["message"])

    def test_automatic_conduits_floors_demand_and_materials(self):
        for number, panel, nivel in ((1, "QD1", 0), (11, "QD2", "3 m")):
            call("insert_electrical_symbol", symbol="quadro_terminal", position=[0, 0], label=panel, nivel=nivel)
            call("create_circuit", number=number, kind="Iluminação", panel=panel)
            call("insert_electrical_symbol", symbol="ponto_luz_teto", position=[3000, 0], circuito=number,
                 letra="a", nivel=nivel)
            call("insert_electrical_symbol", symbol="interruptor_simples", position=[3000, 2000], circuito=number,
                 letra="a", nivel=nivel)
            call("draw_conduit", points=[[0, 0], [3000, 0]], nivel=nivel)
            res = call("draw_conduit", points=[[3000, 0], [3000, 2000]], nivel=nivel)
            self.assertTrue(res["automatic"])
            self.assertEqual(res["conductors"], [{"circuit": number, "marks": ["fase", "retorno"]}])
        self.assertEqual(len(call("list_electrical_items")["floors"]), 2)
        call("create_circuit", number=9, description="Chuveiro", kind="TUE", connection="Bifásico (F+F)")
        call("insert_electrical_symbol", symbol="tomada_alta", position=[0, 3000], rotation=-90, circuito=9,
             potencia="5400W")
        entrance = call("demand_summary")["entrances"][0]
        self.assertEqual(entrance["category"], "T5")  # a 380 V shower needs a three-phase entrance
        items = call("bill_of_materials")["items"]
        self.assertTrue(any("retorno" in i["item"] for i in items))
        pages = call("create_electrical_sheets")["pages"]
        self.assertEqual([p["kind"] for p in pages].count("planta"), 2)  # one plan page per floor


class TestReviewComments(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("MCPTestRevisao")
        call("set_drawing_scale", denominator=50)
        self.qd = call("insert_electrical_symbol", symbol="quadro_terminal", position=[200, 1500], rotation=90)
        self.luz = call("insert_electrical_symbol", symbol="ponto_luz_teto", position=[900, 1500])
        self.longe = call("insert_electrical_symbol", symbol="tomada_baixa", position=[6000, 0])

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_target_and_surroundings(self):
        from freecad_mcp import review

        c = call("add_review_comment", position=[200, 1500], text="Mover o QDC para perto da porta",
                 target=self.qd["name"], radius=1000)
        self.assertEqual(c["number"], 1)
        self.assertEqual(c["target"]["name"], self.qd["name"])
        near = [n["name"] for n in c["surroundings_now"]]
        self.assertEqual(near[0], self.qd["name"])                 # nearest first
        self.assertIn(self.luz["name"], near)                      # the light 700 mm away
        self.assertNotIn(self.longe["name"], near)                 # the socket 6 m away
        self.assertTrue(any("Ponto de luz" in s for s in c["surroundings_when_created"]))
        self.assertTrue(review.is_comment(self.doc.getObject(c["name"])))

    def test_status_cycle_and_history(self):
        call("add_review_comment", position=[200, 1500], text="Primeiro")
        call("add_review_comment", position=[900, 1500], text="Segundo")
        self.assertEqual(call("list_review_comments")["counts"], {"Aberto": 2, "Corrigido": 0, "Reaberto": 0})
        fixed = call("resolve_review_comment", number=1, note="QDC movido")
        self.assertEqual(fixed["status"], "Corrigido")
        self.assertIn("Claude: corrigido - QDC movido", fixed["history"][-1])
        self.assertEqual([c["number"] for c in call("list_review_comments")["comments"]], [2])
        reply = registry.call("reopen_review_comment", {"number": 2})
        self.assertIn("not Corrigido", reply["error"]["message"])
        reopened = call("reopen_review_comment", number=1, reason="ainda longe da porta")
        self.assertEqual(reopened["status"], "Reaberto")
        self.assertEqual(len(reopened["history"]), 3)
        pending = call("list_review_comments")["comments"]
        self.assertEqual(sorted(c["number"] for c in pending), [1, 2])
        call("undo")  # the reopening is one undo step
        self.assertEqual(call("get_review_comment", number=1)["status"], "Corrigido")

    def test_saved_with_the_document_and_kept_off_sheets(self):
        call("add_review_comment", position=[200, 1500], text="Conferir", target=self.qd["name"])
        sheet = call("create_sheet")
        view = self.doc.getObject(sheet["view"])
        self.assertFalse(any(o.Name == "Revisao" or "Comentario" in o.Name for o in view.Source.ElementList))
        path = os.path.join(tempfile.mkdtemp(), "revisao.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        App.setActiveDocument(self.doc.Name)
        c = call("get_review_comment", number=1)
        self.assertEqual(c["text"], "Conferir")
        self.assertEqual(c["target"]["name"], self.qd["name"])
        self.assertEqual(len(c["history"]), 1)


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

    def test_requests_wait_for_the_running_command(self):
        try:
            from freecad_mcp import server
        except ImportError as e:
            self.skipTest(f"Qt not available: {e}")

        class Sock:
            def __init__(self):
                self.sent = []

            def write(self, data):
                self.sent.append(json.loads(data))

            def flush(self):
                pass

        srv = server.CommandServer(0)
        try:
            sock = Sock()
            srv._buffers[sock] = b'{"id": 1, "method": "ping"}\n{"id": 2, "method": "ping"}\n{"id": 3'
            srv._busy = True  # a command is running and repainting: nothing runs inside it
            srv._drain()
            self.assertEqual(sock.sent, [])
            srv._busy = False  # done: the queue runs in order, the partial line waits
            srv._drain()
            self.assertEqual([r["id"] for r in sock.sent], [1, 2])
            self.assertEqual(srv._buffers[sock], b'{"id": 3')
        finally:
            srv.close()


class TestLive(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("MCPTestLive")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_heavy_outputs_wait_for_the_pause(self):
        from freecad_mcp import live

        import Draft

        line = Draft.make_line(App.Vector(0, 0, 0), App.Vector(1000, 0, 0))
        table = self.doc.addObject("Spreadsheet::Sheet", "Tabela")
        self.doc.recompute()
        self.assertTrue(live.is_heavy(table))
        self.assertFalse(live.is_heavy(line))
        line.End = App.Vector(2000, 0, 0)
        table.touch()
        enabled = live.enabled
        live.enabled = lambda: True  # as while drawing in the GUI
        try:
            live.recompute(self.doc)
        finally:
            live.enabled = enabled
        self.assertAlmostEqual(line.Shape.Length, 2000.0)
        self.assertIn("Touched", table.State)  # left for the pause
        self.assertIn(self.doc.Name, live._state["docs"])
        live._state["docs"].discard(self.doc.Name)
        live.recompute(self.doc)  # without the GUI: everything at once
        self.assertNotIn("Touched", table.State)


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
