# import json
import multiprocessing
from pathlib import Path
import numpy as np
from tqdm import tqdm

from ..simulation import run_single_simulation, run_simulation_for_plot
from ..plotting import (
    generate_sequential_curve_plot,
    save_debug_waveform,
)

DEBUG_PLOT_DIR = Path('debug_plots')
CURVE_PLOT_DIR = Path('curve_plots')
RESULTS_DIR = Path('results')
NUM_PROCESSES = 8


def characterize_sequential(corner_config, sweep_params, cli_args, tables):
    """
    Characterize sequential timing arcs.

    Args:
        corner_config: Dict with temp_str, vdd, cell_config, arc_config,
                       spice_template, arc_name, arc_type
        sweep_params: Dict with loads_to_sweep, clock_slew_ns_list,
                      data_slew_ns_list, slew_scale_factor, do_power_char,
                      save_debug_plots, degradation_percent
        cli_args: Command-line arguments
        tables: Results tables dict
    """
    seq_config = {**corner_config, **sweep_params, 'cli_args': cli_args}

    for load in sweep_params['loads_to_sweep']:
        _process_sequential_load(load, seq_config, tables)


def _process_sequential_load(load, seq_config, tables):
    """Process characterization for a single load value."""
    _initialize_sequential_tables(load, seq_config, tables)

    for clock_slew_ns in seq_config['clock_slew_ns_list']:
        for data_slew_ns in seq_config['data_slew_ns_list']:
            _process_sequential_corner(
                load, clock_slew_ns, data_slew_ns, seq_config, tables
            )


def _initialize_sequential_tables(load, seq_config, tables):
    """Initialize sequential timing tables for a load."""
    temp_str = seq_config['temp_str']
    vdd = seq_config['vdd']

    tables["timing"][temp_str][vdd][load] = {
        cs: {} for cs in seq_config['clock_slew_ns_list']
    }
    tables["transition"][temp_str][vdd][load] = {
        cs: {} for cs in seq_config['clock_slew_ns_list']
    }

    if seq_config['do_power_char']:
        tables["dynamic_energy"][temp_str][vdd][load] = {
            cs: {} for cs in seq_config['clock_slew_ns_list']
        }
        tables["leakage_power"][temp_str][vdd][load] = {
            cs: {} for cs in seq_config['clock_slew_ns_list']
        }


def _process_sequential_corner(load, clock_slew_ns, data_slew_ns,
                               seq_config, tables):
    """Process one corner (load, clock slew, data slew)."""
    slew_context = _build_slew_context(
        load, clock_slew_ns, data_slew_ns, seq_config
    )

    print(f"    -> Running for CkSlew: {slew_context['clock_slew_str']}, "
          f"DeSlew: {slew_context['data_slew_str']}, Load: {load}")

    # Run simulations
    results = _run_sequential_sweep(slew_context, seq_config)

    # Analyze and store
    timing_val_ps = _analyze_sequential_results(
        results, slew_context, seq_config, tables
    )

    # Store timing result
    tables["timing"][seq_config['temp_str']][seq_config['vdd']][load][
        clock_slew_ns][data_slew_ns] = timing_val_ps / 1000.0


def _build_slew_context(load, clock_slew_ns, data_slew_ns, seq_config):
    """Build context for current slew values."""
    spice_clock_slew = clock_slew_ns * seq_config['slew_scale_factor']
    spice_data_slew = data_slew_ns * seq_config['slew_scale_factor']

    return {
        'load': load,
        'clock_slew_ns': clock_slew_ns,
        'data_slew_ns': data_slew_ns,
        'clock_slew_str': f"{spice_clock_slew}n",
        'data_slew_str': f"{spice_data_slew}n"
    }


def _run_sequential_sweep(slew_context, seq_config):
    """Run simulations for sequential arc."""
    arc_config = seq_config['arc_config']

    if "sweep_range_ns" in arc_config:
        return _run_fixed_range_sweep(slew_context, seq_config)
    return _run_adaptive_sweep(slew_context, seq_config)


def _run_fixed_range_sweep(slew_context, seq_config):
    """Run sweep with fixed range from config."""
    start, stop, points = seq_config['arc_config']["sweep_range_ns"]

    tasks = _build_sweep_tasks(
        slew_context, seq_config, np.linspace(start, stop, int(points))
    )

    return _execute_parallel_sweep(tasks, slew_context, seq_config)


def _run_adaptive_sweep(slew_context, seq_config):
    """Run sweep with adaptive binary search."""
    sim_cfg = seq_config['arc_config'].get('_sim_cfg', {})
    iterations = sim_cfg.get("binary_search_iterations", 8)
    sweep_points = sim_cfg.get("sequential_sweep_points", 100)
    margin = sim_cfg.get("sequential_sweep_margin_ns", 1.05)
    fail_bound, pass_bound = _get_search_bounds(
        seq_config['arc_type'], sim_cfg
    )
    if not _check_timing_point(slew_context, pass_bound, seq_config):
        print(f"      WARNING: Arc failed at safe delta {pass_bound}ns.")
        return []

    # Binary search for failure point
    current_fail, current_pass = fail_bound, pass_bound
    for _ in range(iterations):
        mid = (current_fail + current_pass) / 2.0
        if _check_timing_point(slew_context, mid, seq_config):
            current_pass = mid
        else:
            current_fail = mid
    auto_start = current_fail - margin
    auto_stop = current_fail + margin
    auto_points = sweep_points
    tasks = _build_sweep_tasks(
        slew_context, seq_config,
        np.linspace(auto_start, auto_stop, int(auto_points))
    )

    return _execute_parallel_sweep(tasks, slew_context, seq_config)


def _check_timing_point(slew_context, test_delta, seq_config):
    """Check if timing delta produces valid result."""
    task_args = (
        seq_config['temp_str'], seq_config['vdd'],
        slew_context['clock_slew_str'], slew_context['data_slew_str'],
        str(slew_context['load']), test_delta,
        seq_config['cell_config'], seq_config['arc_config'],
        seq_config['spice_template'], False
    )
    res = run_single_simulation(task_args)
    return res.get('t_prop') is not None and not np.isnan(res.get('t_prop'))


def _build_sweep_tasks(slew_context, seq_config, delta_values):
    """Build list of simulation tasks."""
    return [
        (seq_config['temp_str'], seq_config['vdd'],
         slew_context['clock_slew_str'], slew_context['data_slew_str'],
         str(slew_context['load']), delta,
         seq_config['cell_config'], seq_config['arc_config'],
         seq_config['spice_template'], seq_config['do_power_char'])
        for delta in delta_values
    ]


def _execute_parallel_sweep(tasks, slew_context, seq_config):
    """Execute tasks in parallel with progress bar."""
    desc = (f"T={seq_config['temp_str']},V={seq_config['vdd']},"
            f"L={slew_context['load']},CS={slew_context['clock_slew_str']},"
            f"DS={slew_context['data_slew_str']}")
    with multiprocessing.Pool(
        processes=seq_config.get('num_processes', NUM_PROCESSES)
    ) as pool:
        return list(tqdm(
            pool.imap(run_single_simulation, tasks),
            total=len(tasks), desc=desc, leave=False
        ))


def _analyze_sequential_results(results, slew_context, seq_config, tables):
    """Analyze simulation results and extract timing."""
    valid_results = [
        r for r in results
        if r.get('t_prop') is not None and not np.isnan(r.get('t_prop'))
    ]

    if len(valid_results) < 2:
        print("      WARNING: Not enough data for timing. Skipping.")
        return np.nan

    # Sort and prepare data
    valid_results.sort(key=lambda r: r['delta_ns'])
    analysis_data = _prepare_analysis_data(
        valid_results, seq_config['arc_type']
    )

    # Find timing constraint
    timing_val_ps = _find_timing_constraint(
        analysis_data,
        seq_config['arc_type'],
        seq_config['degradation_percent']
    )

    # Store power and transition
    _store_seq_power_transition(
        valid_results, slew_context, seq_config, tables, analysis_data
    )

    # Generate plots if requested
    if seq_config['save_debug_plots']:
        _generate_sequential_debug_plot(
            valid_results, slew_context, seq_config, analysis_data
        )

    if seq_config['cli_args'].save_curves and seq_config['arc_type'] in (
        'setup',
        'hold',
        'setup_latch',
        'hold_latch',
        'recovery',
        'removal',
    ):
        stable_tprop, _ = _determine_stable_region(
            seq_config['arc_type'], analysis_data['analysis_y']
        )
        generate_sequential_curve_plot(
            analysis_data, timing_val_ps, slew_context, seq_config,
            stable_tprop
        )
    return timing_val_ps


def _determine_stable_region(arc_type, analysis_data_y):
    """Determine stable region for degradation analysis."""
    num_stable = max(2, int(len(analysis_data_y) * 0.2))

    if 'removal' in arc_type or arc_type == 'hold_latch':
        return np.mean(analysis_data_y[:num_stable]), 0
    return np.mean(analysis_data_y[-num_stable:]), len(analysis_data_y) - 1


def _prepare_analysis_data(valid_results, arc_type):
    """Prepare analysis data from results."""
    final_delta_ps = np.array([r['delta_ns'] for r in valid_results]) * 1000
    final_tprop_ns = np.array([r['t_prop'] * 1e9 for r in valid_results])

    if arc_type == 'setup_latch':
        analysis_data_y = final_tprop_ns + (final_delta_ps / 1000.0)
        y_label = "D-to-Q Propagation Delay (ns)"
    elif arc_type == 'hold_latch':
        analysis_data_y = final_tprop_ns - (final_delta_ps / 1000.0)
        y_label = "D-to-Q Propagation Delay (ns)"
    else:
        analysis_data_y = final_tprop_ns
        y_label = "Clock-to-Q Propagation Delay (ns)"

    return {
        'delta_ps': final_delta_ps,
        'tprop_ns': final_tprop_ns,
        'analysis_y': analysis_data_y,
        'y_label': y_label
    }


def _find_timing_constraint(analysis_data, arc_type, degradation_percent):
    """Find timing constraint value."""
    stable_tprop, _ = _determine_stable_region(
        arc_type, analysis_data['analysis_y']
    )

    degradation_amount = abs(stable_tprop) * (degradation_percent / 100.0)
    threshold_tprop = stable_tprop + degradation_amount

    diff = analysis_data['analysis_y'] - threshold_tprop
    cross_indices = np.where(np.diff(np.sign(diff)))[0]

    if cross_indices.size > 0:
        idx1, idx2 = cross_indices[0], cross_indices[0] + 1
        prop1 = analysis_data['analysis_y'][idx1]
        delt1 = analysis_data['delta_ps'][idx1]
        prop2 = analysis_data['analysis_y'][idx2]
        delt2 = analysis_data['delta_ps'][idx2]
        if abs(prop2 - prop1) > 1e-12:
            slope = (delt2 - delt1) / (prop2 - prop1)
            return delt1 + slope * (threshold_tprop - prop1)
    # Handle edge cases
    if np.all(diff > 0):
        print("      WARNING: All points failed timing check.")
        return np.nan
    if np.all(diff < 0):
        print("      INFO: Hard Failure (Cliff) detected.")
        if 'removal' in arc_type or arc_type == 'hold_latch':
            return analysis_data['delta_ps'][-1]
        return analysis_data['delta_ps'][0]
    return np.nan


def _store_seq_power_transition(valid_results, slew_context,
                                seq_config, tables, analysis_data):
    """Store power and transition results."""
    _, stable_idx = _determine_stable_region(
        seq_config['arc_type'], analysis_data['analysis_y']
    )
    stable_result = valid_results[stable_idx]

    temp_str = seq_config['temp_str']
    vdd = seq_config['vdd']
    load = slew_context['load']
    clock_slew_ns = slew_context['clock_slew_ns']
    data_slew_ns = slew_context['data_slew_ns']

    if seq_config['do_power_char']:
        leakage = stable_result.get('leakage_power')
        dynamic = stable_result.get('dynamic_energy')

        tables["leakage_power"][temp_str][vdd][load][clock_slew_ns][
            data_slew_ns] = leakage * 1e6 if leakage is not None else np.nan
        tables["dynamic_energy"][temp_str][vdd][load][clock_slew_ns][
            data_slew_ns] = dynamic * 1e15 if dynamic is not None else np.nan

    transition = stable_result.get('t_tran')
    tables["transition"][temp_str][vdd][load][clock_slew_ns][data_slew_ns] = (
        transition * 1e9 if transition is not None else np.nan
    )


def _generate_sequential_debug_plot(
    valid_results,
    slew_context,
    seq_config,
    analysis_data,
):
    """Generate debug waveform plot."""
    _, stable_idx = _determine_stable_region(
        seq_config['arc_type'], analysis_data['analysis_y']
    )
    stable_delta_ns = valid_results[stable_idx]['delta_ns']

    plot_args = (
        seq_config['temp_str'], seq_config['vdd'],
        slew_context['clock_slew_str'], slew_context['data_slew_str'],
        slew_context['load'], stable_delta_ns,
        seq_config['cell_config'], seq_config['arc_config'],
        seq_config['spice_template']
    )

    plot_data = run_simulation_for_plot(plot_args)
    if not plot_data:
        return

    save_debug_waveform(
        plot_data, slew_context, seq_config, stable_delta_ns
    )


def _get_search_bounds(arc_type, sim_cfg=None):
    """Get binary search bounds for sequential timing arcs."""
    if sim_cfg is None:
        sim_cfg = {}
    if 'hold' in arc_type:
        if 'latch' in arc_type:
            return (
                sim_cfg.get("seq_search_fail_bound_latch_hold", 1.0),
                sim_cfg.get("seq_search_pass_bound_latch_hold", -4.0)
            )
        return (
            sim_cfg.get("seq_search_fail_bound_hold", -1.0),
            sim_cfg.get("seq_search_pass_bound_hold", 4.0)
        )
    if 'removal' in arc_type:
        return (
            sim_cfg.get("seq_search_fail_bound_removal", 1.0),
            sim_cfg.get("seq_search_pass_bound_removal", -4.0)
        )
    if 'latch' in arc_type:
        return (
            sim_cfg.get("seq_search_fail_bound_latch", -1.5),
            sim_cfg.get("seq_search_pass_bound_latch", 1.0)
        )
    return (
        sim_cfg.get("seq_search_fail_bound_ff", -6.0),
        sim_cfg.get("seq_search_pass_bound_ff", 6.0)
    )
