"""Timing arc processing: delay, constraint, mpw and input capacitance."""

import json
import math
from ..liberty_models import (
    TimingGroup,
    LookupTable,
    LookupTable1D,
)

from .context import ArcContext
from .constants import (
    TARGET_TEMP,
    TARGET_VDD,
)
from .tables import (
    load_delay_tables,
    parse_constraint_corner,
    parse_mpw_corner,
    sort_tables,
    generate_template_string,
    generate_template_string_1d,
)

try:  # optional: timing-only builds omit this module
    from .power import (
        process_internal_power,
        process_leakage,
        process_passive_power,
    )
    HAS_POWER = True
except ImportError:  # pragma: no cover
    HAS_POWER = False


def is_active_low(pin_name):
    """Active-low convention: trailing _N / _B (GATE_N, TE_B, ...)."""
    if not pin_name:
        return False
    return pin_name.upper().endswith(('_N', '_B'))


def _is_inverted_output(out_pin):
    """Q_N / QN style inverted outputs."""
    if not out_pin:
        return False
    return out_pin.upper() in ('Q_N', 'QN') or out_pin.upper().endswith('_N')


def _async_timing_type(related_pin, out_pin):
    """timing_type/sense for an async clear or preset arc, or None.

    RESET forces Q low (clear) but drives Q_N high, which is a preset on
    the inverted output; SET is the mirror image.
    """
    related_upper = related_pin.upper()
    inverted = _is_inverted_output(out_pin)
    if 'RESET' in related_upper or 'CLR' in related_upper:
        if inverted:
            return "preset", "negative_unate"
        return "clear", "positive_unate"
    if 'SET' in related_upper or 'PRE' in related_upper:
        if inverted:
            return "clear", "positive_unate"
        return "preset", "negative_unate"
    return None


def get_timing_type_and_sense(arc, in_pin, clock_pins, out_pin):
    """Determine Liberty timing_type and
     timing_sense from arc configuration."""
    arc_type = arc.get('type')
    data_trans = arc.get('data_transition')
    out_trans = arc.get('expected_output_transition')
    related_pin = arc.get('related_pin')
    timing_sense_cfg = arc.get('timing_sense')

    timing_type = "combinational"
    timing_sense = timing_sense_cfg if timing_sense_cfg else "positive_unate"

    # Explicit Liberty timing_type from config (e.g.
    # three_state_enable/disable, three_state_enable_rise, ...). Takes
    # precedence; sense from config or default.
    if 'timing_type' in arc:
        return (arc['timing_type'] or None), timing_sense

    if in_pin in clock_pins and arc_type == 'propagation_delay':
        # Active-low gates (GATE_N latches) launch on the falling edge;
        # reference: dllr/dllrq GATE_N->Q/Q_N arcs are falling_edge.
        edge = "falling_edge" if is_active_low(in_pin) else "rising_edge"
        return edge, "non_unate"

    if arc_type == 'propagation_delay' and related_pin:
        async_result = _async_timing_type(related_pin, out_pin)
        if async_result:
            return async_result

    if (arc_type == 'propagation_delay'
            and not timing_sense_cfg and data_trans and out_trans):
        timing_sense = (
            "positive_unate" if data_trans == out_trans else "negative_unate"
        )

    return timing_type, timing_sense


def get_constraint_timing_type(arc):
    """Determine Liberty timing_type for constraint arcs."""
    arc_type = arc.get('type')
    arc_name = arc.get('name', '')

    if 'non_seq' in arc_name:
        return {
            "setup": "non_seq_setup_rising",
            "hold": "non_seq_hold_rising"
        }.get(arc_type, f"{arc_type}_rising")

    # Latch constraints are referenced to the CLOSING edge of the gate:
    # active-high GATE closes on its falling edge -> setup/hold_falling;
    # active-low GATE_N closes on its rising edge -> setup/hold_rising
    # (reference: dlhq/dlhr *_falling, dllr/dllrq *_rising).
    gate_pin = arc.get('gate_signal') or arc.get('related_pin') or ''
    latch_edge = "rising" if is_active_low(gate_pin) else "falling"

    type_map = {
        "recovery": "recovery_rising",
        "removal": "removal_rising",
        "setup": "setup_rising",
        "hold": "hold_rising",
        "setup_latch": f"setup_{latch_edge}",
        "hold_latch": f"hold_{latch_edge}",
    }
    return type_map.get(arc_type, f"{arc_type}_rising")


def _when_to_sdf_cond(when):
    """Derive sdf_cond from a when condition: '!A1&!A2' ->
    "(A1 == 1'b0 && A2 == 1'b0)"; single literal '!B1' -> "B1 == 1'b0"
    (reference style: multi-term parenthesized, single-term bare).
    Only handles pure conjunctions of literals; anything else returns
    None so no sdf_cond is emitted."""
    if not when:
        return None
    expr = when.replace(' ', '').replace('(', '').replace(')', '')
    expr = expr.replace('*', '&')
    if any(op in expr for op in ('+', '|', '^')):
        return None
    terms = []
    for lit in expr.split('&'):
        if not lit:
            return None
        if lit.startswith('!'):
            terms.append(f"{lit[1:]} == 1'b0")
        else:
            terms.append(f"{lit} == 1'b1")
    joined = ' && '.join(terms)
    return f'({joined})' if len(terms) > 1 else joined


def _build_delay_tg(ctx, direction):
    """Build TimingGroup for a delay arc; return (tg, tbl_delay, tbl_trans)."""
    timing_type, timing_sense = get_timing_type_and_sense(
        ctx.arc, ctx.in_pin, ctx.clock_pins, ctx.out_pin
    )
    tbl_delay = "cell_rise" if direction == "rise" else "cell_fall"
    tbl_trans = "rise_transition" if direction == "rise" else "fall_transition"
    timing_grp = TimingGroup(ctx.in_pin, timing_type, timing_sense)
    # Reference: clock-edge, async clear/preset, and three_state
    # enable/disable delay arcs carry sdf_edges : start_edge.
    if timing_type and (
            timing_type in ('rising_edge', 'falling_edge', 'clear', 'preset')
            or timing_type.startswith('three_state')):
        timing_grp.sdf_edges = 'start_edge'
    when_cond = (
        ctx.arc.get('when')
        or ("SCE" if '_sce' in ctx.arc_name.lower() else None)
    )
    if when_cond:
        timing_grp.when_condition = when_cond
    sdf_cond = ctx.arc.get('sdf_cond') or _when_to_sdf_cond(when_cond)
    if sdf_cond:
        timing_grp.sdf_cond = sdf_cond
    return timing_grp, tbl_delay, tbl_trans


def _finite(val):
    """True for a real, finite number."""
    return isinstance(val, (int, float)) and math.isfinite(val)


def _cap_range(rng):
    """(min, max) from a cap JSON range, or None if absent/invalid."""
    if (isinstance(rng, (list, tuple)) and len(rng) == 2
            and all(_finite(v) for v in rng)):
        return (float(rng[0]), float(rng[1]))
    return None


def _process_input_capacitance(ctx: ArcContext):
    """Handles arc_type == 'input_capacitance'"""
    cap_file = ctx.res_path / f"{ctx.arc_name}_capacitance.json"
    if not cap_file.exists():
        print(f"  Warning: No capacitance file for {ctx.arc_name}")
        return
    with open(cap_file, encoding='utf-8') as fcap:
        cap_data = json.load(fcap)
    try:
        cap_corner = cap_data[TARGET_TEMP][TARGET_VDD]
        cap_val = cap_corner.get('capacitance', 0.001)
        rise_cap = cap_corner.get('rise_capacitance', cap_val)
        fall_cap = cap_corner.get('fall_capacitance', cap_val)
        pin_name = ctx.arc.get('data_signal', 'A')
        if not all(_finite(v) for v in (cap_val, rise_cap, fall_cap)):
            print(f"  Warning: non-finite capacitance for {ctx.arc_name}, "
                  "pin left to the config declaration")
            return
        pin_obj = ctx.lib_cell.get_or_create_pin(pin_name, "input")
        pin_obj.capacitance = cap_val
        pin_obj.rise_capacitance = rise_cap
        pin_obj.fall_capacitance = fall_cap
        # min/max over the cap_ramp_ns sweep (Liberate convention); OpenSTA
        # uses the upper value for switching power
        pin_obj.rise_capacitance_range = _cap_range(
            cap_corner.get('rise_capacitance_range'))
        pin_obj.fall_capacitance_range = _cap_range(
            cap_corner.get('fall_capacitance_range'))
        print(f"  Added capacitance for pin {pin_name}: {cap_val:.6f} pF "
              f"(rise={rise_cap:.6f}, fall={fall_cap:.6f})")
    except Exception as exc:
        print(
            "  Warning: Error processing capacitance for "
            f"{ctx.arc_name}: {exc}"
        )


def _process_mpw(ctx: ArcContext):
    """Handles arc_type == 'mpw'"""
    timing_file = ctx.res_path / f"{ctx.arc_name}_mpw_table.json"
    if not timing_file.exists():
        timing_file = ctx.res_path / f"{ctx.arc_name}_timing_table.json"
    if not timing_file.exists():
        print(f"  Warning: No results for {ctx.arc_name}, skipping.")
        return
    with open(timing_file, encoding='utf-8') as ftiming:
        t_data = json.load(ftiming)
    try:
        t_corner = t_data[TARGET_TEMP][TARGET_VDD]
        slews, values = parse_mpw_corner(t_corner)
        if slews is None:
            return
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  Error parsing MPW data for {ctx.arc_name}: {exc}")
        return

    tmpl_name = f"mpw_CONSTRAINT_{len(slews)}x{len(slews)}"
    ctx.lib_cell.add_template(
        generate_template_string_1d(
            tmpl_name, "constrained_pin_transition", slews
        )
    )
    timing_grp = TimingGroup(
        ctx.arc.get('related_pin') or ctx.in_pin, "min_pulse_width", ""
    )
    # Add this:
    when_cond = ctx.arc.get('when')
    if when_cond:
        timing_grp.when_condition = when_cond
    pol = ctx.arc.get('pulse_polarity', 'high').lower()
    tbl_type = (
        "rise_constraint" if "high" in pol else "fall_constraint"
    )
    timing_grp.add_table(
        LookupTable1D(tbl_type, tmpl_name, slews, values)
    )
    ctx.lib_cell.get_or_create_pin(
        ctx.in_pin, "input").add_timing_group(timing_grp)


def _process_constraint(ctx: ArcContext):
    """Handles setup / hold / setup_latch
    / hold_latch / recovery / removal arcs"""
    timing_file = ctx.res_path / f"{ctx.arc_name}_timing_table.json"
    if not timing_file.exists():
        print(f"  Warning: No results for {ctx.arc_name}, skipping.")
        return
    with open(timing_file, encoding='utf-8') as ftiming:
        t_data = json.load(ftiming)
    try:
        t_corner = t_data[TARGET_TEMP][TARGET_VDD]
        data_slews, clk_slews, values = parse_constraint_corner(t_corner)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  Error parsing constraint data for {ctx.arc_name}: {exc}")
        return

    tmpl_name = f"CONSTRAINT_{len(data_slews)}x{len(clk_slews)}"
    ctx.lib_cell.add_template(generate_template_string(
        tmpl_name, "constrained_pin_transition", "related_pin_transition",
        data_slews, clk_slews
    ))
    timing_type = get_constraint_timing_type(ctx.arc)
    timing_grp = TimingGroup(ctx.clk_pin, timing_type, "")
    # Reference: all setup/hold/recovery/removal arcs carry
    # sdf_edges : both_edges.
    timing_grp.sdf_edges = 'both_edges'
    when_cond = ctx.arc.get('when')
    if when_cond:
        timing_grp.when_condition = when_cond
    data_trans = ctx.arc.get('data_transition', 'rise')
    table_type = (
        "rise_constraint" if data_trans == 'rise' else "fall_constraint"
    )
    timing_grp.add_table(LookupTable(
        table_type, tmpl_name, (data_slews, clk_slews), values
    ))
    ctx.lib_cell.get_or_create_pin(
        ctx.in_pin, "input"
    ).add_timing_group(timing_grp)


def _process_delay(ctx: ArcContext):
    """Handles arc_type == 'propagation_delay'"""
    result = load_delay_tables(ctx)
    if result is None:
        return
    slews, loads, t_values, tr_values = result
    tmpl_name = f"TIMING_DELAY_{len(slews)}x{len(loads)}ds1"
    ctx.lib_cell.add_template(generate_template_string(
        tmpl_name, "input_net_transition",
        "total_output_net_capacitance", slews, loads
    ))
    direction = ctx.arc.get('expected_output_transition', 'rise')
    timing_grp, tbl_delay, tbl_trans = _build_delay_tg(ctx, direction)
    timing_grp.add_table(
        LookupTable(tbl_delay, tmpl_name, (slews, loads), t_values)
    )
    timing_grp.add_table(
        LookupTable(tbl_trans, tmpl_name, (slews, loads), tr_values)
    )
    ctx.lib_cell.get_or_create_pin(
        ctx.out_pin, "output"
    ).add_timing_group(timing_grp)


def process_arc(arc, lib_cell, res_path, clock_pins):
    """Build ArcContext and dispatch to the correct handler."""
    ctx = ArcContext(
        arc=arc,
        arc_name=arc['name'],
        in_pin=arc.get('data_signal'),
        out_pin=arc.get('output_signal'),
        clk_pin=(
            arc.get('clock_signal') or arc.get('gate_signal')
            or arc.get('related_pin')
        ),
        lib_cell=lib_cell,
        res_path=res_path,
        clock_pins=clock_pins,
    )
    arc_type = arc['type']
    if arc_type == 'input_capacitance':
        _process_input_capacitance(ctx)
    elif arc_type in (
        'leakage_power',
        'internal_power',
        'hidden_power_internal',
    ):
        if not HAS_POWER:
            print(f"  Warning: skipping {arc_type} arc "
                  f"(power emission not installed).")
            return
        if arc_type == 'leakage_power':
            process_leakage(ctx)
        elif arc_type == 'internal_power':
            process_internal_power(ctx)
        else:
            process_passive_power(ctx)
    elif arc_type == 'mpw':
        _process_mpw(ctx)
    elif arc_type in (
        'setup',
        'hold',
        'setup_latch',
        'hold_latch',
        'recovery',
        'removal'
    ):
        _process_constraint(ctx)
    elif arc_type == 'propagation_delay':
        _process_delay(ctx)


def merge_delay_groups(pin_obj):
    """Merge delay timing() groups on an output pin that share the same
    (related_pin, timing_type, timing_sense, when, sdf_cond) into one group
    holding both cell_rise and cell_fall. Tulip runs rise/fall as separate
    sims -> two groups; the reference pairs them (A->Z combinational and
    three_state_enable/disable each carry cell_rise+cell_fall together)."""
    merged, order, result = {}, [], []
    for grp in pin_obj.timing_groups:
        key = (grp.related_pin, grp.timing_type, grp.timing_sense,
               grp.when_condition, grp.sdf_cond)
        if key not in merged:
            merged[key] = grp
            order.append(key)
        else:
            existing = {getattr(t, 'name', None) for t in merged[key].tables}
            for tbl in grp.tables:
                if getattr(tbl, 'name', None) not in existing:
                    merged[key].add_table(tbl)
    for key in order:
        sort_tables(merged[key])
        result.append(merged[key])
    pin_obj.timing_groups = result
