"""ngspice execution and measurement routing.

Power measurement is imported defensively so that a timing-only
distribution without ``measure_power`` still imports and runs."""

import gc
import os
import numpy as np
from PySpice.Spice.NgSpice.Shared import NgSpiceShared
from .build import (
    build_netlist,
    finalize_netlist,
    prepare_netlist,
)
from .measure_cap import (
    measure_input_capacitance,
)
from .measure_mpw import (
    measure_mpw_timing,
)
from .measure_timing import (
    measure_reference_time,
    measure_standard_timing,
)
from .vectors import (
    failed_result,
    get_vector_safe,
)

try:  # optional: timing-only builds omit this module
    from .measure_power import (
        measure_internal_energy,
        measure_leakage,
    )
    HAS_POWER = True
except ImportError:  # pragma: no cover
    HAS_POWER = False


def run_simulation_for_plot(args):
    """
    Run SPICE simulation and return raw plot data for visualization.
    Args:
        args: Tuple of simulation parameters
    Returns:
        NgSpice plot object or None on failure
    """
    (temp_str, vdd_str, clock_slew_str, data_slew_str, load_str,
     delta_ns, cell_config, arc_config, spice_template_str) = args
    sim_conditions = {
        'temp_str': temp_str,
        'vdd_str': vdd_str,
        'clock_slew_str': clock_slew_str,
        'data_slew_str': data_slew_str,
        'load_str': load_str,
        'delta_ns': delta_ns
    }
    netlist_string = build_netlist(
        spice_template_str, sim_conditions, arc_config, cell_config
    )
    final_netlist = finalize_netlist(
        netlist_string, vdd_str, load_str, clock_slew_str, data_slew_str
    )
    ngspice = None
    try:
        ngspice = NgSpiceShared.new_instance()
        try:  # ngspice's OpenMP team ignores OMP_NUM_THREADS; one thread per
            # pool worker, else N workers x N threads spin on N cores.
            ngspice.exec_command(
                "set num_threads="
                + os.environ.get("TULIP_NGSPICE_THREADS", "1"))
        except Exception:  # pylint: disable=broad-except
            pass
        ngspice.load_circuit(final_netlist)
        ngspice.run()
        return ngspice.plot(simulation=None, plot_name='tran1')
    except Exception as exc:
        print(f"  WARNING: Failed to generate debug plot for "
              f"arc '{arc_config['name']}'. Error: {exc}")
        return None
    finally:
        if ngspice:
            try:
                ngspice.destroy()
            except Exception:
                pass
        gc.collect()


def run_single_simulation(args):
    """
    Run SPICE simulation and extract timing/power measurements.
    Args:
        args: Tuple of (temp_str, vdd_str, clock_slew_str, data_slew_str,
                       load_str, delta_ns, cell_config, arc_config,
                       spice_template_str, do_power_char)
    Returns:
        dict: Measurement results (t_prop, t_tran, leakage_power,
               dynamic_energy, or capacitance values)
    """
    # Debug logging for MPW
    arc_config = args[7]
    if arc_config.get("type") == "mpw":
        print(f"      [SIM_DEBUG] delta_ns={args[5]}, "
              f"data_slew_str={args[3]}")
    # Prepare netlist and context
    final_netlist = prepare_netlist(args)
    sim_context = {
        'arc_type': arc_config.get("type"),
        'delta_ns': args[5],
        'vdd_str': args[1],
        'data_slew_str': args[3],
        'do_power_char': args[9]
    }
    # Run simulation
    return _run_spice_and_measure(final_netlist, arc_config, sim_context)


def _run_spice_and_measure(final_netlist, arc_config, sim_context):
    """
    Execute SPICE simulation and route to measurement functions.
    Args:
        final_netlist: Complete SPICE netlist string
        arc_config: Arc configuration dictionary
        sim_context: Dict with arc_type, delta_ns, vdd_str,
        data_slew_str, do_power_char
    Returns:
        dict: Measurement results
    """
    ngspice = None
    try:
        ngspice = NgSpiceShared.new_instance()
        try:  # ngspice's OpenMP team ignores OMP_NUM_THREADS; one thread per
            # pool worker, else N workers x N threads spin on N cores.
            ngspice.exec_command(
                "set num_threads="
                + os.environ.get("TULIP_NGSPICE_THREADS", "1"))
        except Exception:  # pylint: disable=broad-except
            pass
        ngspice.load_circuit(final_netlist)
        try:
            ngspice.run()
        except Exception:  # pylint: disable=broad-except
            # PySpice reports any stderr line not starting with "Warning:"
            # as a failed run, including ngspice's informational
            # "Note: Starting dynamic gmin stepping" on a transient that
            # completed. For power arcs, fail only on other stderr lines
            # (timing arcs keep the old behaviour).
            errors = [ln for ln in getattr(ngspice, "_stderr", [])
                      if not str(ln).lstrip().startswith("Note:")]
            if (errors or sim_context.get('arc_type') not in (
                    "leakage_power", "internal_power",
                    "hidden_power_internal")):
                raise
        plot = ngspice.plot(simulation=None, plot_name='tran1')
        # Route to measurement
        return _route_to_measurement(plot, arc_config, sim_context)
    except Exception as exc:
        print(f"\nFATAL EXCEPTION in run_single_simulation for "
              f"arc '{arc_config['name']}': {exc}")
        fail_dir = (arc_config.get('_sim_cfg', {}).get('dump_decks')
                    or 'failed_decks')
        os.makedirs(fail_dir, exist_ok=True)
        fail_path = os.path.join(
            fail_dir, f"FAILED_{arc_config.get('name', 'arc')}.sp")
        with open(fail_path, 'w', encoding='utf-8') as fdeck:
            fdeck.write(final_netlist)
        print(f"      -> deck written to {fail_path}")
        return failed_result(sim_context['delta_ns'])
    finally:
        if ngspice:
            try:
                ngspice.destroy()
            except Exception:
                pass
        gc.collect()


def _timing_result(delta_ns, t_prop, t_tran, dynamic_energy=np.nan):
    """Result dict for a timing measurement."""
    return {
        "delta_ns": delta_ns,
        "t_prop": t_prop,
        "t_tran": t_tran,
        "leakage_power": np.nan,
        "dynamic_energy": dynamic_energy,
    }


def _route_power_arc(plot, arc_config, arc_type, vdd_numeric, delta_ns):
    """Route a power-only arc, or report that power support is absent."""
    if not HAS_POWER:
        print(f"  ERROR: power measurement unavailable for arc "
              f"type '{arc_type}' (measure_power not installed).")
        return failed_result(delta_ns)
    if arc_type == "leakage_power":
        return measure_leakage(plot, arc_config, vdd_numeric, delta_ns)
    return measure_internal_energy(
        plot, arc_config, vdd_numeric, delta_ns, output_toggles=False,
    )


def _route_to_measurement(plot, arc_config, sim_context):
    """
    Route to appropriate measurement function based on arc type.
    Args:
        plot: NgSpice plot object
        arc_config: Arc configuration dictionary
        sim_context: Simulation context dictionary
    Returns:
        dict: Measurement results
    """
    # Extract context first
    arc_type = sim_context['arc_type']
    delta_ns = sim_context['delta_ns']
    vdd_numeric = float(sim_context['vdd_str'])

    # Verify time vector exists (always required)
    time_vec = get_vector_safe(plot, 'time')
    if time_vec is None:
        print("  ERROR: Could not find time vector in plot.")
        print(f"  Available keys: {list(plot.keys())}")
        return failed_result(delta_ns)

        # Power-only arcs: route BEFORE the v_q lookup. For leakage, Q is
    # observed only as a settling sanity check; not strictly required.
    if arc_type in ("leakage_power", "hidden_power_internal"):
        return _route_power_arc(
            plot, arc_config, arc_type, vdd_numeric, delta_ns
        )

    # Verify output signal vector for everything else
    v_q = get_vector_safe(plot, arc_config["output_signal"])
    if v_q is None:
        print(f"  ERROR: Could not find output vector "
              f"'{arc_config['output_signal']}' in plot.")
        print(f"  Available keys: {list(plot.keys())}")
        return failed_result(delta_ns)

    if arc_type == "input_capacitance":
        return measure_input_capacitance(
            plot, arc_config, vdd_numeric, delta_ns
        )

    if arc_type == "mpw":
        t_prop, t_tran = measure_mpw_timing(
            plot, arc_config, vdd_numeric,
            delta_ns, sim_context['data_slew_str']
        )
    else:
        t_ref = measure_reference_time(
            arc_type, arc_config, plot, vdd_numeric
        )
        t_prop, t_tran = measure_standard_timing(
            plot, arc_config, vdd_numeric, t_ref
        )
        if t_prop is not None and not np.isnan(t_prop):
            print(f"      [TIMING_DEBUG] t_ref={t_ref*1e9:.4f}ns, "
                  f"delay={t_prop*1e9:.4f}ns")

    # Internal power: timing AND power on the same run (Q toggles)
    if arc_type == "internal_power":
        power = (
            measure_internal_energy(
                plot, arc_config, vdd_numeric, delta_ns,
                output_toggles=True,
            )
            if HAS_POWER else {}
        )
        return _timing_result(
            delta_ns, t_prop, t_tran,
            power.get("dynamic_energy", np.nan)
        )
    return _timing_result(delta_ns, t_prop, t_tran)
