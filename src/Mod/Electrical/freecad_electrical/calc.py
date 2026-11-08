# SPDX-License-Identifier: LGPL-2.1-or-later

"""Electrical calculations: currents, protection and conductor suggestions, phase loads, and
the basic load-planning minimums of NBR 5410. Pure functions, no FreeCAD needed.

These are estimates to check a design, not a substitute for the responsible engineer: the
conductor table is the simplified one for copper, PVC insulation, conduit in masonry
(reference method B1, 30 °C), without grouping, temperature or voltage-drop corrections.
"""

import math

SYSTEMS = {"380/220 V": (380.0, 220.0), "220/127 V": (220.0, 127.0)}
DEFAULT_SYSTEM = "380/220 V"

MONO, BI, TRI, TRI_N = "Monofásico (F+N)", "Bifásico (F+F)", "Trifásico (3F)", "Trifásico (3F+N)"
CONNECTIONS = [MONO, BI, TRI, TRI_N]
PHASES = "RST"

KINDS = ["Iluminação", "TUG", "TUE", "Motor", "Alimentador"]
MIN_SECTION = {"Iluminação": 1.5, "TUG": 2.5, "TUE": 2.5, "Motor": 2.5, "Alimentador": 2.5}
MIN_BREAKER = {"Iluminação": 10, "TUG": 16, "TUE": 16, "Motor": 10, "Alimentador": 16}

BREAKERS = [6, 10, 16, 20, 25, 32, 40, 50, 63, 70, 80, 100, 125, 160, 200]
# Current capacity (A) of copper conductors, PVC, method B1 at 30 °C (NBR 5410, table 36):
# section mm² -> (2 loaded conductors, 3 loaded conductors)
CAPACITY = {1.5: (17.5, 15.5), 2.5: (24, 21), 4: (32, 28), 6: (41, 36), 10: (57, 50), 16: (76, 68),
            25: (101, 89), 35: (125, 110), 50: (151, 134), 70: (192, 171), 95: (232, 207), 120: (269, 239)}

CV_W = 735.5
MOTOR_EFFICIENCY = 0.85


def voltages(system):
    return SYSTEMS.get(system, SYSTEMS[DEFAULT_SYSTEM])


def phase_count(connection):
    return {MONO: 1, BI: 2, TRI: 3, TRI_N: 3}[connection]


def loaded_conductors(connection):
    return 2 if connection in (MONO, BI) else 3


def item_power(power, unit, quantity=1, power_factor=0.92):
    """(VA, W) of `quantity` items of `power` in VA, W or CV."""
    quantity = max(int(quantity or 1), 1)
    pf = power_factor if power_factor and power_factor > 0 else 1.0
    if unit == "VA":
        va = power * quantity
        return va, va * pf
    if unit == "CV":
        w = power * CV_W / MOTOR_EFFICIENCY * quantity
        return w / pf, w
    w = power * quantity  # W
    return w / pf, w


def power_text(power, unit, quantity=1):
    """How the power is written on the plan: 300VA, 2x100W, 3CV."""
    if not power:
        return ""
    value = f"{power:g}".replace(".", ",")
    if quantity and quantity > 1:
        return f"{int(quantity)}x{value}{unit}"
    return f"{value}{unit}"


def current(va, connection, system):
    """Design current (A) of a circuit of `va` volt-amperes."""
    v_ll, v_ln = voltages(system)
    if connection == MONO:
        return va / v_ln
    if connection == BI:
        return va / v_ll
    return va / (math.sqrt(3) * v_ll)


def suggest_breaker(amps, kind):
    floor = MIN_BREAKER.get(kind, 10)
    for size in BREAKERS:
        if size >= amps and size >= floor:
            return size
    return BREAKERS[-1]


def capacity(section, connection):
    two, three = CAPACITY.get(section, (0, 0))
    return two if loaded_conductors(connection) == 2 else three


def suggest_section(breaker, kind, connection):
    """Smallest section that carries the breaker current and meets the minimum for the kind."""
    floor = MIN_SECTION.get(kind, 2.5)
    for section in sorted(CAPACITY):
        if section >= floor and capacity(section, connection) >= breaker:
            return section
    return max(CAPACITY)


def phase_loads(va, connection, phases):
    """VA on each phase R, S, T."""
    loads = {p: 0.0 for p in PHASES}
    used = [p for p in (phases or "") if p in PHASES]
    if not used:
        return loads
    share = va / len(used)
    for p in used:
        loads[p] += share
    return loads


def choose_phases(connection, current_loads, counts=None):
    """Least loaded phase(s) for a circuit; ties go to the phase with fewer circuits."""
    n = phase_count(connection)
    if n == 3:
        return PHASES
    counts = counts or {}
    order = sorted(PHASES, key=lambda p: (round(current_loads.get(p, 0.0), 3), counts.get(p, 0), p))
    return "".join(sorted(order[:n]))


def balance(circuits):
    """Phases for each circuit that spread the load evenly: biggest loads first, each on the
    least loaded phase(s). `circuits` is [(key, va, connection)]; returns {key: phases}."""
    loads = {p: 0.0 for p in PHASES}
    counts = {p: 0 for p in PHASES}
    result = {}
    for key, va, connection in sorted(circuits, key=lambda c: -c[1]):
        phases = choose_phases(connection, loads, counts)
        result[key] = phases
        for p in phases:
            loads[p] += va / len(phases)
            counts[p] += 1
    return result


def imbalance(loads):
    """Largest deviation from the mean phase load, in % of the mean."""
    values = [loads.get(p, 0.0) for p in PHASES]
    mean = sum(values) / 3
    if mean <= 0:
        return 0.0
    return max(abs(v - mean) for v in values) / mean * 100


# --- NBR 5410 load planning (item 9.5.2), simplified ----------------------------------

WET = ("cozinha", "copa", "serviço", "servico", "lavanderia", "banheiro", "lavabo")
KITCHEN_LIKE = ("cozinha", "copa", "serviço", "servico", "lavanderia")


def room_category(name):
    low = (name or "").lower()
    if any(w in low for w in ("banheiro", "lavabo", "wc")):
        return "banheiro"
    if any(w in low for w in KITCHEN_LIKE):
        return "cozinha"
    if any(w in low for w in ("varanda", "sacada")):
        return "varanda"
    return "seco"


def min_lighting_va(area_m2):
    """At least 100 VA for the first 6 m², plus 60 VA for each whole 4 m² after that."""
    if area_m2 <= 6:
        return 100
    return 100 + 60 * int((area_m2 - 6) // 4)


def min_sockets(category, area_m2, perimeter_m):
    if category == "banheiro":
        return 1
    if category == "cozinha":
        return math.ceil(perimeter_m / 3.5) if perimeter_m > 0 else 1
    if category == "varanda":
        return 1
    if area_m2 <= 6:
        return 1
    return math.ceil(perimeter_m / 5.0) if perimeter_m > 0 else 1


def min_socket_va(category, count):
    """Minimum VA of each socket: 600 VA for the first three in wet areas, 100 VA otherwise."""
    if category in ("banheiro", "cozinha"):
        return [600] * min(count, 3) + [100] * max(count - 3, 0)
    return [100] * count
