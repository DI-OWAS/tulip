# import gc
# import json
import multiprocessing
from pathlib import Path
import numpy as np
from tqdm import tqdm

from ..simulation import run_single_simulation, run_simulation_for_plot
from ..plotting import (
    save_mpw_curve_plot,
    save_mpw_waveform,
)


DEBUG_PLOT_DIR = Path('debug_plots')
CURVE_PLOT_DIR = Path('curve_plots')
RESULTS_DIR = Path('results')
NUM_PROCESSES = 8


def characterize_mpw(corner_config, sweep_params, cli_args, tables):
    """
    Characterize Minimum Pulse Width timing arc.

    Args:
        corner_config: Dict with temp_str, vdd, cell_config, arc_config,
                       spice_template, arc_name
        sweep_params: Dict with loads_to_sweep, clock_slew_ns_list,
                      data_slew_ns_list, slew_scale_factor, do_power_char,
                      save_debug_plots, get_cfg
        cli_args: Command-line arguments
        tables: Results tables
    """
    mpw_config = {**corner_config, **sweep_params, 'cli_args': cli_args}

    print(f"\n{'='*70}")
    print(f"  MPW Characterization: {mpw_config['arc_config']['name']}")
    print(f"{'='*70}")

    # Configure thresholds
    _configure_mpw_thresholds(mpw_config)
    for load in mpw_config['loads_to_sweep']:
        _process_mpw_load(load, mpw_config, tables)

    print(f"\n{'='*70}")
    print("  MPW Characterization Complete")
    print(f"{'='*70}\n")


def _configure_mpw_thresholds(mpw_config):
    """Configure MPW input and output thresholds."""
    get_cfg = mpw_config['get_cfg']
    arc_config = mpw_config['arc_config']

    mpw_input_threshold = float(get_cfg("mpw_input_threshold", 0.9))
    arc_config["mpw_input_threshold"] = mpw_input_threshold
    print(f"  MPW Input Threshold: {mpw_input_threshold*100:.0f}% of VDD")

    mpw_output_threshold = float(get_cfg("mpw_output_threshold", 0.8))
    arc_config["mpw_output_threshold"] = mpw_output_threshold
    print(f"  MPW Output Threshold: {mpw_output_threshold*100:.0f}% of VDD")


def _process_mpw_load(load, mpw_config, tables):
    """Process MPW characterization for a single load."""
    temp_str = mpw_config['temp_str']
    vdd = mpw_config['vdd']

    # Initialize tables
    _initialize_mpw_tables(
        load, temp_str, vdd, tables, mpw_config['do_power_char']
    )

    # Get clock slew
    clock_slew_ns = mpw_config['clock_slew_ns_list'][0]
    spice_clock_slew = clock_slew_ns * mpw_config['slew_scale_factor']
    clock_slew_str = f"{spice_clock_slew}n"

    # Process each data slew
    for data_slew_ns in mpw_config['data_slew_ns_list']:
        _process_mpw_corner(
            load, data_slew_ns, clock_slew_str, mpw_config, tables
        )


def _initialize_mpw_tables(load, temp_str, vdd, tables, do_power_char):
    """Initialize MPW tables for a load."""
    tables["timing"][temp_str][vdd][load] = {}
    tables["transition"][temp_str][vdd][load] = {}
    if do_power_char:
        tables["dynamic_energy"][temp_str][vdd][load] = {}
        tables["leakage_power"][temp_str][vdd][load] = {}


def _process_mpw_corner(
    load, data_slew_ns, clock_slew_str, mpw_config, tables
):
    # Compute edge slew per data slew index
    mpw_edge_slew_ns = data_slew_ns * mpw_config['slew_scale_factor']
    mpw_edge_slew_str = f"{mpw_edge_slew_ns}n"

    print(f"\n  Load={load}pF, Index={data_slew_ns}ns "
          f"(Edge={mpw_edge_slew_str})")

    # Build MPW context with load and data_slew_ns
    mpw_context = {
        'load': load,
        'data_slew_ns': data_slew_ns,
        'temp_str': mpw_config['temp_str'],
        'vdd': mpw_config['vdd'],
        'clock_slew_str': clock_slew_str,
        'data_slew_str': mpw_edge_slew_str,   # <-- per-slew edge
        'cell_config': mpw_config['cell_config'],
        'arc_config': mpw_config['arc_config'],
        'spice_template': mpw_config['spice_template']
    }

    # also pass to _analyze so curve plot labels are correct
    mpw_config['mpw_edge_slew_str'] = mpw_edge_slew_str
    mpw_config['mpw_edge_slew_ns'] = mpw_edge_slew_ns

    mpw_edge = _find_mpw_edge(mpw_context)
    if mpw_edge is None:
        return

    # Run fine sweep around edge
    results = _run_mpw_sweep(mpw_edge, mpw_context, mpw_config)
    if not results:
        tables["timing"][mpw_config['temp_str']][mpw_config['vdd']][load][
            data_slew_ns
        ] = np.nan
        return

    # Analyze and store results
    _analyze_mpw_results(results, mpw_context, mpw_config, tables)


def _find_mpw_edge(mpw_context):
    """Find MPW edge using binary search."""
    sim_cfg = mpw_context['arc_config'].get('_sim_cfg', {})
    min_width = sim_cfg.get("mpw_min_width_ns", 0.020)
    max_width = sim_cfg.get("mpw_max_width_ns", 5.0)
    iterations = sim_cfg.get("binary_search_iterations", 10)
    print(f"    [Binary Search] Testing range: "
          f"{min_width*1000:.0f}ps to {max_width*1000:.0f}ps")

    # Check if max width works
    if not _check_mpw_point(max_width, mpw_context):
        print(f"    ERROR: Even {max_width}ns pulse fails!")
        print(f"           Check initialization: "
              f"{mpw_context['arc_config'].get('initialization')}")
        print(f"           Check static pins: "
              f"{mpw_context['arc_config'].get('static_pins')}")
        print("           Skipping this condition.")
        return None

    # Binary search
    current_min, current_max = min_width, max_width
    for _ in range(iterations):
        mid = (current_min + current_max) / 2.0
        if _check_mpw_point(mid, mpw_context):
            current_max = mid
        else:
            current_min = mid

    mpw_edge = current_min
    print(f"    [Binary Search] Edge found at: {mpw_edge*1000:.1f}ps")
    return mpw_edge


def _check_mpw_point(pulse_width_ns, mpw_context):
    """Check if pulse width produces valid output transition."""
    task = (
        mpw_context['temp_str'], mpw_context['vdd'],
        mpw_context['clock_slew_str'], mpw_context['data_slew_str'],
        str(mpw_context['load']), pulse_width_ns,
        mpw_context['cell_config'], mpw_context['arc_config'],
        mpw_context['spice_template'], False
    )
    res = run_single_simulation(task)
    t_prop = res.get('t_prop')

    if pulse_width_ns < 0.1:
        status = (
            'NaN' if t_prop is None or np.isnan(t_prop)
            else f'{t_prop*1e9:.3f}ns'
        )
        print(f"      [Debug] Width={pulse_width_ns*1000:.1f}ps "
              f"→ t_prop={status}")

    return (t_prop is not None and not np.isnan(t_prop) and t_prop < 10e-9)


def _run_mpw_sweep(mpw_edge, mpw_context, mpw_config):
    """Run fine sweep around MPW edge."""
    # Determine sweep range
    sim_cfg_ = mpw_context['arc_config'].get('_sim_cfg', {})
    sweep_params = _get_sweep_parameters(mpw_edge, sim_cfg_)
    sweep_start, sweep_end, num_points = sweep_params
    if sweep_start < 0:
        print(
            "    WARNING: Sweep start was negative "
            f"({sweep_start}), clamping to 0.01ns"
        )
        sweep_start = 0.01

    # Build tasks
    sweep_widths = np.linspace(sweep_start, sweep_end, num_points)
    tasks = [
        (mpw_context['temp_str'], mpw_context['vdd'],
         mpw_context['clock_slew_str'], mpw_context['data_slew_str'],
         str(mpw_context['load']), width,
         mpw_context['cell_config'], mpw_context['arc_config'],
         mpw_context['spice_template'], mpw_config['do_power_char'])
        for width in sweep_widths
    ]
    # Execute in parallel
    desc = (
        f"MPW Sweep L={mpw_context['load']}pF "
        f"Slew={mpw_context['data_slew_ns']}ns"
    )
    with multiprocessing.Pool(
        processes=mpw_config.get('num_processes', NUM_PROCESSES)
    ) as pool:
        return list(tqdm(
            pool.imap(run_single_simulation, tasks),
            total=len(tasks), desc=desc, leave=False
        ))


def _get_sweep_parameters(mpw_edge, sim_cfg=None):
    """Determine sweep parameters based on MPW edge location."""
    if sim_cfg is None:
        sim_cfg = {}
    pts_fast = sim_cfg.get("mpw_sweep_points_fast", 50)
    pts_normal = sim_cfg.get("mpw_sweep_points_normal", 100)
    if mpw_edge < 0.1:
        sweep_start, sweep_end, num_points = 0.01, 0.5, pts_fast
        print("    [Fine Sweep] Very fast cell - using 10ps to 500ps")
    elif mpw_edge < 0.5:
        sweep_start = max(0.01, mpw_edge - 0.2)
        sweep_end = mpw_edge + 0.5
        num_points = int(pts_fast * 1.5)
        print(f"    [Fine Sweep] Fast cell - "
              f"using {sweep_start*1000:.0f}ps to {sweep_end*1000:.0f}ps")
    else:
        sweep_start = max(0.05, mpw_edge - 1.0)
        sweep_end = min(mpw_edge + 3.0, 10.0)
        num_points = pts_normal
        print(f"    [Fine Sweep] Normal cell - "
              f"using {sweep_start*1000:.0f}ps to {sweep_end*1000:.0f}ps")

    return sweep_start, sweep_end, num_points


def _analyze_mpw_results(results, mpw_context, mpw_config, tables):
    """Analyze MPW results and store in tables."""
    load = mpw_context['load']
    data_slew_ns = mpw_context['data_slew_ns']

    valid_results = [
        r for r in results
        if r.get('t_prop') is not None and not np.isnan(r.get('t_prop'))
    ]

    if len(valid_results) < 3:
        print(
            f"    ERROR: Insufficient valid data points "
            f"({len(valid_results)})"
        )
        tables["timing"][mpw_config['temp_str']][mpw_config['vdd']][load][
            data_slew_ns
        ] = np.nan
        return

    valid_results.sort(key=lambda r: r['delta_ns'])

    # Calculate MPW
    mpw_result_ps = _calculate_mpw_result(
        valid_results,
        mpw_config['mpw_edge_slew_ns']
    )

    # Print statistics
    num_pass = len(valid_results)
    num_fail = len(results) - num_pass
    print(f"    [Stats] Pass: {num_pass}, Fail: {num_fail}")

    # Build plot context combining all needed info
    plot_context = {
        **mpw_context,
        **mpw_config,
        'mpw_result_ps': mpw_result_ps,
        'valid_results': valid_results,
        'all_results': results
    }

    # Store results
    _store_mpw_results(plot_context, tables)

    # Generate plots
    if mpw_config['save_debug_plots']:
        _generate_mpw_debug_plot(plot_context)

    save_mpw_curve_plot(plot_context)


def _calculate_mpw_result(valid_results, mpw_edge_slew_ns,
                          degradation_percent=10.0):
    # Convert flat-top delta_ns to 50-50 pulse widths
    slew_ps = mpw_edge_slew_ns * 1000
    pulse_widths_ps = (
        np.array([r['delta_ns'] for r in valid_results]) * 1000 + slew_ps
    )

    num_stable = max(2, int(len(valid_results) * 0.2))
    stable_tprop_ns = np.mean(
        [r['t_prop'] * 1e9 for r in valid_results[-num_stable:]]
    )
    threshold_ns = stable_tprop_ns * (1 + degradation_percent / 100.0)
    tprop_ns = np.array([r['t_prop'] * 1e9 for r in valid_results])

    diff = tprop_ns - threshold_ns
    cross_indices = np.where(np.diff(np.sign(diff)))[0]

    if cross_indices.size > 0:
        i = cross_indices[0]
        mpw_result_ps = pulse_widths_ps[i] + (
            (pulse_widths_ps[i + 1] - pulse_widths_ps[i])
            * (threshold_ns - tprop_ns[i])
            / (tprop_ns[i + 1] - tprop_ns[i])
        )
        return mpw_result_ps

    return valid_results[0]['delta_ns'] * 1000 + slew_ps


def _store_mpw_results(plot_context, tables):
    """Store MPW results in tables."""
    temp_str = plot_context['temp_str']
    vdd = plot_context['vdd']
    load = plot_context['load']
    data_slew_ns = plot_context['data_slew_ns']
    mpw_result_ps = plot_context['mpw_result_ps']
    valid_results = plot_context['valid_results']

    stable_idx = len(valid_results) - 1
    stable_result = valid_results[stable_idx]

    tables["timing"][temp_str][vdd][load][
        data_slew_ns
    ] = mpw_result_ps / 1000.0

    transition = stable_result.get('t_tran')
    tables["transition"][temp_str][vdd][load][data_slew_ns] = (
        transition * 1e9 if transition is not None else np.nan
    )

    if plot_context['do_power_char']:
        leakage = stable_result.get('leakage_power')
        dynamic = stable_result.get('dynamic_energy')

        tables["leakage_power"][temp_str][vdd][load][data_slew_ns] = (
            leakage * 1e6 if leakage is not None else np.nan
        )
        tables["dynamic_energy"][temp_str][vdd][load][data_slew_ns] = (
            dynamic * 1e15 if dynamic is not None else np.nan
        )


def _generate_mpw_debug_plot(plot_context):
    """Generate MPW debug waveform plot."""
    valid_results = plot_context['valid_results']
    plot_width_ns = valid_results[-1]['delta_ns'] if valid_results else 0.5

    plot_args = (
        plot_context['temp_str'], plot_context['vdd'],
        plot_context['clock_slew_str'], plot_context['data_slew_str'],
        plot_context['load'], plot_width_ns,
        plot_context['cell_config'], plot_context['arc_config'],
        plot_context['spice_template']
    )

    plot_data = run_simulation_for_plot(plot_args)
    if not plot_data:
        return

    save_mpw_waveform(plot_data, plot_context)
