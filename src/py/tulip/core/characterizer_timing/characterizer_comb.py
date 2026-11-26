# import json
import multiprocessing
from pathlib import Path
import numpy as np

from ..simulation import run_single_simulation, run_simulation_for_plot
from ..plotting import (
    generate_overlapped_plot,
    save_single_plot,
)

DEBUG_PLOT_DIR = Path('debug_plots')
RESULTS_DIR = Path('results')
NUM_PROCESSES = 8


def characterize_combinational(corner_config, sweep_params, tables):
    """
    Characterize combinational delay arcs.

    Args:
        corner_config: Dict with temp_str, vdd, cell_config, arc_config,
                       spice_template, arc_name
        sweep_params: Dict with loads_to_sweep, clock_slew_ns_list,
                      data_slew_ns_list, slew_scale_factor, do_power_char,
                      save_debug_plots
        tables: Results tables dict
    """
    # Build configuration context
    comb_config = {
        'temp_str': corner_config['temp_str'],
        'vdd': corner_config['vdd'],
        'clock_slew_ns_list': sweep_params['clock_slew_ns_list'],
        'data_slew_ns_list': sweep_params['data_slew_ns_list'],
        'slew_scale_factor': sweep_params['slew_scale_factor'],
        'cell_config': corner_config['cell_config'],
        'arc_config': corner_config['arc_config'],
        'spice_template': corner_config['spice_template'],
        'do_power_char': sweep_params['do_power_char'],
        'save_debug_plots': sweep_params['save_debug_plots'],
        'arc_name': corner_config['arc_name'],
        'num_processes': sweep_params.get('num_processes', NUM_PROCESSES)
    }

    # Process each load
    for load in sweep_params['loads_to_sweep']:
        _process_combinational_load(load, comb_config, tables)


def _process_combinational_load(load, comb_config, tables):
    """Process characterization for a single load value."""
    temp_str = comb_config['temp_str']
    vdd = comb_config['vdd']

    # Initialize tables for this load
    _initialize_load_tables(
        load, temp_str, vdd, tables, comb_config['do_power_char']
    )

    # Build simulation tasks
    tasks = _build_combinational_tasks(load, comb_config)

    # Run simulations in parallel
    print(f"    -> Running {len(tasks)} slews in parallel for Load {load}...")
    with multiprocessing.Pool(
        processes=comb_config.get('num_processes', NUM_PROCESSES)
    ) as pool:
        results = pool.map(run_single_simulation, [t['task'] for t in tasks])

    # Store results and generate plots
    accumulated_plots = {}
    for task_info, result in zip(tasks, results):
        _store_combinational_result(
            task_info, result, load, comb_config, tables
        )

        if comb_config['save_debug_plots']:
            plot_data = _generate_single_plot(task_info, load, comb_config)
            if plot_data:
                accumulated_plots[task_info['data_slew_ns']] = plot_data

    # Generate overlapped plot
    if comb_config['save_debug_plots'] and accumulated_plots:
        generate_overlapped_plot(load, accumulated_plots, comb_config)


def _initialize_load_tables(load, temp_str, vdd, tables, do_power_char):
    """Initialize table entries for a specific load."""
    tables["timing"][temp_str][vdd][load] = {}
    tables["transition"][temp_str][vdd][load] = {}
    if do_power_char:
        tables["dynamic_energy"][temp_str][vdd][load] = {}
        tables["leakage_power"][temp_str][vdd][load] = {}


def _build_combinational_tasks(load, comb_config):
    """Build list of simulation tasks for all slew values."""
    tasks = []

    for data_slew_ns in comb_config['data_slew_ns_list']:
        spice_data_slew = data_slew_ns * comb_config['slew_scale_factor']
        data_slew_str = f"{spice_data_slew}n"

        spice_clock_slew = (
            comb_config['clock_slew_ns_list'][0]
            * comb_config['slew_scale_factor']
        )
        clock_slew_str = f"{spice_clock_slew}n"

        task = (
            comb_config['temp_str'], comb_config['vdd'],
            clock_slew_str, data_slew_str, str(load), 0.0,
            comb_config['cell_config'], comb_config['arc_config'],
            comb_config['spice_template'], comb_config['do_power_char']
        )

        tasks.append({
            'data_slew_ns': data_slew_ns,
            'clock_slew_str': clock_slew_str,
            'data_slew_str': data_slew_str,
            'task': task
        })

    return tasks


def _store_combinational_result(task_info, result, load, comb_config, tables):
    """Store simulation results in tables."""
    if not result or np.isnan(result.get('t_prop')):
        return

    temp_str = comb_config['temp_str']
    vdd = comb_config['vdd']
    data_slew_ns = task_info['data_slew_ns']

    tables["timing"][temp_str][vdd][load][data_slew_ns] = (
        result['t_prop'] * 1e9
    )
    tables["transition"][temp_str][vdd][load][data_slew_ns] = (
        result['t_tran'] * 1e9
    )

    if comb_config['do_power_char']:
        tables["dynamic_energy"][temp_str][vdd][load][data_slew_ns] = (
            result['dynamic_energy'] * 1e15
        )
        tables["leakage_power"][temp_str][vdd][load][data_slew_ns] = (
            result['leakage_power'] * 1e6
        )


def _generate_single_plot(task_info, load, comb_config):
    """Generate debug plot for a single slew value."""
    plot_args = (
        comb_config['temp_str'], comb_config['vdd'],
        task_info['clock_slew_str'], task_info['data_slew_str'],
        str(load), 0.0,
        comb_config['cell_config'], comb_config['arc_config'],
        comb_config['spice_template']
    )

    plot_data = run_simulation_for_plot(plot_args)
    if not plot_data:
        return None

    # Extract vectors
    vectors = _extract_plot_vectors(plot_data, comb_config['arc_config'])
    if not vectors:
        return None

    # Save individual plot (pass tuple instead of unpacked values)
    save_single_plot(vectors, load, task_info['data_slew_ns'], comb_config)

    return vectors  # Return tuple directly


def _extract_plot_vectors(plot_data, arc_config):
    """Extract time, input, and output vectors from plot data."""
    plot_data_lower = {str(k).lower(): v for k, v in plot_data.items()}

    time_vec = np.asarray(plot_data_lower['time'].to_waveform()) * 1e9

    input_pin = arc_config["data_signal"].lower()
    v_in = (np.asarray(plot_data_lower[input_pin].to_waveform())
            if input_pin in plot_data_lower else None)

    output_pin = arc_config["output_signal"].lower()
    v_out = (np.asarray(plot_data_lower[output_pin].to_waveform())
             if output_pin in plot_data_lower else None)

    if v_in is None or v_out is None:
        return None

    return (time_vec, v_in, v_out)
