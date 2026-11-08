# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests for the Electrical module. Run with: FreeCADCmd -t TestElectrical"""

import math
import os
import tempfile
import unittest

import FreeCAD as App

from freecad_electrical import calc, checks, model, sheets


class TestCalc(unittest.TestCase):
    def test_currents_380_220(self):
        self.assertAlmostEqual(calc.current(2200, calc.MONO, "380/220 V"), 10.0)
        self.assertAlmostEqual(calc.current(5400, calc.BI, "380/220 V"), 5400 / 380)
        self.assertAlmostEqual(calc.current(10000, calc.TRI, "380/220 V"), 10000 / (math.sqrt(3) * 380))
        self.assertAlmostEqual(calc.current(1270, calc.MONO, "220/127 V"), 10.0)

    def test_protection_suggestions(self):
        # electric shower 5400 VA, 380 V two-phase: 14.2 A -> 16 A breaker, 2.5 mm²
        amps = calc.current(5400, calc.BI, "380/220 V")
        self.assertEqual(calc.suggest_breaker(amps, "TUE"), 16)
        self.assertEqual(calc.suggest_section(16, "TUE", calc.BI), 2.5)
        # lighting: at least 10 A and 1.5 mm²
        self.assertEqual(calc.suggest_breaker(2.0, "Iluminação"), 10)
        self.assertEqual(calc.suggest_section(10, "Iluminação", calc.MONO), 1.5)
        # 3-phase 40 A: 3 loaded conductors -> 10 mm² (50 A), not 6 mm² (36 A)
        self.assertEqual(calc.suggest_section(40, "Motor", calc.TRI), 10)

    def test_powers(self):
        self.assertEqual(calc.item_power(300, "VA", 1, 0.9), (300, 270))
        va, w = calc.item_power(100, "W", 2, 0.92)
        self.assertAlmostEqual(w, 200)
        self.assertAlmostEqual(va, 200 / 0.92)
        va, w = calc.item_power(1, "CV", 1, 0.8)
        self.assertAlmostEqual(w, 735.5 / 0.85)
        self.assertEqual(calc.power_text(100, "W", 2), "2x100W")
        self.assertEqual(calc.power_text(1.5, "CV"), "1,5CV")

    def _imbalance(self, circuits):
        plan = calc.balance(circuits)
        loads = {p: 0.0 for p in calc.PHASES}
        for key, va, _ in circuits:
            for p in plan[key]:
                loads[p] += va / len(plan[key])
        return calc.imbalance(loads)

    def test_balance(self):
        six = [(str(i), 1000, calc.MONO) for i in range(6)]
        self.assertLess(self._imbalance(six), 1.0)  # two per phase
        mixed = [("a", 4000, calc.MONO), ("b", 2000, calc.MONO), ("c", 3000, calc.MONO), ("d", 3000, calc.MONO),
                 ("e", 6000, calc.BI)]
        self.assertLess(self._imbalance(mixed), 1.0)  # 6000 per phase
        # three 3000 VA single-phase + a 6000 VA two-phase: the best possible is 6000/6000/3000 (40 %)
        odd = [("a", 3000, calc.MONO), ("b", 3000, calc.MONO), ("c", 3000, calc.MONO), ("d", 6000, calc.BI)]
        self.assertLessEqual(self._imbalance(odd), 40.0 + 1e-6)

    def test_nbr5410_minimums(self):
        self.assertEqual(calc.min_lighting_va(6), 100)
        self.assertEqual(calc.min_lighting_va(12), 160)
        self.assertEqual(calc.min_sockets("seco", 12, 14), 3)
        self.assertEqual(calc.min_sockets("cozinha", 9, 12), 4)
        self.assertEqual(calc.min_socket_va("cozinha", 4), [600, 600, 600, 100])


class TestModel(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("TestEletrica")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_points_make_circuits_and_totals(self):
        model.make_point(self.doc, "tomada_baixa", App.Vector(0, 0, 0), 90, circuit=1)
        model.make_point(self.doc, "tomada_baixa", App.Vector(1000, 0, 0), 90, circuit=1, power=600)
        model.make_point(self.doc, "ponto_luz_teto", App.Vector(2000, 2000, 0), circuit=2, power=100, quantity=2)
        self.doc.recompute()
        c1, c2 = model.find_circuit(self.doc, 1), model.find_circuit(self.doc, 2)
        self.assertEqual(c1.Kind, "TUG")
        self.assertEqual(c2.Kind, "Iluminação")
        self.assertEqual(c1.Points, 2)
        self.assertAlmostEqual(c1.PowerVA, 700)
        self.assertAlmostEqual(c1.Current, 700 / 220, places=2)
        self.assertNotEqual(c1.Phases, c2.Phases)  # new circuits go to different phases
        self.assertEqual(model.installation(self.doc).System, "380/220 V")

    def test_symbol_drawn_from_data_at_scale(self):
        p = model.make_point(self.doc, "ponto_luz_teto", App.Vector(0, 0, 0), circuit=3, power=100)
        self.doc.recompute()
        self.assertFalse(p.Shape.isNull())
        circle = [e for e in p.Shape.Edges if hasattr(e.Curve, "Radius") and abs(e.Curve.Radius - 125) < 1e-6]
        self.assertTrue(circle)  # Ø 5 mm on paper at 1:50
        self.assertTrue(p.Shape.Faces)  # the texts are faces
        before = len(p.Shape.Faces)
        p.Quantity = 2  # "2x100W" has more characters than "100W"
        self.doc.recompute()
        self.assertGreater(len(p.Shape.Faces), before)

    def test_editing_and_deleting_update_the_circuit(self):
        a = model.make_point(self.doc, "tomada_baixa", App.Vector(0, 0, 0), circuit=1)
        model.make_point(self.doc, "tomada_baixa", App.Vector(500, 0, 0), circuit=1)
        self.doc.recompute()
        c = model.find_circuit(self.doc, 1)
        self.assertAlmostEqual(c.PowerVA, 200)
        model.set_point(a, power=300)
        self.doc.recompute()
        self.assertAlmostEqual(c.PowerVA, 400)
        self.doc.removeObject(a.Name)
        self.doc.recompute()
        self.assertAlmostEqual(c.PowerVA, 100)
        self.assertEqual(c.Points, 1)

    def test_moving_moves_the_data(self):
        import Draft

        p = model.make_point(self.doc, "tomada_baixa", App.Vector(0, 0, 0), 0)
        self.doc.recompute()
        Draft.move(p, App.Vector(1000, 500, 0))
        self.doc.recompute()
        self.assertEqual(p.Position, App.Vector(1000, 500, 0))
        self.assertTrue(p.Placement.isIdentity())
        self.assertAlmostEqual(p.Shape.BoundBox.XMin, 1000 - 0.5 * 50, places=3)

    def test_three_phase_and_schedule(self):
        model.make_circuit(self.doc, 1, "Motor bomba", "Motor", calc.TRI)
        model.make_point(self.doc, "motor", App.Vector(0, 0, 0), circuit=1, power=5, unit="CV", power_factor=0.85)
        model.make_point(self.doc, "tomada_alta", App.Vector(0, 3000, 0), -90, circuit=2, power=5400, unit="VA")
        model.set_circuit(model.find_circuit(self.doc, 2), kind="TUE", connection=calc.BI, phases="")
        self.doc.recompute()
        c1 = model.find_circuit(self.doc, 1)
        self.assertEqual(c1.Phases, "RST")
        self.assertAlmostEqual(c1.LoadR, c1.PowerVA / 3, places=1)
        sched = model.schedule(self.doc)
        self.doc.recompute()
        sheet = sched.Sheet
        self.assertEqual(sheet.get("A5"), 1)
        self.assertIn("Motor", sheet.get("B5"))
        self.assertAlmostEqual(float(sheet.get("I5")), round(c1.Current, 1), places=1)
        self.assertTrue(sched.LastCell.startswith("N"))
        c2 = model.find_circuit(self.doc, 2)
        self.assertTrue(any("fase" in w for w in c2.Warnings))  # two-phase circuit without phases
        changes = model.rebalance_phases(self.doc)
        self.assertEqual(len(c2.Phases), 2)
        self.assertIn(2, changes)

    def test_schedule_follows_later_changes(self):
        p = model.make_point(self.doc, "tomada_baixa", App.Vector(0, 0, 0), circuit=1)
        sheet = model.schedule(self.doc).Sheet
        self.doc.recompute()
        self.assertEqual(sheet.get("G5"), 100)
        model.set_point(p, power=450)  # a change after the schedule exists
        self.doc.recompute()
        self.assertEqual(sheet.get("G5"), 450)
        self.assertIn(model.find_circuit(self.doc, 1), model.schedule(self.doc).OutList)

    def test_conduit_marks_follow_the_circuits(self):
        model.make_circuit(self.doc, 1, "Tomadas", "TUG", calc.MONO)
        model.make_circuit(self.doc, 2, "Motor", "Motor", calc.TRI)
        self.assertEqual(model.conductors_of(model.find_circuit(self.doc, 1)), ["fase", "neutro", "terra"])
        self.assertEqual(model.conductors_of(model.find_circuit(self.doc, 2)), ["fase"] * 3 + ["terra"])
        c = model.make_conduit(self.doc, [App.Vector(0, 0, 0), App.Vector(3000, 0, 0)], "piso", [1, 2],
                               ["retorno"], "25")
        self.doc.recompute()
        self.assertFalse(c.Shape.isNull())
        dashes = [e for e in c.Shape.Edges if abs(e.BoundBox.YLength) < 1e-6 and abs(e.BoundBox.ZLength) < 1e-6
                  and e.Length <= 100.0 + 1e-6 and abs(e.BoundBox.YMin) < 1e-6]
        self.assertGreater(len(dashes), 10)  # floor conduits are dashed

    def test_saved_with_the_document(self):
        model.make_point(self.doc, "tomada_media", App.Vector(0, 0, 0), circuit=4, power=300)
        self.doc.recompute()
        path = os.path.join(tempfile.mkdtemp(), "eletrica.FCStd")
        self.doc.saveAs(path)
        App.closeDocument(self.doc.Name)
        self.doc = App.openDocument(path)
        self.doc.recompute()
        c = model.find_circuit(self.doc, 4)
        self.assertAlmostEqual(c.PowerVA, 300)
        self.assertEqual(model.points(self.doc)[0].Circuit, c)

    def test_room_checks(self):
        import Arch
        import Draft

        outline = Draft.make_wire([App.Vector(0, 0, 0), App.Vector(4000, 0, 0), App.Vector(4000, 3000, 0),
                                   App.Vector(0, 3000, 0)], closed=True, face=True)
        volume = self.doc.addObject("Part::Extrusion", "Volume")
        volume.Base = outline
        volume.Dir = App.Vector(0, 0, 2800)
        volume.Solid = True
        self.doc.recompute()
        space = Arch.makeSpace(volume)
        space.Label = "Quarto"
        model.make_point(self.doc, "ponto_luz_teto", App.Vector(2000, 1500, 0), circuit=1, power=100)
        model.make_point(self.doc, "tomada_baixa", App.Vector(0, 1000, 0), 0, circuit=2)
        report = checks.check(self.doc)
        room = report["rooms"][0]
        self.assertEqual(room["room"], "Quarto")
        self.assertEqual(room["min_lighting_va"], 160)  # 12 m²
        self.assertEqual(room["min_sockets"], 3)       # 14 m of perimeter
        self.assertEqual(room["sockets"], 1)
        self.assertFalse(room["ok"])


class TestSheets(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("TestFolhas")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_sheet_follows_new_items(self):
        import Draft

        Draft.make_rectangle(4000, 3000)
        model.make_point(self.doc, "tomada_baixa", App.Vector(0, 1000, 0), 0, circuit=1)
        self.doc.recompute()
        page, view, size, scale, box = sheets.make_sheet(self.doc)
        content = view.Source
        names = [o.Name for o in content.ElementList]
        self.assertIn(model.CONTAINER, names)
        self.assertIn("Rectangle", names)
        line = Draft.make_line(App.Vector(0, 0, 0), App.Vector(0, 5000, 0))
        self.assertTrue(sheets.sync(self.doc))  # the new loose line joins the sheet
        self.assertIn(line.Name, [o.Name for o in content.ElementList])
        self.assertFalse(sheets.sync(self.doc))
        self.assertEqual(scale, 50)

    def test_schedule_page_follows_the_table(self):
        model.make_point(self.doc, "tomada_baixa", App.Vector(0, 0, 0), 0, circuit=1)
        page, table = sheets.make_schedule_page(self.doc)
        first = table.CellEnd
        for n in range(2, 6):
            model.make_point(self.doc, "tomada_baixa", App.Vector(n * 500, 0, 0), 0, circuit=n)
        self.doc.recompute()
        sheets.follow_schedule(self.doc)
        self.assertNotEqual(table.CellEnd, first)
