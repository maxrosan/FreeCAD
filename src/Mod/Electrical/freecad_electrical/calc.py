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
    if any(w in low for w in ("externa", "externo", "quintal", "jardim", "garagem", "piscina")):
        return "externa"
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
    if category in ("varanda", "externa"):
        return 1
    if area_m2 <= 6:
        return 1
    return math.ceil(perimeter_m / 5.0) if perimeter_m > 0 else 1


def min_socket_va(category, count):
    """Minimum VA of each socket: 600 VA for the first three in wet areas, 100 VA otherwise."""
    if category in ("banheiro", "cozinha"):
        return [600] * min(count, 3) + [100] * max(count - 3, 0)
    return [100] * count


# --- conductor sizing with corrections (NBR 5410 6.2.5 and 6.2.7) --------------------------

RESISTIVITY = 0.0225  # copper at the 70 °C limit of PVC insulation, ohm.mm²/m (reactance ignored)
MAX_VOLTAGE_DROP = 4.0  # % for terminal circuits, from the panel (NBR 5410 6.2.7)
# Grouping factor for circuits bunched in one conduit (NBR 5410 table 42, reference method B1):
# up to N circuits -> factor
GROUPING = [(1, 1.00), (2, 0.80), (3, 0.70), (4, 0.65), (5, 0.60), (6, 0.57), (7, 0.54), (8, 0.52),
            (11, 0.50), (15, 0.45), (19, 0.41)]
GROUPING_MANY = 0.38
# Ambient temperature factor for PVC (NBR 5410 table 40, air): up to °C -> factor
TEMPERATURE = [(10, 1.22), (15, 1.17), (20, 1.12), (25, 1.06), (30, 1.00), (35, 0.94), (40, 0.87),
               (45, 0.79), (50, 0.71), (55, 0.61), (60, 0.50)]


def grouping_factor(circuits_together):
    n = max(int(circuits_together or 1), 1)
    for limit, factor in GROUPING:
        if n <= limit:
            return factor
    return GROUPING_MANY


def temperature_factor(celsius):
    for limit, factor in TEMPERATURE:
        if celsius <= limit:
            return factor
    return TEMPERATURE[-1][1]


def voltage_drop(amps, length_m, section, connection, system):
    """Voltage drop (%) of a circuit carrying `amps` over `length_m` metres of `section` mm²."""
    if not amps or not length_m or not section:
        return 0.0
    v_ll, v_ln = voltages(system)
    if connection == MONO:
        return 2 * RESISTIVITY * length_m * amps / section / v_ln * 100
    if connection == BI:
        return 2 * RESISTIVITY * length_m * amps / section / v_ll * 100
    return math.sqrt(3) * RESISTIVITY * length_m * amps / section / v_ll * 100


def size_conductor(breaker, kind, connection, amps=0.0, length_m=0.0, system=DEFAULT_SYSTEM, factor=1.0,
                   max_drop=MAX_VOLTAGE_DROP):
    """Smallest section that meets the minimum for the kind, carries the breaker current after the
    correction `factor` (grouping x temperature) and keeps the voltage drop within `max_drop`.
    Returns (section, reason): "mínimo", "capacidade" or "queda de tensão"."""
    floor = MIN_SECTION.get(kind, 2.5)
    allowed = sorted(s for s in CAPACITY if s >= floor)
    by_capacity = next((s for s in allowed if capacity(s, connection) * factor >= breaker), None)
    if by_capacity is None:
        return max(CAPACITY), "capacidade"
    reason = "mínimo" if by_capacity == allowed[0] else "capacidade"
    if length_m and amps:
        for section in (s for s in allowed if s >= by_capacity):
            if voltage_drop(amps, length_m, section, connection, system) <= max_drop:
                return section, (reason if section == by_capacity else "queda de tensão")
        return max(CAPACITY), "queda de tensão"
    return by_capacity, reason


# --- residual-current devices (NBR 5410 5.1.3.2.2) --------------------------------------------

RCD_ROOMS = {"banheiro": "ponto em banheiro", "cozinha": "ponto em área molhada (cozinha, serviço)",
             "varanda": "ponto em área externa", "externa": "ponto em área externa"}
RCD_RATINGS = [25, 40, 63, 80, 100, 125]


def rcd_reason(category, kind, height_mm, ceiling_item):
    """Why a point of use needs its circuit protected by a 30 mA RCD, or "" if it does not.
    `kind` is the circuit kind the item loads (None for switches and boxes, which are not points
    of use). Lighting fixtures at 2.5 m or higher are exempt; in outdoor areas the rule is for
    sockets."""
    if category not in RCD_ROOMS or not kind:
        return ""
    if kind == "Iluminação" and (ceiling_item or height_mm >= 2500):
        return ""
    if category in ("varanda", "externa") and kind not in ("TUG", "TUE"):
        return ""
    return RCD_ROOMS[category]


NEGLIGIBLE_SHARE = 0.30  # NBR 5410 6.2.5.5.6


def negligible_in_group(amps, section, connection, factor):
    """A circuit carrying up to 30 % of its grouped capacity can be left out of the grouping count."""
    return amps <= NEGLIGIBLE_SHARE * capacity(section, connection) * factor


def rcd_rating(breaker):
    return next((size for size in RCD_RATINGS if size >= breaker), RCD_RATINGS[-1])


def spare_ways(circuit_count):
    """Spare ways a panel must have (NBR 5410 6.5.4.7)."""
    if circuit_count <= 6:
        return 2
    if circuit_count <= 12:
        return 3
    if circuit_count <= 30:
        return 4
    return math.ceil(circuit_count * 0.15)


# --- demand and service entrance (Neoenergia DIS-NOR-030 rev. 04, items 6.26 to 6.28) ---------
#
# The demand method and categories of the Neoenergia distributors (Cosern, Coelba, Celpe,
# Brasília, Elektro). Other utilities use similar tables with other limits: check them.

G_LIGHT, G_HEATER, G_BOILER, G_APPLIANCE, G_STOVE, G_AIR, G_MOTOR, G_PUMP, G_EV, G_SPECIAL, G_OTHER = (
    "Iluminação e tomadas", "Chuveiro, torneira, aquecedor de passagem, ferro", "Aquecedor de acumulação (boiler)",
    "Secadora, forno, lava-louças, micro-ondas", "Fogão elétrico", "Ar-condicionado", "Motor",
    "Bomba e hidromassagem", "Recarga de veículo elétrico", "Equipamento especial", "Outros (fator 1)")
DEMAND_GROUPS = [G_LIGHT, G_HEATER, G_BOILER, G_APPLIANCE, G_STOVE, G_AIR, G_MOTOR, G_PUMP, G_EV, G_SPECIAL,
                 G_OTHER]
AUTO_GROUP = "Automático"

# words in the item or circuit name -> demand group (checked in this order)
GROUP_WORDS = [
    (G_BOILER, ("boiler", "acumulação", "acumulacao")),
    (G_HEATER, ("chuveiro", "ducha", "torneira", "aquecedor", "ferro")),
    (G_STOVE, ("fogão", "fogao", "cooktop")),
    (G_APPLIANCE, ("secadora", "forno", "lava-louça", "lava louça", "lava-louca", "lava louca", "micro")),
    (G_AIR, ("ar-condicionado", "ar condicionado", "split", "condicionador", "climatizador")),
    (G_PUMP, ("bomba", "hidro", "piscina")),
    (G_EV, ("veículo", "veiculo", "carregador", "wallbox", "recarga")),
]


def demand_group(kind, *names):
    """Demand group of an item from its circuit kind and names (item label, circuit description)."""
    if kind in ("Iluminação", "TUG"):
        return G_LIGHT
    if kind == "Motor":
        return G_MOTOR
    text = " ".join(n for n in names if n).lower()
    for group, words in GROUP_WORDS:
        if any(w in text for w in words):
            return group
    return G_OTHER


def _by_steps(table, value, above):
    for limit, factor in table:
        if value <= limit + 1e-9:
            return factor
    return above


LIGHT_FACTORS = [(1, 0.86), (2, 0.75), (3, 0.66), (4, 0.59), (5, 0.52), (6, 0.45), (7, 0.40), (8, 0.35),
                 (9, 0.31), (10, 0.27)]  # table 6, by installed kW; above 10 kW: 0.24
HEATER_FACTORS = [1.00, 1.00, 0.84, 0.76, 0.70, 0.65, 0.60, 0.57, 0.54, 0.52, 0.49, 0.48, 0.46, 0.45, 0.44,
                  0.43, 0.42, 0.41, 0.40, 0.40, 0.39, 0.39, 0.39, 0.38, 0.38]  # table 7, by count; above: 0.38
BOILER_FACTORS = [(1, 1.00), (2, 0.72)]  # table 8; above: 0.62
APPLIANCE_FACTORS = [(1, 1.00), (4, 0.70), (6, 0.60)]  # table 9; above: 0.50
STOVE_FACTORS = [(1, 1.00), (2, 0.60), (3, 0.48), (4, 0.40), (5, 0.37), (6, 0.35), (7, 0.33), (8, 0.32),
                 (9, 0.31), (11, 0.30), (15, 0.28)]  # table 10; above: 0.26
AIR_FACTORS = [(10, 1.00), (20, 0.86), (30, 0.80), (40, 0.78), (50, 0.75), (75, 0.70), (100, 0.65)]  # table 12
EV_FACTORS = [(10, 1.00), (20, 0.90), (30, 0.82), (40, 0.80), (50, 0.77)]  # table 13; above: 0.75
PUMP_FACTORS = [(1, 1.00), (2, 0.56), (3, 0.47)]  # table 16; above: 0.39


def group_factor(group, count, installed_kw=0.0):
    """Demand factor of a group of `count` appliances (lighting and sockets: by installed kW)."""
    if group == G_LIGHT:
        return _by_steps(LIGHT_FACTORS, installed_kw, 0.24)
    if group == G_HEATER:
        return HEATER_FACTORS[count - 1] if 0 < count <= len(HEATER_FACTORS) else 0.38
    if group == G_BOILER:
        return _by_steps(BOILER_FACTORS, count, 0.62)
    if group == G_APPLIANCE:
        return _by_steps(APPLIANCE_FACTORS, count, 0.50)
    if group == G_STOVE:
        return _by_steps(STOVE_FACTORS, count, 0.26)
    if group == G_AIR:
        return _by_steps(AIR_FACTORS, count, 0.60)
    if group == G_EV:
        return _by_steps(EV_FACTORS, count, 0.75)
    if group == G_PUMP:
        return _by_steps(PUMP_FACTORS, count, 0.39)
    return 1.0


def demand(items):
    """Demand (kVA) of an installation. `items` is [(group, count, kw, kva)]. Groups at power
    factor 1 (kW taken as kVA) get their table factor; motors and special equipment are taken at
    their kVA, the biggest at 100 % and the others at 50 % (motors) or 60 % (special equipment).
    Returns {"groups": [...], "kva": total}."""
    groups = {}
    for group, count, kw, kva in items:
        g = groups.setdefault(group, {"count": 0, "kw": 0.0, "kva": 0.0, "units": []})
        g["count"] += count
        g["kw"] += kw
        g["kva"] += kva
        g["units"] += [kva / count] * count if count else []
    out, total = [], 0.0
    for group in DEMAND_GROUPS:
        g = groups.get(group)
        if not g:
            continue
        if group in (G_MOTOR, G_SPECIAL):
            units = sorted(g["units"], reverse=True)
            rest = 0.50 if group == G_MOTOR else 0.60
            value = (units[0] + rest * sum(units[1:])) if units else 0.0
            factor = value / g["kva"] if g["kva"] else 1.0
        else:
            factor = group_factor(group, g["count"], g["kw"])
            value = g["kw"] * factor
        total += value
        out.append({"group": group, "count": g["count"], "installed_kw": round(g["kw"], 2),
                    "factor": round(factor, 3), "demand_kva": round(value, 2)})
    return {"groups": out, "kva": round(total, 2)}


# Service entrance categories per system: (code, phases, installed kW up to, demand kVA up to or
# None, breaker A, biggest motor cv for 1F/2F/3F motors). Single-phase and two-phase categories
# go by installed load; three-phase ones by demand, with installed load up to 75 kW.
CATEGORIES = {
    "220/127 V": [("M0", 1, 5, None, 40, (1, 0, 0)), ("M1", 1, 10, None, 50, (2, 0, 0)),
                  ("B1", 2, 18, None, 50, (2, 2, 0)),
                  ("T0", 3, 75, 18, 50, (1, 2, 5)), ("T1", 3, 75, 25, 63, (2, 5, 20)),
                  ("T2", 3, 75, 38, 100, (3, 7.5, 25)), ("T3", 3, 75, 47.6, 125, (7.5, 10, 30)),
                  ("T4", 3, 75, 75, 200, (7.5, 10, 50))],
    "380/220 V": [("M2", 1, 8, None, 40, (3, 0, 0)), ("M3", 1, 15, None, 50, (3, 0, 0)),
                  ("T5", 3, 75, 32, 50, (3, 5, 20)), ("T6", 3, 75, 42, 63, (5, 10, 30)),
                  ("T7", 3, 75, 52.7, 80, (7.5, 12, 30)), ("T8", 3, 75, 75, 125, (7.5, 12, 50))],
}


def service_category(system, installed_kw, demand_kva, phases_needed=1, motors=()):
    """Service entrance category (Neoenergia DIS-NOR-030, tables 1 and 2). `phases_needed` is the
    most phases any circuit uses; `motors` is [(cv, phases)]."""
    for code, phases, max_kw, max_kva, breaker, motor_cv in CATEGORIES.get(system, CATEGORIES[DEFAULT_SYSTEM]):
        if phases < phases_needed or installed_kw > max_kw + 1e-9:
            continue
        if max_kva is not None and demand_kva > max_kva + 1e-9:
            continue
        if any(ph > phases or cv > motor_cv[min(max(int(ph), 1), 3) - 1] + 1e-9 for cv, ph in motors):
            continue
        return {"category": code, "phases": phases, "breaker_a": breaker,
                "basis": "demanda" if max_kva is not None else "carga instalada"}
    return {"category": None, "phases": 3, "breaker_a": None,
            "basis": "acima dos limites de baixa tensão (75 kW / 75 kVA): consultar a distribuidora"}


# --- conduit size (NBR 5410 6.2.11.1.6: fill up to 53 % with one cable, 31 % with two, 40 % with three or more)

CABLE_OD = {1.5: 2.9, 2.5: 3.5, 4: 4.1, 6: 4.7, 10: 5.9, 16: 7.0, 25: 8.8, 35: 10.1, 50: 11.9, 70: 14.0,
            95: 16.0, 120: 17.9}  # outer diameter (mm) of 750 V PVC copper cables, typical catalogue values
CONDUITS = [(20, 16.4), (25, 21.3), (32, 27.5), (40, 36.1), (50, 41.4), (60, 52.8), (75, 67.1),
            (85, 79.6)]  # rigid PVC conduit (NBR 15465): nominal size -> inner diameter (mm)


def suggest_conduit(sections):
    """Smallest nominal conduit size for cables of these sections (mm²)."""
    if not sections:
        return CONDUITS[0][0]
    area = sum(math.pi * (CABLE_OD.get(s, CABLE_OD[max(k for k in CABLE_OD if k <= s)] if s >= 1.5 else 2.9) / 2) ** 2
               for s in sections)
    fill = 0.53 if len(sections) == 1 else 0.31 if len(sections) == 2 else 0.40
    for size, inner in CONDUITS:
        if area <= fill * math.pi * (inner / 2) ** 2:
            return size
    return CONDUITS[-1][0]
