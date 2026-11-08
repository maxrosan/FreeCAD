# SPDX-License-Identifier: LGPL-2.1-or-later

"""Electrical installations for building plans: NBR 5444 symbols that carry their data
(power, circuit, room...), circuits on 380/220 V systems, load schedule, NBR 5410 checks,
and sheets that stay up to date.

Main entry points (also used by the MCP module):
  model.make_point, model.make_circuit, model.make_conduit, model.set_point,
  model.load_summary, model.rebalance_phases, checks.check, sheets.make_sheet.
"""
