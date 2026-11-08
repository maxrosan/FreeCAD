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
        self.assertFalse(p.Shape.Faces)  # the texts are kept apart, as faces
        before = len(p.Labels.Faces)
        self.assertTrue(before)
        p.Quantity = 2  # "2x100W" has more characters than "100W"
        self.doc.recompute()
        self.assertGreater(len(p.Labels.Faces), before)
        import Draft

        svg = Draft.get_svg(p, techdraw=True)
        self.assertIn('stroke="none"', svg)  # texts filled without an outline on sheets

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
        self.assertTrue(sched.LastCell.startswith("Q"))
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
        # the content only points at what it draws: a (link) group would hide it in the 3D view
        self.assertNotEqual(content.TypeId, "App::LinkGroup")
        self.assertIsNone(self.doc.getObject("Rectangle").getParentGroup())
        self.assertIn("Rectangle", view.Symbol)
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


class TestSizing(unittest.TestCase):
    def test_voltage_drop_and_conductor(self):
        # 2400 VA at 220 V over 60 m: 2.5 mm² drops 5.4 %, 4 mm² 3.3 %
        amps = calc.current(2400, calc.MONO, "380/220 V")
        self.assertAlmostEqual(calc.voltage_drop(amps, 60, 2.5, calc.MONO, "380/220 V"),
                               2 * 0.0225 * 60 * amps / 2.5 / 220 * 100)
        self.assertEqual(calc.size_conductor(16, "TUG", calc.MONO, amps, 60), (4, "queda de tensão"))
        self.assertEqual(calc.size_conductor(16, "TUG", calc.MONO, amps, 10), (2.5, "mínimo"))
        # three circuits in one conduit: 24 A x 0.70 = 16.8 A still carries 16 A; four do not (15.6 A)
        self.assertEqual(calc.size_conductor(16, "TUG", calc.MONO, factor=calc.grouping_factor(3))[0], 2.5)
        self.assertEqual(calc.size_conductor(16, "TUG", calc.MONO, factor=calc.grouping_factor(4))[0], 4)
        self.assertEqual(calc.temperature_factor(40), 0.87)
        # a lightly loaded circuit does not count in the grouping (NBR 5410 6.2.5.5.6)
        self.assertTrue(calc.negligible_in_group(2.0, 1.5, calc.MONO, calc.grouping_factor(4)))
        self.assertFalse(calc.negligible_in_group(8.0, 2.5, calc.MONO, calc.grouping_factor(4)))
        self.assertEqual(calc.rcd_reason("cozinha", None, 1300, False), "")  # a switch is not a point of use

    def test_rcd_rule(self):
        self.assertTrue(calc.rcd_reason("banheiro", "TUG", 300, False))
        self.assertTrue(calc.rcd_reason("externa", "TUG", 300, False))
        self.assertEqual(calc.rcd_reason("banheiro", "Iluminação", 0, True), "")  # ceiling light exempt
        self.assertEqual(calc.rcd_reason("seco", "TUG", 300, False), "")
        self.assertEqual(calc.room_category("Garagem"), "externa")

    def test_conduit_size(self):
        self.assertEqual(calc.suggest_conduit([2.5] * 3), 20)
        self.assertEqual(calc.suggest_conduit([2.5] * 9), 25)
        self.assertEqual(calc.suggest_conduit([10] * 4), 25)

    def test_demand_and_category(self):
        # DIS-NOR-030, example 2: 2.8 kW of sockets + 1 kW of lights, 2 showers, 1 tap, 1 dryer
        items = [(calc.G_LIGHT, 1, 3.8, 3.8), (calc.G_HEATER, 2, 8.0, 8.0), (calc.G_HEATER, 1, 3.0, 3.0),
                 (calc.G_APPLIANCE, 1, 2.5, 2.5)]
        result = calc.demand(items)
        expected = 3.8 * 0.59 + 11.0 * 0.84 + 2.5 * 1.0
        self.assertAlmostEqual(result["kva"], round(expected, 2))
        cat = calc.service_category("380/220 V", 17.3, result["kva"], 1)
        self.assertEqual(cat["category"], "T5")  # above the 15 kW of M3: three-phase, demand <= 32 kVA
        self.assertEqual(cat["breaker_a"], 50)
        self.assertEqual(calc.service_category("380/220 V", 7.8, 5, 1)["category"], "M2")
        self.assertEqual(calc.service_category("380/220 V", 7.8, 5, 2)["category"], "T5")  # a 380 V load
        self.assertEqual(calc.service_category("220/127 V", 7.8, 5, 1)["category"], "M1")
        self.assertEqual(calc.service_category("380/220 V", 30, 45, 3)["category"], "T7")
        self.assertIsNone(calc.service_category("380/220 V", 90, 80, 3)["category"])
        # motors: the biggest at 100 %, the others at 50 %
        self.assertAlmostEqual(calc.demand([(calc.G_MOTOR, 2, 1.7, 2.0)])["kva"], 1.5)
        self.assertEqual(calc.demand_group("TUE", "Chuveiro elétrico"), calc.G_HEATER)
        self.assertEqual(calc.demand_group("TUE", "Tomada", "Ar-condicionado quarto"), calc.G_AIR)


def _space(doc, label, x0, y0, x1, y1, z=0.0, height=2800):
    import Arch
    import Draft

    outline = Draft.make_wire([App.Vector(x0, y0, z), App.Vector(x1, y0, z), App.Vector(x1, y1, z),
                               App.Vector(x0, y1, z)], closed=True, face=True)
    volume = doc.addObject("Part::Extrusion", "Volume")
    volume.Base = outline
    volume.Dir = App.Vector(0, 0, height)
    volume.Solid = True
    doc.recompute()
    space = Arch.makeSpace(volume)
    space.Label = label
    doc.recompute()
    return space


def _house(doc, z=0.0, first=1, panel="QD1"):
    """A panel, a light with its switch and a socket, joined by automatic conduits."""
    V = App.Vector
    qd = model.make_point(doc, "quadro_terminal", V(0, 0, z), label=panel)
    model.make_circuit(doc, first, "Iluminação", "Iluminação", panel=panel)
    model.make_circuit(doc, first + 1, "Tomadas", "TUG", panel=panel)
    light = model.make_point(doc, "ponto_luz_teto", V(3000, 0, z), circuit=first, letter="a")
    switch = model.make_point(doc, "interruptor_simples", V(3000, 2000, z), circuit=first, letter="a")
    socket = model.make_point(doc, "tomada_baixa", V(0, 3000, z), 0, circuit=first + 1)
    a = model.make_conduit(doc, [V(0, 0, z), V(3000, 0, z)])
    b = model.make_conduit(doc, [V(3000, 0, z), V(3000, 2000, z)])
    c = model.make_conduit(doc, [V(0, 0, z), V(0, 3000, z)])
    return qd, light, switch, socket, a, b, c


class TestNetwork(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("TestRede")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_conductors_from_the_items(self):
        from freecad_electrical import network

        qd, light, switch, socket, a, b, c = _house(self.doc)
        self.doc.recompute()
        self.assertTrue(a.Automatic)
        net = network.network(self.doc)

        def marks(conduit):
            return [(circ.Number, m) for circ, m in net.conductors(net.edge_at(conduit, 0.5))]

        self.assertEqual(marks(a), [(1, ["fase", "neutro", "terra"])])
        self.assertEqual(marks(b), [(1, ["fase", "retorno"])])  # to the switch: phase and return
        self.assertEqual(marks(c), [(2, ["fase", "neutro", "terra"])])
        c1, c2 = model.find_circuit(self.doc, 1), model.find_circuit(self.doc, 2)
        self.assertAlmostEqual(c1.LengthUsed, 3.0 + 1.3)  # run + panel drop (2.8 - 1.5 m)
        self.assertAlmostEqual(c2.LengthUsed, 3.0 + 2.5 + 1.3)  # run + drop to the socket at 0.3 m
        self.assertEqual(c1.Grouping, 1)
        self.assertFalse(b.Shape.isNull())

    def test_long_circuit_gets_a_bigger_conductor(self):
        qd, light, switch, socket, a, b, c = _house(self.doc)
        model.set_point(socket, power=2400)
        c2 = model.find_circuit(self.doc, 2)
        c2.Length = 60
        self.doc.recompute()
        self.assertEqual(c2.SectionSuggested, 4)
        self.assertEqual(c2.SectionReason, "queda de tensão")
        self.assertLess(c2.VoltageDrop, 4.0)
        c2.Section = 2.5
        self.doc.recompute()
        self.assertTrue(any("Queda de tensão" in w for w in c2.Warnings))

    def test_moving_an_item_updates_lengths(self):
        qd, light, switch, socket, a, b, c = _house(self.doc)
        self.doc.recompute()
        c2 = model.find_circuit(self.doc, 2)
        before = c2.LengthUsed
        c.Points = [App.Vector(0, 0, 0), App.Vector(0, 6000, 0)]
        model.set_point(socket, position=App.Vector(0, 6000, 0))
        self.doc.recompute()
        self.assertAlmostEqual(c2.LengthUsed, before + 3.0)

    def test_bathroom_needs_rcd(self):
        _space(self.doc, "Banheiro", -1000, 2000, 2000, 4000)
        _house(self.doc)
        self.doc.recompute()
        c2 = model.find_circuit(self.doc, 2)
        self.assertTrue(c2.HasRCD)
        self.assertIn("Banheiro", c2.RCDReason)
        self.assertFalse(model.find_circuit(self.doc, 1).HasRCD)
        c2.RCD = "Não"
        self.doc.recompute()
        self.assertTrue(any("DR" in w for w in c2.Warnings))

    def test_two_floors_and_feeder(self):
        from freecad_electrical import network

        V = App.Vector
        _house(self.doc, 0.0, 1, "QD1")
        _house(self.doc, 3000.0, 11, "QD2")
        model.make_circuit(self.doc, 20, "Alimentador QD2", "Alimentador", calc.TRI_N, panel="QD1", feeds="QD2")
        # riser from QD1 to QD2: "circuito sobe" over QD1, "circuito desce" under QD2
        model.make_point(self.doc, "circuito_sobe", V(0, 0, 0))
        model.make_point(self.doc, "circuito_desce", V(0, 0, 3000))
        _space(self.doc, "Quarto 1", -500, -500, 4000, 4000, 0.0)
        _space(self.doc, "Quarto 2", -500, -500, 4000, 4000, 3000.0)
        self.doc.recompute()
        self.assertEqual(len(model.floors(self.doc)), 2)
        net = network.network(self.doc)
        self.assertTrue(any(ed["installation"] == "prumada" for ed in net.edges))  # the riser joins the floors
        feeder = model.find_circuit(self.doc, 20)
        qd2_va = sum(c.PowerVA for c in model.panel_circuits(self.doc, "QD2"))
        self.assertAlmostEqual(feeder.PowerVA, qd2_va, places=0)
        self.assertGreater(feeder.DemandVA, 0)
        self.assertLessEqual(feeder.DemandVA, qd2_va + 1e-6)
        # QD1 -> riser (3 m) -> QD2, plus the drops at both panels
        self.assertAlmostEqual(feeder.LengthUsed, 3.0 + 1.3 + 1.3)
        riser = [e for e, ed in enumerate(net.edges) if ed["installation"] == "prumada"][0]
        self.assertIn(20, [c.Number for c, _ in net.conductors(riser)])
        self.assertEqual(model.top_panels(self.doc), ["QD1"])
        summary = model.load_summary(self.doc)
        self.assertAlmostEqual(summary["total_va"],
                               round(sum(c.PowerVA for c in model.circuits(self.doc) if not c.Feeds), 1))
        report = checks.check(self.doc)
        rooms = {r["room"]: r for r in report["rooms"]}
        self.assertEqual(rooms["Quarto 1"]["sockets"], 1)  # each floor counts its own items
        self.assertEqual(rooms["Quarto 2"]["sockets"], 1)
        ranges = sheets.floor_ranges(self.doc)
        self.assertEqual(len(ranges), 2)
        upper = sheets.floor_sources(self.doc, *ranges[1][1:])
        self.assertTrue(upper)
        self.assertTrue(all(o.Shape.BoundBox.ZMin >= 2000 for o in upper if hasattr(o, "Shape")))

    def test_indoor_floor_conduit_warned(self):
        V = App.Vector
        _space(self.doc, "Sala", 0, 0, 4000, 4000, 0.0)
        inside = model.make_conduit(self.doc, [V(500, 500, 0), V(3000, 500, 0)], "piso")
        model.make_conduit(self.doc, [V(5000, 500, 0), V(8000, 500, 0)], "piso")  # outdoors: fine
        model.make_conduit(self.doc, [V(500, 1000, 0), V(3000, 1000, 0)], "teto")
        self.doc.recompute()
        found = checks.indoor_floor_conduits(self.doc)
        self.assertEqual([(c.Name, room) for c, room in found], [(inside.Name, "Sala")])
        report = checks.check(self.doc)
        self.assertEqual(sum("no piso dentro de Sala" in w for w in report["warnings"]), 1)


class TestOutputs(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("TestSaidas")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_materials_and_diagram(self):
        from freecad_electrical import outputs

        _house(self.doc)
        self.doc.recompute()
        rows, notes = outputs.bill_of_materials(self.doc, waste=0.0)
        items = {item: qty for _, item, _, qty in rows}
        # circuit 1 phase: 3 m of conduit + 2 m to the switch, drops to the switch (1.5 m) and the panel (1.3 m)
        self.assertEqual(items["Cabo flexível de cobre 750 V 1,5 mm² - fase"], math.ceil(3 + 2 + 1.5 + 1.3))
        self.assertIn("Cabo flexível de cobre 750 V 1,5 mm² - retorno", items)
        self.assertEqual(items["Tomada 2P+T 10 A (NBR 14136)"], 1)
        self.assertTrue(any(i.startswith("Disjuntor geral") for i in items))
        self.assertTrue(any(i.startswith("Quadro de distribuição QD1") for i in items))
        obj = outputs.materials(self.doc)
        self.doc.recompute()
        self.assertTrue(obj.LastCell.startswith("D"))
        svg, width, height = outputs.unifilar_svg(self.doc)
        self.assertIn("QUADRO QD1", svg)
        self.assertIn("Tomadas", svg)
        self.assertIn("kWh", svg)

    def test_all_sheets(self):
        _house(self.doc)
        self.doc.recompute()
        pages = sheets.make_electrical_sheets(self.doc)
        self.assertEqual([k for k, _ in pages], ["planta", "quadro_de_cargas", "unifilar", "materiais", "3d"])
        view = [o for o in self.doc.Objects if o.TypeId == "TechDraw::DrawViewSymbol"][0]
        self.assertIn("QUADRO QD1", view.Symbol)
        model.make_point(self.doc, "tomada_baixa", App.Vector(0, 2000, 0), 0, circuit=7, label="Tomada nova")
        self.doc.recompute()
        self.assertIn(">7<", view.Symbol)  # the new circuit is on the diagram


def _boxes_overlap(a, b):
    return min(a.XMax, b.XMax) - max(a.XMin, b.XMin) > 1e-6 and min(a.YMax, b.YMax) - max(a.YMin, b.YMin) > 1e-6


class TestLayout(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("TestArranjo")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_texts_of_crowded_items_do_not_overlap(self):
        V = App.Vector
        _house(self.doc)
        # three sockets side by side on the same wall, and a light right next to the conduit
        for x in (600, 900, 1200):
            model.make_point(self.doc, "tomada_media", V(x, 3000, 0), -90, circuit=2)
        model.make_point(self.doc, "ponto_luz_teto", V(1500, 150, 0), circuit=1, letter="a")
        self.doc.recompute()
        labelled = [o for o in model.points(self.doc) + model.conduits(self.doc) if not o.Labels.isNull()]
        self.assertGreater(len(labelled), 6)
        for i, a in enumerate(labelled):
            for b in labelled[i + 1:]:
                for fa in a.Labels.Faces:
                    for fb in b.Labels.Faces:
                        self.assertFalse(_boxes_overlap(fa.BoundBox, fb.BoundBox), (a.Label, b.Label))

    def test_marks_leave_the_usual_sizes_to_the_note(self):
        from freecad_electrical import layout

        qd, light, switch, socket, a, b, c = _house(self.doc)
        self.doc.recompute()
        prims, frame, sections, _ = model.conduit_marks(c, *layout.marks_of(c))
        texts = [p[2] for p in prims if p[0] == "side"]
        self.assertEqual(texts, ["2"])  # circuit 2 at 2,5 mm² in ø20: just its number
        self.assertIn(2.5, sections)
        prims, _, _, _ = model.conduit_marks(a, 0.5)
        self.assertIn("1,5", [p[2] for p in prims if p[0] == "side"])  # lighting at 1,5 mm² is written
        self.assertIn("2,5 mm²", " ".join(sheets.plan_notes()))

    def test_marks_slide_off_an_item_text(self):
        from freecad_electrical import layout

        V = App.Vector
        _house(self.doc)
        # an item whose texts sit right on the middle of the conduit to the light
        model.make_point(self.doc, "motor", V(1500, -250, 0), circuit=1)
        self.doc.recompute()
        conduit = [c for c in model.conduits(self.doc) if c.Points[-1] == V(3000, 0, 0)][0]
        t, _ = layout.marks_of(conduit)
        self.assertNotAlmostEqual(t, 0.5)

    def test_switch_letter_stays_off_its_circle(self):
        for rotation in (0, 90, 180, 270):
            p = model.make_point(self.doc, "interruptor_simples", App.Vector(rotation * 20, 0, 0), rotation, letter="a")
            self.doc.recompute()
            circle = p.Shape.BoundBox
            self.assertFalse(p.Labels.isNull())
            self.assertFalse(_boxes_overlap(circle, p.Labels.BoundBox), rotation)


class Test3D(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("Teste3D")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def _solids_at(self, shape, x, y, tol=200):
        return [s for s in shape.Solids
                if abs(s.BoundBox.Center.x - x) < tol and abs(s.BoundBox.Center.y - y) < tol]

    def test_runs_drops_and_boxes_from_the_plan(self):
        from freecad_electrical import model3d

        _house(self.doc)
        obj = model3d.model3d(self.doc)
        self.doc.recompute()
        shape = obj.Shape
        self.assertFalse(shape.isNull())
        ceiling = 2800 + model3d.FORRO_GAP
        # the run from the panel to the light, above the forro
        self.assertTrue(any(abs(s.BoundBox.Center.z - ceiling) < 30 and s.BoundBox.XLength > 1000 for s in shape.Solids))
        # the socket at 300 mm: its 4x2 box, and a drop down the wall from the forro to it
        at_socket = self._solids_at(shape, 0, 3000)
        self.assertTrue(any(abs(s.BoundBox.Center.z - 300) < 1 and abs(s.BoundBox.ZLength - 101.6) < 1
                            for s in at_socket))
        self.assertTrue(any(s.BoundBox.ZLength > 2000 for s in at_socket))
        # the light: octagonal box on the ceiling
        self.assertTrue(any(abs(s.BoundBox.ZMin - 2800) < 1 and abs(s.BoundBox.ZLength - 50) < 1
                            for s in self._solids_at(shape, 3000, 0, 80)))
        # coloured by circuit
        self.assertEqual(len(obj.FaceColors), len(shape.Faces))
        self.assertGreater(len({tuple(round(x, 3) for x in c[:3]) for c in obj.FaceColors}), 3)

    def test_bends_have_the_fixed_radius(self):
        from freecad_electrical import model3d

        V = App.Vector
        qd = model.make_point(self.doc, "quadro_terminal", V(0, 0, 0), label="QD1")
        model.make_circuit(self.doc, 1, "Tomadas", "TUG", panel="QD1")
        model.make_point(self.doc, "tomada_baixa", V(3000, 2000, 0), 90, circuit=1)
        model.make_conduit(self.doc, [V(0, 0, 0), V(3000, 0, 0), V(3000, 2000, 0)])
        obj = model3d.model3d(self.doc)
        self.doc.recompute()
        torus = [f for f in obj.Shape.Faces if type(f.Surface).__name__ in ("Toroid", "BSplineSurface")]
        self.assertTrue(torus)  # the corner of the run is a bend, not a mitre
        self.assertIsNotNone(qd)

    def test_service_entrance(self):
        from freecad_electrical import model3d, outputs

        V = App.Vector
        _house(self.doc)
        pad = model.make_point(self.doc, "padrao_entrada", V(-3000, 0, 0), 180)
        run = model.make_conduit(self.doc, [V(-3000, 0, 0), V(0, 0, 0)], installation="enterrado")
        obj = model3d.model3d(self.doc)
        self.doc.recompute()
        e = model.entrance(self.doc, pad)
        self.assertEqual(e["panel"], "QD1")
        self.assertTrue(e["category"])
        self.assertGreater(e["section"], 0)
        self.assertEqual(run.DiameterSuggested, str(e["conduit"]))
        self.assertFalse(pad.Labels.isNull())  # its category beside the symbol
        # the pole, and the buried run below the ground
        self.assertTrue(any(s.BoundBox.ZLength >= 6999 for s in self._solids_at(obj.Shape, -3000, 0, 300)))
        self.assertTrue(any(s.BoundBox.ZMax < 0 and s.BoundBox.XLength > 2000 for s in obj.Shape.Solids))
        rows, _ = outputs.bill_of_materials(self.doc, waste=0.0)
        items = {item: qty for _, item, _, qty in rows}
        self.assertTrue(any(i.startswith("Padrão de entrada") for i in items))
        self.assertTrue(any("(enterrado)" in i for i in items))
        self.assertEqual(items["Caixa 4x2 de embutir"], 2)  # the switch and the socket: the meter is no 4x2 box

    def test_3d_stays_off_the_plan_sheets(self):
        from freecad_electrical import model3d

        _house(self.doc)
        model3d.model3d(self.doc)
        self.doc.recompute()
        self.assertNotIn("Instalacao3D", [o.Name for o in sheets.sources(self.doc)])


    def test_isometric_page_in_circuit_colours(self):
        from freecad_electrical import model3d

        _house(self.doc)
        page, view = sheets.make_3d_page(self.doc, "Casa")
        self.assertIn("<polyline", view.Symbol)
        self.assertIn(model3d._hex(model3d.circuit_color(1)), view.Symbol)
        self.assertTrue(model3d.model3d(self.doc).IsoScale.startswith("1:"))
        model.make_point(self.doc, "tomada_baixa", App.Vector(1500, 3000, 0), -90, circuit=2)
        model.make_conduit(self.doc, [App.Vector(0, 3000, 0), App.Vector(1500, 3000, 0)])
        before = view.Symbol
        self.doc.recompute()
        self.assertNotEqual(view.Symbol, before)  # it follows the plan

    def test_show_some_circuits(self):
        from freecad_electrical import model3d

        _house(self.doc)
        page, view = sheets.make_3d_page(self.doc, "Casa")
        obj = model3d.model3d(self.doc)
        everything = len(obj.Shape.Solids)
        light = model3d._hex(model3d.circuit_color(1))
        sockets = model3d._hex(model3d.circuit_color(2))
        model3d.show_circuits(self.doc, [2])
        self.assertLess(len(obj.Shape.Solids), everything)
        self.assertIn(sockets, view.Symbol)
        self.assertNotIn(light, view.Symbol)  # the light circuit is gone, from the 3D and its page
        self.assertNotIn(App.Vector(3000, 0, 0), [App.Vector(round(s.BoundBox.Center.x), round(s.BoundBox.Center.y), 0)
                                                   for s in obj.Shape.Solids if s.BoundBox.ZLength < 60])
        self.assertIn("circuito 2", page.Template.EditableTexts.get(
            next(k for k in page.Template.EditableTexts if "title" in k.lower()), ""))
        model3d.show_circuits(self.doc, [1, 2])
        self.assertEqual(len(obj.Shape.Solids), everything)
        model3d.show_circuits(self.doc, [])
        self.assertEqual(len(obj.Shape.Solids), everything)
        with self.assertRaises(ValueError):
            model3d.show_circuits(self.doc, [99])

    def test_boxes_go_into_the_nearest_wall(self):
        import Arch
        import Draft
        from freecad_electrical import model3d

        V = App.Vector
        base = Draft.make_wire([V(0, 0, 0), V(4000, 0, 0)])
        Arch.makeWall(base, width=150, height=2800, align="Center")  # faces at y = -75 and 75
        model.make_circuit(self.doc, 1, "Iluminação", "Iluminação", panel="QD1")
        # a switch clicked 300 mm off the wall, a panel drawn across it
        switch = model.make_point(self.doc, "interruptor_simples", V(1000, 300, 0), 0, circuit=1, letter="a")
        model.make_point(self.doc, "quadro_terminal", V(2500, 250, 0), 90, label="QD1")
        obj = model3d.model3d(self.doc)
        self.doc.recompute()
        boxes = [s for s in obj.Shape.Solids if abs(s.BoundBox.ZLength - 101.6) < 1 and abs(s.BoundBox.Center.x - 1000) < 1]
        self.assertTrue(boxes)
        self.assertTrue(all(-75 <= s.BoundBox.YMin and s.BoundBox.YMax <= 75 + 1e-6 for s in boxes))  # in the wall
        panel = [s for s in obj.Shape.Solids if abs(s.BoundBox.Center.x - 2500) < 1 and s.BoundBox.ZLength > 300]
        self.assertTrue(panel)
        box = panel[0].BoundBox
        self.assertAlmostEqual(box.YMax, 75, places=3)  # flush with the wall face
        self.assertGreater(box.XLength, box.YLength)  # along the wall
        self.assertEqual(model.SYMBOLS[switch.Symbol].beside > 0, True)


class TestWalk(unittest.TestCase):
    def setUp(self):
        self.doc = App.newDocument("TestePasseio")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_floors_start_walls_and_camera(self):
        import Arch
        import Draft
        from freecad_electrical import walk

        V = App.Vector
        _house(self.doc)
        _house(self.doc, z=3000, first=11, panel="QD2")
        self.assertEqual([z for z, _ in walk.floors(self.doc)], [0.0, 3000.0])
        base = Draft.make_wire([V(-1000, -1000, 0), V(5000, -1000, 0)])
        Arch.makeWall(base, width=150, height=2800, align="Center")
        self.doc.recompute()
        walls = walk.wall_segments(self.doc, walk.EYE)
        self.assertTrue(walls)
        self.assertFalse(walk.wall_segments(self.doc, 3000 + walk.EYE))  # the wall is on the ground floor only
        # walking into the wall is stopped, walking along it or away from it is not
        self.assertTrue(walk.blocked(walls, (1000, -500), (1000, -1200)))
        self.assertFalse(walk.blocked(walls, (1000, -500), (1500, -500)))
        self.assertFalse(walk.blocked(walls, (1000, -850), (1000, -500)))
        # the camera looks where the person faces, with Z up
        r = walk.camera_rotation(90, 0)
        forward = r.multVec(V(0, 0, -1))
        self.assertAlmostEqual(forward.y, 1.0, places=6)
        self.assertAlmostEqual(r.multVec(V(0, 1, 0)).z, 1.0, places=6)
        down = walk.camera_rotation(0, -30).multVec(V(0, 0, -1))
        self.assertLess(down.z, -0.4)


    def test_3d_of_one_floor(self):
        from freecad_electrical import model3d

        _house(self.doc)
        _house(self.doc, z=3000, first=11, panel="QD2")
        obj = model3d.model3d(self.doc)
        self.doc.recompute()
        everything = obj.Shape.BoundBox
        obj.VisibleFloor = "3000"
        self.doc.recompute()
        self.assertGreaterEqual(obj.Shape.BoundBox.ZMin, 3000 - 100)
        self.assertLess(everything.ZMin, 1000)  # the ground floor was there before
        obj.VisibleFloor = ""
        self.doc.recompute()
        self.assertAlmostEqual(obj.Shape.BoundBox.ZMin, everything.ZMin, places=3)

    def test_translucent_forro(self):
        import Arch
        import Draft
        from freecad_electrical import model3d

        _house(self.doc)
        rect = Draft.make_rectangle(4000, 3000)
        volume = self.doc.addObject("Part::Extrusion", "SpaceVolume")
        volume.Base = rect
        volume.Dir = App.Vector(0, 0, 2800)
        volume.Solid = True
        self.doc.recompute()
        space = Arch.makeSpace(volume)
        self.doc.recompute()
        if space.Shape.isNull():
            self.skipTest("the space has no shape here")
        obj = model3d.model3d(self.doc)
        self.doc.recompute()
        before = len(obj.Shape.Solids)
        obj.ShowCeiling = True
        self.doc.recompute()
        forro = [s for s in obj.Shape.Solids if abs(s.BoundBox.ZMin - (space.Shape.BoundBox.ZMin + 2800)) < 1e-3
                 and abs(s.BoundBox.ZLength - model3d.FORRO_THICKNESS) < 1e-3]
        self.assertTrue(forro)
        self.assertEqual(len(obj.Shape.Solids), before + len(forro))
        self.assertTrue(any(abs(c[3] - model3d.CEILING_COLOR[3]) < 0.01 for c in obj.FaceColors))  # translucent
