"""Sequential (setup/hold/recovery) debug and curve plots."""

from pathlib import Path
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
matplotlib.use('Agg')

DEBUG_PLOT_DIR = Path('debug_plots')
CURVE_PLOT_DIR = Path('curve_plots')


def save_debug_waveform(plot_data, slew_context, seq_config, stable_delta_ns):
    """Save debug waveform to file."""
    plot_dir = DEBUG_PLOT_DIR / seq_config['cell_config']['name']
    plot_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        plot_dir
        / (
            f"{seq_config['arc_name']}_T{seq_config['temp_str']}_"
            f"V{seq_config['vdd']}_L{slew_context['load']}_"
            f"CS{slew_context['clock_slew_ns']}_"
            f"DS{slew_context['data_slew_ns']}ns.png"
        )
    )
    plot_data_lower = {k.lower(): v for k, v in plot_data.items()}
    time_vec = np.asarray(plot_data_lower['time'].to_waveform())

    plt.figure(figsize=(12, 8))

    # Plot reference signal
    ref_pin = (
        seq_config['arc_config'].get("clock_signal")
        or seq_config['arc_config'].get("gate_signal")
    )
    if ref_pin and ref_pin.lower() in plot_data_lower:
        plt.plot(
            time_vec * 1e9,
            np.asarray(plot_data_lower[ref_pin.lower()].to_waveform()),
            label=f"V({ref_pin})",
            linestyle="--",
        )
    # Plot data signal
    data_signal = seq_config['arc_config'].get("data_signal")
    if data_signal and data_signal.lower() in plot_data_lower:
        plt.plot(
            time_vec * 1e9,
            np.asarray(plot_data_lower[data_signal.lower()].to_waveform()),
            label=f"V({data_signal})",
        )
    # Plot output
    v_q = np.asarray(
        plot_data_lower[
            seq_config['arc_config']["output_signal"].lower()
        ].to_waveform()
    )
    plt.plot(
        time_vec * 1e9, v_q,
        label=f"V({seq_config['arc_config']['output_signal']})",
        linewidth=2.5
    )

    plt.title(
        f"Debug Waveform: {seq_config['cell_config']['name']}/"
        f"{seq_config['arc_name']}\n"
        f"Stable Delta={stable_delta_ns*1e3:.2f}ps"
    )
    plt.xlabel("Time (ns)")
    plt.ylabel("Voltage (V)")
    plt.grid(True)
    plt.legend()
    plt.savefig(filename)
    plt.close()
    print(f"      -> Saved debug waveform to: {filename}")


def generate_sequential_curve_plot(
    analysis_data,
    timing_val_ps,
    slew_context,
    seq_config,
    stable_tprop,
):
    """Generate timing curve plot.

    ``stable_tprop`` is supplied by the caller so that this module needs
    nothing from the characterizer package (keeps the dependency one-way).
    """
    plot_dir = CURVE_PLOT_DIR / seq_config['cell_config']['name']
    plot_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        plot_dir
        / (
            f"{seq_config['arc_name']}_T{seq_config['temp_str']}_"
            f"V{seq_config['vdd']}_L{slew_context['load']}_"
            f"CS{slew_context['clock_slew_ns']}_"
            f"DS{slew_context['data_slew_ns']}ns.png"
        )
    )
    degradation_amount = (
        abs(stable_tprop) * (seq_config['degradation_percent'] / 100.0)
    )
    threshold_tprop = stable_tprop + degradation_amount

    plt.figure(figsize=(10, 6))
    plt.plot(
        analysis_data['delta_ps'], analysis_data['analysis_y'], 'o-',
        label='Simulation Data'
    )
    plt.axhline(
        y=threshold_tprop, color='r', linestyle='--',
        label=f'Threshold ({threshold_tprop:.3f} ns)'
    )

    if not np.isnan(timing_val_ps):
        plt.axvline(
            x=timing_val_ps,
            color='g',
            linestyle='--',
            label=f'Result ({timing_val_ps:.2f} ps)',
        )

    cell_name = seq_config['cell_config']['name']
    arc_name = seq_config['arc_name']
    plt.title(
        f"{cell_name} / {arc_name}\n"
        f"(T={seq_config['temp_str']}C, VDD={seq_config['vdd']}V, "
        f"L={slew_context['load']}, "
        f"CS={slew_context['clock_slew_ns']}ns, "
        f"DS={slew_context['data_slew_ns']}ns)"
    )

    plt.xlabel(_get_xlabel_for_arc_type(seq_config['arc_type']))
    plt.ylabel(analysis_data['y_label'])
    plt.grid(True)
    plt.legend()
    plt.savefig(filename)
    plt.close()
    print(f"      -> Saved curve plot: {filename}")


def _get_xlabel_for_arc_type(arc_type):
    """Get X-axis label based on arc type."""
    labels = {
        'setup': "Data to Clock Delta (ps)",
        'setup_latch': "Data to Clock Delta (ps)",
        'hold': "Clock to Data Delta (ps)",
        'hold_latch': "Clock to Data Delta (ps)",
        'recovery': "Async De-assert to Clock Delta (ps)",
        'removal': "Clock to Async De-assert Delta (ps)",
        'mpw': "Pulse Width (ps)"
    }
    return labels.get(arc_type, "Delta (ps)")
