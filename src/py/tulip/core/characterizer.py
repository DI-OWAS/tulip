import os
import json
from pathlib import Path
import numpy as np


from .simulation import run_single_simulation
from .characterizer_utils import has_any_data
from .characterizer_timing import (
    characterize_combinational,
    characterize_mpw,
    characterize_sequential,
)
try:  # optional: timing-only builds omit this package
    from .characterizer_power import characterize_power
    HAS_POWER = True
except ImportError:  # pragma: no cover
    HAS_POWER = False


DEBUG_PLOT_DIR = Path('debug_plots')
CURVE_PLOT_DIR = Path('curve_plots')
RESULTS_DIR = Path('results')
NUM_PROCESSES = 8


_UNSET = object()  # sentinel: "key not configured"


def characterize_arc(
    cell_config,
    arc_config,
    spice_template,
    global_settings,
    cli_args
):
    """
    Characterize timing arc across all corners.

    Args:
        cell_config: Cell configuration dictionary
        arc_config: Arc configuration dictionary
        spice_template: SPICE netlist template
        global_settings: Global configuration settings
        cli_args: Command-line arguments
    """
    arc_name = arc_config['name']
    arc_type = arc_config.get('type')
    arc_config = dict(arc_config)
    if (
        "function_mapping" not in arc_config
        and "function_mapping" in cell_config
    ):
        arc_config["function_mapping"] = cell_config["function_mapping"]
    arc_config['_sim_cfg'] = global_settings
    arc_config['_cell_sim_window_ns'] = cell_config.get(
        'sim_window_ns', global_settings.get('sim_window_ns', 30)
    )
    arc_config['_cell_delay_window_ns'] = cell_config.get(
        'delay_window_ns', global_settings.get('delay_window_ns', 50)
    )
    arc_config['_scan_pins'] = cell_config.get(
        'scan_pins', ["SCE", "SCD"]
    )
    print(f"\n--- Characterizing Arc: {cell_config['name']} / "
          f"{arc_name} ({arc_type}) ---")

    # Resume: TULIP_SKIP_EXISTING=1 skips power arcs whose final result
    # file already exists (each saver writes this file last).
    if os.environ.get("TULIP_SKIP_EXISTING", "0") == "1":
        last = {"leakage_power": "_leakage_power_table.json",
                "internal_power": "_hidden_energy_table.json",
                "hidden_power_internal": "_passive_power.json"}.get(arc_type)
        if last and (Path("results") / cell_config["name"]
                     / f"{arc_name}{last}").is_file():
            print("  [SKIP] result exists")
            return None

    # Handle input capacitance early return
    if arc_type == "input_capacitance":
        return _characterize_input_capacitance(
            cell_config, arc_config, spice_template, global_settings, arc_name
        )

    # Determine arc category
    is_combinational = arc_type in ['propagation_delay', 'combinational_delay',
                                    'cell_rise', 'cell_fall']
    is_sequential = arc_type in ['setup', 'hold', 'setup_latch', 'hold_latch',
                                 'recovery', 'removal', 'mpw']

    # Build characterization context
    char_context = _build_char_context(
        arc_config, global_settings, cli_args, is_sequential
    )

    # Build sweep configuration
    sweep_config = {
        'cell_config': cell_config,
        'arc_config': arc_config,
        'arc_name': arc_name,
        'arc_type': arc_type,
        'spice_template': spice_template,
        'is_combinational': is_combinational,
        'is_sequential': is_sequential
    }

    # Initialize tables
    tables = _initialize_tables()

    # Run characterization loops
    _run_characterization_sweeps(sweep_config, char_context, tables)

    # Save and print results
    _save_and_print_results(sweep_config, tables, char_context)

    return None


def _characterize_input_capacitance(cell_config, arc_config, spice_template,
                                    global_settings, arc_name):
    """Handle input capacitance characterization."""
    print(f"\n{'='*60}")
    print(f"  Input Capacitance: {cell_config['name']}")
    print(f"{'='*60}")

    # Get configuration helper
    def get_cfg(key, default=None):
        if default is None:
            default = []
        val = arc_config.get(key)
        return val if val is not None else global_settings.get(key, default)

    # Run measurements
    cap_results = _measure_capacitance_corners(
        cell_config, arc_config, spice_template, get_cfg
    )

    # Save results
    _save_capacitance_results(cell_config, arc_name, cap_results)


def _measure_capacitance_corners(
    cell_config, arc_config, spice_template, get_cfg
):
    """Measure input capacitance across all corners."""
    temp_values = get_cfg("temp_values")
    vdd_values = get_cfg("vdd_values")
    input_pin = arc_config.get("data_signal", "A")
    cap_results = {}

    ramps = _cap_ramp_list(arc_config)
    for temp in temp_values:
        cap_results[temp] = {}
        for vdd in vdd_values:
            print(f"  T={temp}C, VDD={vdd}V, Pin={input_pin}")
            runs = []
            for ramp in ramps:
                run_cfg = (arc_config if ramp is None
                           else _with_cap_ramp(arc_config, ramp))
                result = run_single_simulation((
                    str(temp), vdd, "0.1n", "2n", "0.001", 0.0,
                    cell_config, run_cfg, spice_template, False
                ))
                runs.append({
                    'capacitance': result.get('capacitance', np.nan),
                    'rise_capacitance': result.get('rise_cap', np.nan),
                    'fall_capacitance': result.get('fall_cap', np.nan)
                })
            cap_results[temp][vdd] = _combine_cap_runs(runs, ramps)

    return cap_results


def _cap_ramp_list(arc_config):
    """Ramps to average over (Liberate-style mean over the slew grid).

    cap_ramp_ns = "slew_grid": data_slew_ns x slew_scale_factor, i.e. the
    full 0-100 % edge of every slew in the delay grid; a list: those
    ramps; anything else: one run with the scalar handling ([None]).
    """
    sim_cfg = arc_config.get('_sim_cfg', {})
    val = sim_cfg.get('cap_ramp_ns')
    if val == "slew_grid":
        scale = float(sim_cfg.get('slew_scale_factor', 1.0))
        val = [float(x) * scale for x in sim_cfg.get('data_slew_ns', [])]
    if isinstance(val, (list, tuple)) and val:
        return [float(x) for x in val]
    return [None]


def _with_cap_ramp(arc_config, ramp):
    """Copy of arc_config whose _sim_cfg carries one scalar ramp."""
    run_cfg = dict(arc_config)
    sim_cfg = dict(run_cfg.get('_sim_cfg', {}))
    sim_cfg['cap_ramp_ns'] = ramp
    run_cfg['_sim_cfg'] = sim_cfg
    return run_cfg


def _combine_cap_runs(runs, ramps):
    """Mean over ramp runs; a single run is returned unchanged. With
    several ramps, min/max and the per-ramp values are kept as well."""
    if len(runs) == 1:
        return runs[0]
    out = {}
    for key in ('capacitance', 'rise_capacitance', 'fall_capacitance'):
        vals = np.array([r[key] for r in runs], dtype=float)
        good = vals[~np.isnan(vals)]
        out[key] = float(good.mean()) if good.size else np.nan
        out[f'{key}_range'] = ([float(good.min()), float(good.max())]
                               if good.size else [np.nan, np.nan])
    out['cap_ramp_ns'] = ramps
    out['per_ramp'] = runs
    return out


def _save_capacitance_results(cell_config, arc_name, cap_results):
    """Save capacitance results to JSON file."""
    arc_results_dir = RESULTS_DIR / cell_config['name']
    arc_results_dir.mkdir(parents=True, exist_ok=True)
    filename = arc_results_dir / f"{arc_name}_capacitance.json"

    with open(filename, 'w', encoding='utf-8') as fout:
        json.dump(cap_results, fout, indent=4)

    print(f"Saved: {filename}")


def _build_char_context(arc_config, global_settings, cli_args, is_sequential):
    """Build characterization context with all settings."""
    def get_cfg(key, default=None):
        if default is None:
            default = []
        val = arc_config.get(key)
        return val if val is not None else global_settings.get(key, default)

    output_loads = get_cfg("output_loads")

    return {
        'temp_values': get_cfg("temp_values"),
        'vdd_values': get_cfg("vdd_values"),
        'clock_slew_ns_list': get_cfg("clock_slew_ns"),
        'data_slew_ns_list': get_cfg("data_slew_ns"),
        'output_loads': output_loads,
        'loads_to_sweep': (
            get_cfg("sequential_loads")
            if is_sequential
            else get_cfg("output_loads")
        ),
        'degradation_percent': float(
            get_cfg("delay_degradation_percent", 10.0)
        ),
        'do_power_char': cli_args.char_power,
        'save_debug_plots': getattr(cli_args, 'debug_plots', False),
        'num_processes': int(get_cfg("num_processes", 8)),
        'slew_scale_factor': get_cfg("slew_scale_factor", 1.0 / 0.6),
        'get_cfg': get_cfg,
        'cli_args': cli_args
    }


def _initialize_tables():
    """Initialize empty characterization tables."""
    return {
        "timing": {},
        "transition": {},
        "dynamic_energy": {},
        "leakage_power": {},
        "hidden_energy": {}
    }


def _run_characterization_sweeps(sweep_config, char_context, tables):
    """Run temperature and voltage sweeps for characterization."""
    for temp in char_context['temp_values']:
        temp_str = str(temp)
        for table in tables.values():
            table[temp_str] = {}

        print(f"\n>> Sweeping at Temperature = {temp_str} C <<")

        for vdd in char_context['vdd_values']:
            for table in tables.values():
                table[temp_str][vdd] = {}

            print(f"  -> VDD = {vdd} V")

            # Route to appropriate characterization function
            _run_single_corner(
                temp_str, vdd, sweep_config, char_context, tables
            )


def _run_single_corner(temp_str, vdd, sweep_config, char_context, tables):
    """Run characterization for a single corner (temp, vdd)."""
    arc_type = sweep_config['arc_type']

    # ---- Power arcs (leakage / hidden / internal) ----
    # Routed through their own handler with sim_cfg propagated so the
    # netlist generators and measurement helpers can read settle/window
    # parameters from global_settings.
    if arc_type in ("leakage_power",
                    "hidden_power_internal",
                    "internal_power"):
        _power_keys = (
            "power_settle_window_ns", "power_settle_offset_ns",
            "power_hidden_window_ns", "power_toggle_window_factor",
            "power_leakage_preload_release_ns",
            "power_leakage_preload_edge_ns",
            "power_leakage_tail_start_pct",
            "power_leakage_tail_end_pct",
            "power_internal_subtract_pin_charges",
            "power_internal_subtract_load",
            "power_internal_subtract_leakage",
            "power_internal_subtract_hidden",
            "power_fixed_edge_window", "power_window_ns",
            "power_toggle_start_ns", "power_toggle_pad_ns",
            "sim_window_ns", "sim_timestep_ps",
        )
        per_arc = dict(sweep_config['arc_config'])
        sim_cfg = dict(per_arc.get('_sim_cfg', {}))
        for key in _power_keys:
            # get_cfg() turns a None default into [], so probe with a
            # sentinel: only copy keys that are actually configured.
            val = char_context['get_cfg'](key, _UNSET)
            if val is not _UNSET:
                sim_cfg[key] = val
        per_arc['_sim_cfg'] = sim_cfg
        per_arc['_cell_name'] = sweep_config['cell_config']['name']
        # Second output with its own load (CLoad_QN in the deck): internal
        # power must exclude that load's charge as well when it rises.
        _cell = sweep_config['cell_config']
        if _cell.get('has_qn_output', True):
            per_arc['_qn_load_node'] = _cell.get('qn_output_node', 'Q_N')

        corner_config = {
            'temp_str': temp_str,
            'vdd': vdd,
            'cell_config': sweep_config['cell_config'],
            'arc_config': per_arc,
            'spice_template': sweep_config['spice_template'],
            'arc_name': sweep_config['arc_name'],
            'arc_type': arc_type,
        }
        sweep_params = {
            'loads_to_sweep': char_context['loads_to_sweep'],
            'clock_slew_ns_list': char_context['clock_slew_ns_list'],
            'data_slew_ns_list': char_context['data_slew_ns_list'],
            'output_loads': char_context['output_loads'],
            'slew_scale_factor': char_context['slew_scale_factor'],
            'do_power_char': char_context['do_power_char'],
            'save_debug_plots': char_context['save_debug_plots'],
            'num_processes': char_context['num_processes'],
            'get_cfg': char_context['get_cfg'],
        }
        if not HAS_POWER:
            print(f"  ERROR: power characterization unavailable for arc "
                  f"type '{arc_type}' (characterizer_power not installed).")
            return
        characterize_power(
            corner_config, sweep_params, char_context['cli_args'], tables
        )
        return  # power handlers save their own JSON

    if arc_type == "mpw":
        # Build corner config
        corner_config = {
            'temp_str': temp_str,
            'vdd': vdd,
            'cell_config': sweep_config['cell_config'],
            'arc_config': sweep_config['arc_config'],
            'spice_template': sweep_config['spice_template'],
            'arc_name': sweep_config['arc_name']
        }

        # Build sweep params
        sweep_params = {
            'loads_to_sweep': char_context['loads_to_sweep'],
            'clock_slew_ns_list': char_context['clock_slew_ns_list'],
            'data_slew_ns_list': char_context['data_slew_ns_list'],
            'slew_scale_factor': char_context['slew_scale_factor'],
            'do_power_char': char_context['do_power_char'],
            'save_debug_plots': char_context['save_debug_plots'],
            'get_cfg': char_context['get_cfg'],
            'num_processes': char_context['num_processes']
        }

        # Call with 4 arguments
        characterize_mpw(
            corner_config, sweep_params, char_context['cli_args'], tables
        )
        return  # MPW saves its own results

    if sweep_config['is_combinational']:
        # Build corner config
        corner_config = {
            'temp_str': temp_str,
            'vdd': vdd,
            'cell_config': sweep_config['cell_config'],
            'arc_config': sweep_config['arc_config'],
            'spice_template': sweep_config['spice_template'],
            'arc_name': sweep_config['arc_name']
        }

        # Build sweep params
        sweep_params = {
            'loads_to_sweep': char_context['loads_to_sweep'],
            'clock_slew_ns_list': char_context['clock_slew_ns_list'],
            'data_slew_ns_list': char_context['data_slew_ns_list'],
            'slew_scale_factor': char_context['slew_scale_factor'],
            'do_power_char': char_context['do_power_char'],
            'save_debug_plots': char_context['save_debug_plots'],
            'num_processes': char_context['num_processes']
        }

        characterize_combinational(corner_config, sweep_params, tables)

    elif sweep_config['is_sequential']:
        # Build corner config
        corner_config = {
            'temp_str': temp_str,
            'vdd': vdd,
            'cell_config': sweep_config['cell_config'],
            'arc_config': sweep_config['arc_config'],
            'spice_template': sweep_config['spice_template'],
            'arc_name': sweep_config['arc_name'],
            'arc_type': arc_type
        }

        # Build sweep params
        sweep_params = {
            'loads_to_sweep': char_context['loads_to_sweep'],
            'clock_slew_ns_list': char_context['clock_slew_ns_list'],
            'data_slew_ns_list': char_context['data_slew_ns_list'],
            'slew_scale_factor': char_context['slew_scale_factor'],
            'do_power_char': char_context['do_power_char'],
            'save_debug_plots': char_context['save_debug_plots'],
            'degradation_percent': char_context['degradation_percent'],
            'num_processes': char_context['num_processes']
        }

        # Call with 4 arguments
        characterize_sequential(
            corner_config, sweep_params, char_context['cli_args'], tables
        )


def _build_result_filename(arc_results_dir, arc_name, name_map_key,
                           name_map, sweep_config):
    """Build output filename, unique for single-point runs."""
    is_single_point = sweep_config['arc_config'].get(
        '_sim_cfg', {}
    ).get('_single_point', False)
    if is_single_point:
        sim_cfg = sweep_config['arc_config'].get('_sim_cfg', {})
        temp = str(sim_cfg.get('temp_values', [''])[0])
        vdd = str(sim_cfg.get('vdd_values', [''])[0])
        data_slew = str(sim_cfg.get('data_slew_ns', [''])[0])
        load = str(sim_cfg.get('output_loads', [''])[0])
        return (
            arc_results_dir
            / f"point_{arc_name}_T{temp}_V{vdd}_DS{data_slew}"
              f"_L{load}_{name_map[name_map_key]}.json"
        )
    return arc_results_dir / f"{arc_name}_{name_map[name_map_key]}.json"


def _save_and_print_results(sweep_config, tables, char_context):
    """Save results and print final tables."""
    cell_config = sweep_config['cell_config']
    arc_name = sweep_config['arc_name']
    arc_type = sweep_config['arc_type']

    # Save results
    arc_results_dir = RESULTS_DIR / cell_config['name']
    arc_results_dir.mkdir(parents=True, exist_ok=True)

    name_map = {
        "timing": "timing_table",
        "transition": "transition_table",
        "dynamic_energy": "dynamic_energy_table",
        "leakage_power": "leakage_power_table",
        "hidden_energy": "hidden_energy_table"
    }

    if arc_type in ['cell_rise', 'cell_fall', 'propagation_delay']:
        name_map['timing'] = 'delay_table'
    elif arc_type == 'mpw':
        name_map['timing'] = 'mpw_table'
    for key, table in tables.items():
        if has_any_data(table):
            filename = _build_result_filename(
                arc_results_dir, arc_name, key, name_map, sweep_config
            )
            with open(filename, 'w', encoding='utf-8') as fout:
                json.dump(table, fout, indent=4)
            print(f"Saved {name_map[key]} to: {filename}")

    # Print final tables
    _print_final_tables(sweep_config, tables, char_context)


def _print_final_tables(sweep_config, tables, char_context):
    """Print final characterization tables."""
    cell_config = sweep_config['cell_config']
    arc_name = sweep_config['arc_name']
    arc_type = sweep_config['arc_type']

    # For power arcs, characterizer_power.py writes its own per-arc JSON
    # but doesn't populate `tables` (to avoid double-save). Re-load the
    # JSON from disk so the standard print loop below has data to show.
    if arc_type in (
        'leakage_power', 'hidden_power_internal', 'internal_power'
    ):
        _hydrate_tables_from_power_json(sweep_config, tables)

    for temp, temp_data in tables["timing"].items():
        print(f"\n--- Final Tables for {cell_config['name']}/{arc_name} "
              f"at TEMP = {temp} C ---")

        for vdd, _ in temp_data.items():
            print(f"  --- VDD = {vdd} V ---")

            _print_metrics_for_corner(
                temp, vdd, tables, char_context, sweep_config
            )


def _print_metrics_for_corner(temp, vdd, tables, char_context, sweep_config):
    """Print metrics for a single corner."""
    metrics = [
        "Timing (ns)",
        "Output Transition (ns)",
        "Leakage Power (uW)",
        "Dynamic Energy (fJ)"
    ]

    table_key_map = {
        "Timing (ns)": "timing",
        "Output Transition (ns)": "transition",
        "Leakage Power (uW)": "leakage_power",
        "Dynamic Energy (fJ)": "dynamic_energy"
    }

    for metric in metrics:
        key = table_key_map[metric]

        # Skip power metrics if not characterized
        if key in ["leakage_power", "dynamic_energy"]:
            if not char_context['do_power_char']:
                continue

        # Skip if no data
        if not any(tables[key][temp][vdd].values()):
            continue

        print(f"\n    {metric}:")
        if sweep_config['arc_type'] == 'mpw':
            _print_mpw_table(
                temp, vdd, key, tables, char_context['data_slew_ns_list']
            )

        elif sweep_config['is_combinational']:
            _print_combinational_table(
                temp, vdd, key, tables, char_context['data_slew_ns_list']
            )
        elif sweep_config['is_sequential']:
            _print_sequential_table(
                temp, vdd, key, tables, char_context['data_slew_ns_list']
            )


def _print_combinational_table(temp, vdd, key, tables, data_slew_ns_list):
    """Print combinational timing table."""
    header = (
        f"    {'Load (pF)':<10} | "
        + " | ".join([f"InSlew={s:<8.4f}" for s in data_slew_ns_list])
    )
    print(header)
    print(f"    {'-' * len(header)}")

    for load, slew_data in tables[key][temp][vdd].items():
        row_str = f"    {float(load):<10.4f} | "
        row_vals = [
            f"{slew_data.get(data_slew, np.nan):<13.5f}"
            for data_slew in data_slew_ns_list
        ]
        row_str += " | ".join(row_vals)
        print(row_str)


def _print_sequential_table(temp, vdd, key, tables, data_slew_ns_list):
    """Print sequential timing table."""
    header = (
        f"    {'Load':<8} | {'CkSlew':<10} | "
        + " | ".join([f"DSlew={s:<8.4f}" for s in data_slew_ns_list])
    )
    print(header)
    print(f"    {'-' * len(header)}")

    for load, clock_slew_data in tables[key][temp][vdd].items():
        for clock_slew, data_slew_data in clock_slew_data.items():
            row_str = f"    {load:<8} | {clock_slew:<10.5f} | "
            row_vals = [
                f"{data_slew_data.get(data_slew, np.nan):<13.3f}"
                for data_slew in data_slew_ns_list
            ]
            row_str += " | ".join(row_vals)
            print(row_str)


def _print_mpw_table(temp, vdd, key, tables, data_slew_ns_list):
    """Print MPW timing table."""
    header = (
        f"    {'Load (pF)':<10} | "
        + " | ".join([f"Index={s:<8.4f}" for s in data_slew_ns_list])
    )
    print(header)
    print(f"    {'-' * len(header)}")
    for load, slew_data in tables[key][temp][vdd].items():
        row_str = f"    {float(load):<10.4f} | "
        row_vals = [
            f"{slew_data.get(data_slew, np.nan):<13.5f}"
            for data_slew in data_slew_ns_list
        ]
        row_str += " | ".join(row_vals)
        print(row_str)


def _hydrate_tables_from_power_json(sweep_config, tables):
    """Read the per-arc power JSON written by characterizer_power and load
    it back into `tables` so _print_final_tables can display it."""
    cell_name = sweep_config['cell_config']['name']
    arc_name = sweep_config['arc_name']
    arc_type = sweep_config['arc_type']

    # Map arc_type -> (filename suffix, table key the data goes into)
    routing = {
        'leakage_power': ('leakage_power_table', 'leakage_power'),
        'hidden_power_internal': ('hidden_energy_table', 'hidden_energy'),
        'internal_power': ('dynamic_energy_table', 'dynamic_energy'),
    }
    suffix, table_key = routing[arc_type]
    path = RESULTS_DIR / cell_name / f"{arc_name}_{suffix}.json"
    if not path.exists():
        return

    with open(path, 'r', encoding='utf-8') as fout:
        data = json.load(fout)

    # Need to also seed timing structure so the outer
    # `for temp, temp_data in tables["timing"].items()` loop iterates.
    tables.setdefault(table_key, {})
    for temp, temp_data in data.items():
        tables.setdefault("timing", {}).setdefault(temp, {})
        tables[table_key].setdefault(temp, {})
        for vdd, vdd_data in temp_data.items():
            tables["timing"][temp].setdefault(vdd, {})
            # Flatten one level: leakage JSON is {temp:{vdd:{state:val}}},
            # internal/hidden is {temp:{vdd:{arc_name:...}}}.
            tables[table_key][temp][vdd] = _flatten_power_payload(
                arc_type, vdd_data
            )


def _flat_leakage(vdd_data):
    """{state_name: {p_leak_uw, ...}} -> {state_name: p_leak_uw}."""
    flat = {}
    for state_name, state_data in vdd_data.items():
        flat[state_name] = (
            state_data.get('p_leak_uw', float('nan'))
            if isinstance(state_data, dict) else state_data
        )
    return flat


def _flat_hidden(vdd_data):
    """{arc: {slew: {e_internal_fj, ...}}} -> {arc/slew: e_internal_fj}."""
    flat = {}
    for inner_arc_name, slew_dict in vdd_data.items():
        if not isinstance(slew_dict, dict):
            continue
        for slew, state_data in slew_dict.items():
            key = f"{inner_arc_name}/slew={slew}"
            flat[key] = (
                state_data.get('e_internal_fj', float('nan'))
                if isinstance(state_data, dict) else state_data
            )
    return flat


def _flat_internal(vdd_data):
    """{arc: {load: {slew: e_fj}}} -> {arc/L=load/slew=slew: e_fj}."""
    flat = {}
    for inner_arc_name, load_dict in vdd_data.items():
        if not isinstance(load_dict, dict):
            continue
        for load, slew_dict in load_dict.items():
            if not isinstance(slew_dict, dict):
                continue
            for slew, val in slew_dict.items():
                flat[f"{inner_arc_name}/L={load}/slew={slew}"] = val
    return flat


def _flatten_power_payload(arc_type, vdd_data):
    """Flatten the per-arc JSON payload into a {key: scalar_uW_or_fJ} dict
    so the existing print code's `any(...).values()` check works."""
    flatteners = {
        'leakage_power': _flat_leakage,
        'hidden_power_internal': _flat_hidden,
        'internal_power': _flat_internal,
    }
    flattener = flatteners.get(arc_type)
    return flattener(vdd_data) if flattener else {}
