# SPDX-License-Identifier: LGPL-2.1-or-later

"""Routing of circuits through the conduits drawn on the plan.

The conduits form a graph: their vertices are nodes (vertices closer than MERGE_MM are one
node, and a vertex lying on another conduit splits it), each straight run is an edge. Items
attach to the node at their position, or to the nearest run, within ATTACH_PAPER_MM on paper.
The panel of a circuit is the panel item (QD, QG) named like the circuit's Panel field, or the
only panel on the plan.

From the panel, each circuit follows the shortest path to each of its items:
* to a load (outlet, light, motor): its phase(s), neutral and earth (see model.conductors_of);
* to a switch: one phase;
* between a switch and the lights with the same letter: one return ("retorno") per letter.

The result gives, for each run, the conductors of each circuit in it; for each circuit, its
length from the panel to the farthest load (vertical drops included, for the voltage drop) and
how many circuits share its most crowded run (for the grouping factor).

Simplifications: two-way and intermediate switches carry one return per letter, like simple
ones (their travellers are not counted); the path is the shortest one, as an installer would
take.
"""

import heapq
import math
import re

import FreeCAD as App

from . import model
from .symbols import SYMBOLS

MERGE_MM = 50.0
ATTACH_PAPER_MM = 8.0
DEFAULT_CEILING_MM = 2800.0
PANELS = ("quadro_terminal", "quadro_geral")

_cache = {}


def is_switch(symbol):
    return symbol.startswith("interruptor")


def letters_of(text):
    return re.findall(r"[a-z]\d*", (text or "").lower())


RISERS = ("circuito_sobe", "circuito_desce")


def _xy(v):
    """3D coordinates: items and conduits of each floor sit at the floor's elevation."""
    return (float(v.x), float(v.y), float(v.z))


def _dist(a, b):
    return math.dist(a, b)


def _project(p, a, b):
    """(parameter 0..1, distance) of p on the segment a-b."""
    d = [b[i] - a[i] for i in range(3)]
    length2 = sum(x * x for x in d)
    if length2 < 1e-12:
        return 0.0, _dist(p, a)
    t = sum((p[i] - a[i]) * d[i] for i in range(3)) / length2
    t = min(max(t, 0.0), 1.0)
    return t, _dist(p, tuple(a[i] + t * d[i] for i in range(3)))


def _cls(installation):
    return "piso" if installation == "piso" else "alto"


def _signature(doc):
    sig = []
    for c in model.conduits(doc):
        sig.append((c.Name, tuple(_xy(p) for p in c.Points), c.Installation))
    for p in model.points(doc):
        sig.append((p.Name, p.Symbol, _xy(p.Position), p.Circuit.Name if p.Circuit is not None else "",
                    p.Letter, p.Label, float(p.Height.Value)))
    for c in model.circuits(doc):
        sig.append((c.Name, c.Panel, c.Connection, c.Number))
    inst = model.installation(doc, create=False)
    sig.append((model.scale_of(doc), ceiling_of(doc), inst.Name if inst else ""))
    return tuple(sig)


def ceiling_of(doc):
    inst = model.installation(doc, create=False)
    value = getattr(inst, "CeilingHeight", None) if inst is not None else None
    value = float(value.Value) if value is not None and hasattr(value, "Value") else float(value or 0)
    return value if value > 0 else DEFAULT_CEILING_MM


class Network:
    """Graph of the conduits and the routes of the circuits. Build with `network(doc)`."""

    def __init__(self, doc):
        self.doc = doc
        self.ceiling = ceiling_of(doc)
        self.nodes = []        # (x, y, z)
        self.node_cls = []     # "piso" or "alto" (ceiling and walls): floor and ceiling runs meet only at items
        self.edges = []        # dict(u, v, length, conduit, installation)
        self.adjacent = {}     # node -> [(edge index, other node)]
        self.attached = {}     # point name -> node
        self.conduit_edges = {}  # conduit name -> [edge indices] in path order
        self.contents = {}     # edge index -> {circuit name: {"load", "feed", "returns"}}
        self.circuit_info = {}  # circuit name -> {"routed", "length_m", "grouping", "edges"}
        self.unrouted = []     # labels of items with a circuit that no conduit reaches
        self.switch_circuit = {}  # switch name -> circuit it is on
        self._build()
        self._route()

    # -- graph -----------------------------------------------------------------------------------

    def _node(self, xy, cls):
        for i, n in enumerate(self.nodes):
            if self.node_cls[i] == cls and _dist(n, xy) <= MERGE_MM:
                return i
        self.nodes.append(xy)
        self.node_cls.append(cls)
        return len(self.nodes) - 1

    def _add_edge(self, u, v, conduit, installation):
        if u == v:
            return None
        self.edges.append({"u": u, "v": v, "length": _dist(self.nodes[u], self.nodes[v]), "conduit": conduit,
                           "installation": installation})
        return len(self.edges) - 1

    def _build(self):
        conduits = model.conduits(self.doc)
        runs = []
        for c in conduits:
            cls = _cls(c.Installation)
            pts = [_xy(p) for p in c.Points]
            ids = [self._node(p, cls) for p in pts]
            runs.append((c, ids))
        for c, ids in runs:  # split runs at the vertices of other conduits lying on them
            chain = []
            for a, b in zip(ids, ids[1:]):
                pa, pb = self.nodes[a], self.nodes[b]
                inner = []
                for k, n in enumerate(self.nodes):
                    if k in (a, b) or self.node_cls[k] != self.node_cls[a]:
                        continue
                    t, d = _project(n, pa, pb)
                    if d <= MERGE_MM and 1e-6 < t < 1 - 1e-6:
                        inner.append((t, k))
                seq = [a] + [k for _, k in sorted(inner)] + [b]
                chain += seq if not chain else seq[1:]
            edges = []
            for u, v in zip(chain, chain[1:]):
                e = self._add_edge(u, v, c.Name, c.Installation)
                if e is not None:
                    edges.append(e)
            self.conduit_edges[c.Name] = edges
        attach = ATTACH_PAPER_MM * model.scale_of(self.doc)
        for p in model.points(self.doc):
            xy = _xy(p.Position)
            best = min(((i, _dist(n, xy)) for i, n in enumerate(self.nodes) if self._degree_ok(i)),
                       key=lambda t: t[1], default=(None, float("inf")))
            if best[0] is not None and best[1] <= attach:
                self.attached[p.Name] = best[0]
            else:
                near = min(((e, *_project(xy, self.nodes[ed["u"]], self.nodes[ed["v"]]))
                            for e, ed in enumerate(self.edges)), key=lambda t: t[2], default=None)
                if near is not None and near[2] <= attach:
                    self.attached[p.Name] = self._split(near[0], near[1])
            if p.Name in self.attached:
                self._bridge(p.Name, xy, attach)
        self._connect_risers(attach)
        for e, ed in enumerate(self.edges):
            self.adjacent.setdefault(ed["u"], []).append((e, ed["v"]))
            self.adjacent.setdefault(ed["v"], []).append((e, ed["u"]))

    def _connect_risers(self, attach):
        """Riser symbols ("circuito sobe/desce") at the same place on different floors are joined
        by a vertical run (the riser shaft)."""
        risers = sorted((p for p in model.points(self.doc) if p.Symbol in RISERS and p.Name in self.attached),
                        key=lambda p: p.Position.z)
        stacks = []
        for p in risers:
            for stack in stacks:
                top = stack[-1]
                if math.hypot(p.Position.x - top.Position.x, p.Position.y - top.Position.y) <= attach \
                        and p.Position.z - top.Position.z > MERGE_MM:
                    stack.append(p)
                    break
            else:
                stacks.append([p])
        for stack in stacks:
            for a, b in zip(stack, stack[1:]):
                u, v = self.attached[a.Name], self.attached[b.Name]
                if u != v:
                    self.edges.append({"u": u, "v": v, "length": abs(b.Position.z - a.Position.z),
                                       "conduit": None, "installation": "prumada"})

    def _bridge(self, name, xy, attach):
        """An item where floor and ceiling runs both arrive (a panel, a box) joins them."""
        node = self.attached[name]
        others = [(i, _dist(n, xy)) for i, n in enumerate(self.nodes)
                  if self.node_cls[i] != self.node_cls[node] and self._degree_ok(i)]
        best = min(others, key=lambda t: t[1], default=(None, float("inf")))
        if best[0] is not None and best[1] <= attach:
            self.edges.append({"u": node, "v": best[0], "length": _dist(self.nodes[node], self.nodes[best[0]]),
                               "conduit": None, "installation": "caixa"})

    def _degree_ok(self, node):
        return any(node in (ed["u"], ed["v"]) for ed in self.edges)

    def _split(self, e, t):
        """Split edge e at parameter t; returns the new node."""
        ed = self.edges[e]
        a, b = self.nodes[ed["u"]], self.nodes[ed["v"]]
        if t <= 1e-6:
            return ed["u"]
        if t >= 1 - 1e-6:
            return ed["v"]
        self.nodes.append(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))
        self.node_cls.append(self.node_cls[ed["u"]])
        k = len(self.nodes) - 1
        old_v = ed["v"]
        ed["v"] = k
        ed["length"] = _dist(a, self.nodes[k])
        new = self._add_edge(k, old_v, ed["conduit"], ed["installation"])
        chain = self.conduit_edges[ed["conduit"]]
        chain.insert(chain.index(e) + 1, new)
        return k

    def _tree(self, root):
        """Shortest-path tree from root: {node: (distance, parent edge, parent node)}."""
        best = {root: (0.0, None, None)}
        heap = [(0.0, root)]
        while heap:
            d, n = heapq.heappop(heap)
            if d > best[n][0] + 1e-9:
                continue
            for e, other in self.adjacent.get(n, []):
                nd = d + self.edges[e]["length"]
                if other not in best or nd < best[other][0] - 1e-9:
                    best[other] = (nd, e, n)
                    heapq.heappush(heap, (nd, other))
        return best

    @staticmethod
    def _path(tree, node):
        edges = []
        while node in tree and tree[node][1] is not None:
            edges.append(tree[node][1])
            node = tree[node][2]
        return edges

    # -- routing -------------------------------------------------------------------------------

    def panel_points(self):
        return [p for p in model.points(self.doc) if p.Symbol in PANELS]

    def panel_for(self, circuit, members=()):
        """The panel item of a circuit: named like its Panel field (the one on the floor of its
        items if several have that name), or the only panel on the plan."""
        panels = self.panel_points()
        named = [p for p in panels
                 if circuit.Panel and circuit.Panel.lower() in (p.Label.lower(), (p.Letter or "").lower())]
        if len(named) > 1 and members:
            z = members[0].Position.z
            named.sort(key=lambda p: abs(p.Position.z - z))
        if named:
            return named[0]
        return panels[0] if len(panels) == 1 else None

    def _vertical(self, point, node):
        """Vertical run (mm) between the conduit at `node` and the item."""
        h = float(point.Height.Value)
        if self.node_cls[node] == "piso":
            return h
        return self.ceiling - h if h > 0 else 0.0

    def _route(self):
        doc = self.doc
        circuits = model.circuits(doc)
        pts = model.points(doc)
        lights_by_letter = {}
        for p in pts:
            if SYMBOLS[p.Symbol].kind == "Iluminação" and p.Circuit is not None:
                for letter in letters_of(p.Letter):
                    lights_by_letter.setdefault(letter, []).append(p)
        trees = {}
        for c in circuits:
            members = [p for p in pts if p.Circuit == c]
            panel = self.panel_for(c, members)
            switches = [p for p in pts if is_switch(p.Symbol) and self._switch_circuit(p, lights_by_letter) == c]
            for s in switches:
                self.switch_circuit[s.Name] = c
            info = {"routed": False, "length_m": 0.0, "grouping": 1, "edges": set(), "panel": panel.Label if panel else ""}
            self.circuit_info[c.Name] = info
            loads = [p for p in members if SYMBOLS[p.Symbol].kind]
            if getattr(c, "Feeds", ""):
                self._route_feeder(c, panel, info, trees)
                continue
            if panel is None or panel.Name not in self.attached:
                info["length_m"] = self._estimate(panel, loads)
                self.unrouted += [p.Label for p in loads]
                continue
            root = self.attached[panel.Name]
            tree = trees.get(root) or trees.setdefault(root, self._tree(root))
            panel_drop = self._vertical(panel, root)
            longest = 0.0
            for p in loads + switches:
                node = self.attached.get(p.Name)
                if node is None or node not in tree:
                    if p in loads:  # no conduit to it yet: a right-angle estimate
                        self.unrouted.append(p.Label)
                        longest = max(longest, self._estimate(panel, [p]) * 1000.0)
                    continue
                role = "load" if p in loads else "feed"
                for e in self._path(tree, node):
                    self._mark(e, c)[role] = True
                if p in loads:
                    longest = max(longest, tree[node][0] + self._vertical(p, node) + panel_drop)
            for s in switches:
                ns = self.attached.get(s.Name)
                if ns is None or ns not in tree:
                    continue
                to_switch = set(self._path(tree, ns))
                for letter in letters_of(s.Letter):
                    for light in lights_by_letter.get(letter, []):
                        nl = self.attached.get(light.Name)
                        if light.Circuit != c or nl is None or nl not in tree:
                            continue
                        for e in to_switch ^ set(self._path(tree, nl)):
                            self._mark(e, c)["returns"].add(letter)
            info["routed"] = True
            info["length_m"] = round(longest / 1000.0, 2)
        for e, content in self.contents.items():
            for name in content:
                self.circuit_info[name]["edges"].add(e)
        for name, info in self.circuit_info.items():
            info["grouping"] = max((len(self.contents[e]) for e in info["edges"]), default=1)

    def _route_feeder(self, c, panel, info, trees):
        """A feeder goes from its panel to the panel it feeds (through the risers between floors)."""
        target = next((p for p in self.panel_points()
                       if c.Feeds.lower() in (p.Label.lower(), (p.Letter or "").lower())), None)
        if panel is None or target is None:
            return
        if panel.Name not in self.attached or target.Name not in self.attached:
            info["length_m"] = self._estimate(panel, [target])
            return
        root = self.attached[panel.Name]
        tree = trees.get(root) or trees.setdefault(root, self._tree(root))
        node = self.attached[target.Name]
        if node not in tree:
            info["length_m"] = self._estimate(panel, [target])
            return
        for e in self._path(tree, node):
            self._mark(e, c)["load"] = True
        info["routed"] = True
        info["length_m"] = round((tree[node][0] + self._vertical(panel, root) + self._vertical(target, node)) / 1000.0, 2)

    def _switch_circuit(self, switch, lights_by_letter):
        if switch.Circuit is not None:
            return switch.Circuit
        for letter in letters_of(switch.Letter):
            for light in lights_by_letter.get(letter, []):
                return light.Circuit
        return None

    def _mark(self, e, circuit):
        return self.contents.setdefault(e, {}).setdefault(
            circuit.Name, {"load": False, "feed": False, "returns": set()})

    def _estimate(self, panel, loads):
        """Length (m) without conduits: right-angle path from the panel plus vertical drops."""
        if panel is None or not loads:
            return 0.0
        pp = _xy(panel.Position)
        drop = self.ceiling - float(panel.Height.Value)
        longest = 0.0
        for p in loads:
            q = _xy(p.Position)
            h = float(p.Height.Value)
            longest = max(longest, abs(q[0] - pp[0]) + abs(q[1] - pp[1]) + abs(q[2] - pp[2]) + drop
                          + (self.ceiling - h if h > 0 else 0))
        return round(longest / 1000.0, 2)

    # -- results ----------------------------------------------------------------------------------

    def conductors(self, e):
        """[(circuit, [marks])] in edge e, ordered by circuit number."""
        out = []
        for name, content in self.contents.get(e, {}).items():
            c = self.doc.getObject(name)
            if c is None:
                continue
            marks = list(model.conductors_of(c)) if content["load"] else (["fase"] if content["feed"] else [])
            marks += ["retorno"] * len(content["returns"])
            if marks:
                out.append((c, marks))
        return sorted(out, key=lambda t: t[0].Number)

    def edge_at(self, conduit, fraction):
        """Edge of `conduit` at `fraction` (0..1) of its length."""
        chain = self.conduit_edges.get(conduit.Name, [])
        if not chain:
            return None
        total = sum(self.edges[e]["length"] for e in chain)
        target = total * min(max(float(fraction), 0.0), 1.0)
        for e in chain:
            if target <= self.edges[e]["length"] + 1e-9:
                return e
            target -= self.edges[e]["length"]
        return chain[-1]

    def vertical_runs(self):
        """[(point, circuit, marks, length mm)]: drops from the conduit down (or up) to each routed
        item and to the panels, with the conductors of each circuit that ends there."""
        out = []
        for p in model.points(self.doc):
            node = self.attached.get(p.Name)
            if node is None:
                continue
            drop = self._vertical(p, node)
            if drop <= 0:
                continue
            own = self.switch_circuit.get(p.Name) if is_switch(p.Symbol) else p.Circuit
            chosen = {}
            for e, _ in self.adjacent.get(node, []):
                for c, marks in self.conductors(e):
                    if (p.Symbol in PANELS or c == own) and len(marks) > len(chosen.get(c.Name, (None, []))[1]):
                        chosen[c.Name] = (c, marks)
            out += [(p, c, marks, drop) for c, marks in chosen.values()]
        return out


def network(doc):
    """The routing of `doc`, rebuilt only when conduits, items or circuits changed."""
    sig = _signature(doc)
    key = (doc.Name, doc.Uid)
    cached = _cache.get(key)
    if cached is not None and cached[0] == sig:
        return cached[1]
    net = Network(doc)
    _cache.clear()  # one document at a time is enough, and closed documents must not linger
    _cache[key] = (sig, net)
    return net


def forget(doc):
    _cache.clear()
