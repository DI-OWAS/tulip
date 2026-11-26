"""Power group emission: leakage, passive and switching energy.

Optional: timing-only builds may omit this module; the arc and
cell processors guard the import."""

import json
import numpy as np
from ..liberty_models import (
    LookupTable,
    LookupTable1D,
    InternalPowerGroup,
    LeakagePowerGroup,
)
from .constants import (
    TARGET_TEMP,
    TARGET_VDD,
)
from .context import ArcContext
from .tables import (
    generate_template_string,
    generate_template_string_1d,
    process_table_data,
)


class _ScalarPowerTable:
    """Minimal scalar power table emitter, for missing rise/fall on async
    arcs."""

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return (f'{self.name} (scalar) {{\n'
                f'values ("0");\n'
                f'}}')


def process_passive_power_data(data_dict):
    """
    Process passive power data (1D: slew -> value)
    Data format: {"rise_power": {slew: val}, "fall_power": {slew: val}}
    """
    rise_data = data_dict.get('rise_power', {})
    fall_data = data_dict.get('fall_power', {})

    if rise_data:
        slews = sorted([float(k) for k in rise_data.keys()])
    elif fall_data:
        slews = sorted([float(k) for k in fall_data.keys()])
    else:
        return [], [], []

    rise_values = []
    fall_values = []

    for slew in slews:
        rise_key = next(
            (k for k in rise_data.keys() if abs(float(k) - slew) < 1e-9), None
        )
        fall_key = next(
            (k for k in fall_data.keys() if abs(float(k) - slew) < 1e-9), None
        )
        rise_val = rise_data.get(rise_key, 0.0) if rise_key else 0.0
        fall_val = fall_data.get(fall_key, 0.0) if fall_key else 0.0
        try:
            rise_val = (
                float(rise_val)
                if rise_val is not None and not np.isnan(float(rise_val))
                else 0.0
            )
        except (ValueError, TypeError):  # pylint: disable=bare-except
            rise_val = 0.0
        try:
            fall_val = (
                float(fall_val)
                if fall_val is not None and not np.isnan(float(fall_val))
                else 0.0
            )
        except (ValueError, TypeError):  # pylint: disable=bare-except
            fall_val = 0.0
        rise_values.append(rise_val)
        fall_values.append(fall_val)

    return slews, rise_values, fall_values


def _get_power_tbl_name(arc, arc_name):
    """Determine rise_power vs fall_power table name."""
    out_trans = arc.get('expected_output_transition', '')
    if out_trans == 'rise':
        return "rise_power"
    if out_trans == 'fall':
        return "fall_power"
    return "rise_power" if 'rise' in arc_name.lower() else "fall_power"


def _get_power_when_cond(arc, arc_name):
    """Resolve when condition for dynamic power arc."""
    when_cond = arc.get('when')
    if not when_cond:
        static = arc.get('static_pins', {})
        if (
            ('_sce' in arc_name.lower() or 'SCE' in str(static))
            and static.get('SCE') == 'VDD'
        ):
            when_cond = "SCE"
    return when_cond


def process_leakage(ctx: ArcContext):
    """Handles arc_type == 'leakage_power'"""
    leak_file = ctx.res_path / f"{ctx.arc_name}_leakage_power_table.json"
    if not leak_file.exists():
        print(f"  Warning: No leakage file for {ctx.arc_name}")
        return
    with open(leak_file, encoding='utf-8') as fleak:
        l_data = json.load(fleak)
    try:
        l_corner = l_data[TARGET_TEMP][TARGET_VDD]
        config_states = ctx.arc.get('states', [])
        for state_key, value in l_corner.items():
            idx = int(state_key.split('_')[1])
            cond = (
                config_states[idx]['when']
                if idx < len(config_states)
                else None
            )
            # JSON stores p_leak in microwatts; .lib unit is 1pW. Convert.
            value_pw = float(value) * 1e6
            ctx.lib_cell.add_leakage_group(LeakagePowerGroup(value_pw, cond))
        print(f"  Added {len(l_corner)} leakage power states")
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  Warning: Error processing leakage for {ctx.arc_name}: {exc}")


def process_passive_power(ctx: ArcContext):
    """Handles internal_power with 'when' condition (passive power, 1D)"""
    when_condition = ctx.arc.get('when')
    pwr_file = ctx.res_path / f"{ctx.arc_name}_passive_power.json"
    if not pwr_file.exists():
        print(
            "  Warning: No passive power results for "
            f"{ctx.arc_name}, skipping."
        )
        return
    with open(pwr_file, encoding='utf-8') as fpwr:
        p_data = json.load(fpwr)
    try:
        p_corner = p_data[TARGET_TEMP][TARGET_VDD]
    except KeyError:
        print(f"  Warning: No data for corner in {ctx.arc_name}")
        return

    slews, rise_values, fall_values = process_passive_power_data(p_corner)
    target_pin = ctx.in_pin if ctx.in_pin else (ctx.arc.get('related_pin'))
    tmpl_name = f"passive_power_{len(slews)}"
    ctx.lib_cell.add_template(
        generate_template_string_1d(tmpl_name, "input_transition_time", slews)
    )

    pwr_group = InternalPowerGroup(None)
    pwr_group.when_condition = when_condition
    if p_corner.get("rise_power"):
        pwr_group.add_table(
            LookupTable1D("rise_power", tmpl_name, slews, rise_values)
        )
    if p_corner.get("fall_power"):
        pwr_group.add_table(
            LookupTable1D("fall_power", tmpl_name, slews, fall_values)
        )
    pin_obj = ctx.lib_cell.get_or_create_pin(target_pin, "input")
    pin_obj.add_power_group(pwr_group)
    print(f"  Added passive power for {ctx.arc_name}")


def _build_passive_dynamic_pg(ctx, slews, _, values_2d):
    """Build passive (1D averaged)
    InternalPowerGroup. Returns (pg, target_pin)."""
    related = ctx.arc.get('related_pin') or ctx.in_pin
    tbl_name = _get_power_tbl_name(ctx.arc, ctx.arc_name)
    pwr_grp = InternalPowerGroup(related)
    when_cond = _get_power_when_cond(ctx.arc, ctx.arc_name)
    if when_cond:
        pwr_grp.when_condition = when_cond
    values_1d = [
        np.mean([row[i] for row in values_2d])
        for i in range(len(values_2d[0]))
    ]
    if len(values_1d) != len(slews):
        values_1d = values_2d[0]
    tmpl_name = f"passive_power_{len(slews)}"
    ctx.lib_cell.add_template(
        generate_template_string_1d(tmpl_name, "input_transition_time", slews)
    )
    pwr_grp.add_table(LookupTable1D(tbl_name, tmpl_name, slews, values_1d))
    target_pin = ctx.in_pin if ctx.in_pin else related
    return pwr_grp, target_pin


def _build_switching_pg(ctx, slews, loads, values_2d):
    """Build switching (2D) InternalPowerGroup. Returns pg."""
    related = ctx.arc.get('related_pin') or ctx.in_pin
    tbl_name = _get_power_tbl_name(ctx.arc, ctx.arc_name)
    pwr_grp = InternalPowerGroup(related)
    when_cond = _get_power_when_cond(ctx.arc, ctx.arc_name)
    if when_cond:
        pwr_grp.when_condition = when_cond
    tmpl_name = f"power_{len(slews)}x{len(loads)}"
    ctx.lib_cell.add_template(generate_template_string(
        tmpl_name, "input_transition_time",
        "total_output_net_capacitance", slews, loads
    ))
    pwr_grp.add_table(
        LookupTable(tbl_name, tmpl_name, (slews, loads), values_2d)
    )
    return pwr_grp


def _process_dynamic_power(ctx: ArcContext):
    """Handles internal_power without
    'when' condition (dynamic / switching, 2D)"""
    pwr_file = ctx.res_path / f"{ctx.arc_name}_dynamic_energy_table.json"
    if not pwr_file.exists():
        print(f"  Warning: No results for {ctx.arc_name}, skipping.")
        return
    with open(pwr_file, encoding='utf-8') as fpwr:
        p_data = json.load(fpwr)
    try:
        p_corner = p_data[TARGET_TEMP][TARGET_VDD]
    except KeyError:
        return

    try:
        slews, loads, values_2d = process_table_data(p_corner)
    except (ValueError, TypeError) as exc:
        print(f"  Warning: dropping power for {ctx.arc_name}: {exc}")
        return
    is_passive = (
        'passive' in ctx.arc_name.lower()
        or ctx.arc.get('power_loads_pf') is not None
    )

    if is_passive:
        pwr_grp, target_pin = _build_passive_dynamic_pg(
            ctx, slews, loads, values_2d
        )
        ctx.lib_cell.get_or_create_pin(
            target_pin, "input"
        ).add_power_group(pwr_grp)
    else:
        n_sw = ctx.arc.get('_switching_outputs', 1)
        if n_sw != 1:
            values_2d = [[v / n_sw for v in row] for row in values_2d]
            print(f"  {ctx.arc_name}: internal power / {n_sw} "
                  "(switching outputs)")
        pwr_grp = _build_switching_pg(ctx, slews, loads, values_2d)
        ctx.lib_cell.get_or_create_pin(
            ctx.out_pin, "output"
        ).add_power_group(pwr_grp)

        # Liberate convention: when an asynchronous async-pin (RESET_B,
        # CLR, SET_B) drives the output to a fixed level, the opposite
        # transition cannot happen — emit a scalar 0 for the missing
        # direction so the lib matches the reference structure.
        related_pin = (ctx.arc.get('related_pin') or '').upper()
        is_async = any(
            x in related_pin for x in ['RESET', 'CLR', 'SET', 'PRE', 'RST']
        )
        out_trans = ctx.arc.get('expected_output_transition', '')
        if is_async and out_trans in ('rise', 'fall'):
            opposite_tbl = (
                'fall_power' if out_trans == 'rise' else 'rise_power'
            )
            scalar_pg = InternalPowerGroup(
                ctx.arc.get('related_pin') or ctx.in_pin
            )
            scalar_pg.add_table(_ScalarPowerTable(opposite_tbl))
            ctx.lib_cell.get_or_create_pin(
                ctx.out_pin, "output"
            ).add_power_group(scalar_pg)


def process_internal_power(ctx: ArcContext):
    """Dispatches to passive or dynamic power handler.

    Discriminator is arc TYPE, not presence of `when`. Sequential hidden
    power (type 'hidden_power_internal') attaches under the INPUT pin.
    Combinational/active switching power (type 'internal_power') attaches
    under the OUTPUT pin with related_pin + when -- even when it carries a
    `when` state condition. Branching on `when` alone misrouted
    combinational when-conditioned arcs to the input pin.
    """
    if ctx.arc.get('type') == 'hidden_power_internal':
        process_passive_power(ctx)
    else:
        _process_dynamic_power(ctx)


def merge_active_power_groups(pin_obj):
    """Merge active internal_power groups on an output pin that share the
    same (related_pin, when_condition) into a single group holding both
    rise_power and fall_power. Tulip processes the rise and fall arcs as
    separate sims, producing two standalone internal_power() groups; the
    reference (Liberate) pairs them in one group. Without merging, the
    comparison tool sees an unmatched extra block (phantom CLK->Q rows).

    Only groups carrying a related_pin are merged (active arcs). Passive
    input-pin groups (related_pin is None) are left untouched.
    """
    merged = {}
    order = []
    result = []
    for grp in pin_obj.power_groups:
        related = getattr(grp, 'related_pin', None)
        if not related:
            result.append(grp)
            continue
        key = (related, getattr(grp, 'when_condition', None))
        if key not in merged:
            merged[key] = grp
            order.append(key)
        else:
            existing_names = {
                getattr(t, 'name', None) for t in merged[key].tables
            }
            for tbl in grp.tables:
                if getattr(tbl, 'name', None) not in existing_names:
                    merged[key].add_table(tbl)
    for key in order:
        result.append(merged[key])
    pin_obj.power_groups = result


def _load_passive(res_path, arc_name):
    """Corner data of one hidden arc's passive-power result, or None."""
    pwr_file = res_path / f"{arc_name}_passive_power.json"
    if not pwr_file.exists():
        return None
    with open(pwr_file, encoding='utf-8') as fpwr:
        p_data = json.load(fpwr)
    return p_data.get(TARGET_TEMP, {}).get(TARGET_VDD)


def emit_hidden_defaults(cell_conf, lib_cell, res_path):
    """Emit the unconditional (when "") hidden-power group of each input pin.

    The reference default group is a copy of one conditional group per
    direction (e.g. dfrbpq CLK: rise from D*RB*Q, fall from D*RB*!Q), so no
    extra simulation is needed. Config, per cell:
        "hidden_defaults": [{"pin": "CLK", "rise": "<hidden arc name>",
                             "fall": "<hidden arc name>"}]
    """
    for entry in cell_conf.get('hidden_defaults', []):
        pwr_group = InternalPowerGroup(None)
        slews_seen = None
        for direction in ('rise', 'fall'):
            src = entry.get(direction)
            corner = _load_passive(res_path, src) if src else None
            tbl = f"{direction}_power"
            if not corner or not corner.get(tbl):
                print(f"  Warning: default {entry['pin']} {tbl}: no data "
                      f"in {src}, skipped")
                continue
            slews, rise_v, fall_v = process_passive_power_data(corner)
            slews_seen = slews
            tmpl_name = f"passive_power_{len(slews)}"
            lib_cell.add_template(generate_template_string_1d(
                tmpl_name, "input_transition_time", slews))
            pwr_group.add_table(LookupTable1D(
                tbl, tmpl_name, slews,
                rise_v if direction == 'rise' else fall_v))
        if slews_seen is not None:
            lib_cell.get_or_create_pin(
                entry['pin'], "input").add_power_group(pwr_group)
            print(f"  Added default hidden power for {entry['pin']}")
