"""Lookup-table parsing, template emission and table ordering."""

import json
import numpy as np
from .constants import (
    TARGET_TEMP,
    TARGET_VDD,
)

_TABLE_ORDER = {
    'cell_rise': 0, 'rise_transition': 1,
    'cell_fall': 2, 'fall_transition': 3,
    'rise_constraint': 0, 'fall_constraint': 1,
    'rise_power': 0, 'fall_power': 1,
}


def _tmpl_kw(name):
    """Liberty group keyword for a template: power templates must be
    power_lut_template, or OpenSTA drops every table that uses them."""
    if name.startswith(("power_", "passive_power_")):
        return "power_lut_template"
    return "lu_table_template"


def generate_template_string(
    name, index_1_var, index_2_var, idx1_vals, idx2_vals
):
    """Generates a 2D lu_table_template or power_lut_template"""
    i1_str = ", ".join([f"{x:.6g}" for x in idx1_vals])
    i2_str = ", ".join([f"{x:.6g}" for x in idx2_vals])
    return (f'  {_tmpl_kw(name)} ({name}) {{\n'
            f'    variable_1 : {index_1_var};\n'
            f'    variable_2 : {index_2_var};\n'
            f'    index_1 ("{i1_str}");\n'
            f'    index_2 ("{i2_str}");\n'
            f'  }}')


def generate_template_string_1d(name, index_1_var, idx1_vals):
    """Generates a 1D lu_table_template or power_lut_template"""
    i1_str = ", ".join([f"{x:.6g}" for x in idx1_vals])
    return (f'  {_tmpl_kw(name)} ({name}) {{\n'
            f'    variable_1 : {index_1_var};\n'
            f'    index_1 ("{i1_str}");\n'
            f'  }}')


def process_table_data(data_dict):
    """
    TRANSPOSED MATRIX GENERATION
    Outer Loop = Slews (Rows)
    Inner Loop = Loads (Columns)
    """
    loads = sorted([float(k) for k in data_dict.keys()])
    # Slew axis = UNION of slews across every load,
    # so a ragged/sparse table
    # (some loads missing some slews, or empty {})
    # does not raise StopIteration.
    slew_set = set()
    for key in data_dict:
        slew_set.update(float(s) for s in data_dict[key].keys())
    slews = sorted(slew_set)

    def _match(keys, target):
        for k in keys:
            if abs(float(k) - target) < 1e-9:
                return k
        return None

    matrix = []
    for slew in slews:
        row_values = []
        for load in loads:
            load_key = _match(data_dict.keys(), load)
            slew_dict = (
                data_dict.get(load_key, {})
                if load_key is not None
                else {}
            )
            slew_key = _match(slew_dict.keys(), slew)
            val = slew_dict.get(slew_key) if slew_key is not None else None
            if val is None:
                raise ValueError(
                    f"incomplete table: missing point "
                    f"load={load} slew={slew}"
                )
            val_float = float(val)
            if np.isnan(val_float):
                raise ValueError(
                    f"incomplete table: NaN at load={load} slew={slew}"
                )
            row_values.append(val_float)
        matrix.append(row_values)
    return slews, loads, matrix


def parse_mpw_corner(t_corner):
    """Extract slews + values from MPW corner dict."""
    load_keys = list(t_corner.keys())
    if not load_keys:
        return None, None
    data_slew_dict = t_corner[load_keys[0]]
    slews = sorted([float(k) for k in data_slew_dict.keys()])
    values = []
    for slew_val in slews:
        key = next(
            k for k in data_slew_dict if abs(float(k) - slew_val) < 1e-7
        )
        val = data_slew_dict[key]
        values.append(
            float(val)
            if val is not None and not np.isnan(float(val))
            else 0.0
        )
    return slews, values


def parse_constraint_corner(t_corner):
    """Extract data_slews, clk_slews, values from constraint corner dict."""
    matrix_data = t_corner[list(t_corner.keys())[0]]
    return process_table_data(matrix_data)


def _load_transition_values(trans_file, slews, loads):
    """Load transition table; None if absent or incomplete."""
    if not trans_file.exists():
        return None
    with open(trans_file, encoding='utf-8') as ftrans:
        tr_data = json.load(ftrans)
    try:
        _, _, tr_values = process_table_data(
            tr_data[TARGET_TEMP][TARGET_VDD]
        )
    except (KeyError, ValueError, TypeError) as exc:
        print(f"  Warning: transition table unusable "
              f"({trans_file.name}): {exc}")
        return None
    if len(tr_values) != len(slews) or any(
            len(r) != len(loads) for r in tr_values):
        print(f"  Warning: transition table shape mismatch "
              f"({trans_file.name})")
        return None
    return tr_values


def load_delay_tables(ctx):
    """Load delay + transition tables.
    Returns (slews, loads, t_values, tr_values) or None."""
    timing_file = ctx.res_path / f"{ctx.arc_name}_delay_table.json"
    trans_file = ctx.res_path / f"{ctx.arc_name}_transition_table.json"
    if not timing_file.exists():
        print(f"  Warning: No results for {ctx.arc_name}, skipping.")
        return None
    with open(timing_file, encoding='utf-8') as ftiming:
        t_data = json.load(ftiming)
    try:
        t_corner = t_data[TARGET_TEMP][TARGET_VDD]
        slews, loads, t_values = process_table_data(t_corner)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"  Error parsing delay data for {ctx.arc_name}: {exc}")
        return None
    tr_values = _load_transition_values(trans_file, slews, loads)
    if tr_values is None:
        print(f"  Warning: dropping arc {ctx.arc_name}: "
              f"no usable transition table.")
        return None
    return slews, loads, t_values, tr_values


def sort_tables(grp):
    """Reference table order inside a timing()/internal_power() group:
    cell_rise, rise_transition, cell_fall, fall_transition (always
    rise-first regardless of unateness); rise_constraint before
    fall_constraint."""
    grp.tables.sort(
        key=lambda t: _TABLE_ORDER.get(getattr(t, 'name', ''), 99)
    )
