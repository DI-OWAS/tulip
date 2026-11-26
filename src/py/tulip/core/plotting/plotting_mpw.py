"""Minimum-pulse-width debug and curve plots."""

from pathlib import Path
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
matplotlib.use('Agg')

DEBUG_PLOT_DIR = Path('debug_plots')
CURVE_PLOT_DIR = Path('curve_plots')


def save_mpw_waveform(plot_data, plot_context):
    """Save MPW waveform plot."""
    plot_dir = DEBUG_PLOT_DIR / plot_context['cell_config']['name']
    plot_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        plot_dir
        / (
            f"MPW_{plot_context['arc_config']['name']}_"
            f"T{plot_context['temp_str']}_"
            f"V{plot_context['vdd']}_"
            f"L{plot_context['load']}_"
            f"Slew{plot_context['data_slew_ns']}ns.png"
        )
    )

    plot_data_lower = {k.lower(): v for k, v in plot_data.items()}
    time_vec = np.asarray(plot_data_lower['time'].to_waveform())

    plt.figure(figsize=(12, 8))

    pulse_pin = plot_context['arc_config']["data_signal"].lower()
    if pulse_pin in plot_data_lower:
        plt.plot(
            time_vec * 1e9,
            np.asarray(plot_data_lower[pulse_pin].to_waveform()),
            label=f"V({plot_context['arc_config']['data_signal']}) - Pulsed",
            linestyle='--', linewidth=2
        )

    output_pin = plot_context['arc_config']["output_signal"].lower()
    if output_pin in plot_data_lower:
        plt.plot(
            time_vec * 1e9,
            np.asarray(plot_data_lower[output_pin].to_waveform()),
            label=f"V({plot_context['arc_config']['output_signal']}) - Output",
            linewidth=2.5
        )

    plt.title(
        f"MPW Debug: {plot_context['cell_config']['name']}/"
        f"{plot_context['arc_config']['name']}\n"
        f"MPW={plot_context['mpw_result_ps']:.1f}ps"
    )
    plt.xlabel("Time (ns)")
    plt.ylabel("Voltage (V)")
    plt.grid(True)
    plt.legend()
    plt.savefig(filename)
    plt.close()
    print(f"    Saved waveform: {filename.name}")


def save_mpw_curve_plot(plot_context):
    """Save MPW characterization curve plot."""
    if not plot_context['cli_args'].save_curves:
        return

    plot_dir = CURVE_PLOT_DIR / plot_context['cell_config']['name']
    plot_dir.mkdir(parents=True, exist_ok=True)

    filename = (
        plot_dir
        / (
            f"MPW_{plot_context['arc_config']['name']}_"
            f"T{plot_context['temp_str']}_"
            f"V{plot_context['vdd']}_"
            f"L{plot_context['load']}_"
            f"Slew{plot_context['data_slew_ns']}ns.png"
        )
    )

    valid_results = plot_context['valid_results']
    all_results = plot_context['all_results']
    mpw_result_ps = plot_context['mpw_result_ps']

    pulse_widths_ps = np.array([r['delta_ns'] for r in valid_results]) * 1000
    delays_ns = np.array([r['t_prop'] * 1e9 for r in valid_results])

    _, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), height_ratios=[2, 1])

    # Top plot: Delay vs pulse width
    ax1.plot(
        pulse_widths_ps,
        delays_ns,
        'o-',
        label='Simulation Data',
        markersize=4, linewidth=2
    )

    if not np.isnan(mpw_result_ps):
        ax1.axvline(
            x=mpw_result_ps,
            color='g',
            linestyle='--',
            linewidth=2,
            label=f'MPW Result ({mpw_result_ps:.1f}ps)'
        )

    ax1.set_ylabel("Propagation Delay (ns)", fontsize=11)
    ax1.set_title(
        f"{plot_context['cell_config']['name']} / "
        f"{plot_context['arc_config']['name']}\n"
        f"(T={plot_context['temp_str']}C, "
        f"VDD={plot_context['vdd']}V, "
        f"Load={plot_context['load']}pF, "
        f"Edge={plot_context['mpw_edge_slew_str']})",
        fontsize=12
    )
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='best')

    # Bottom plot: Pass/fail indicator
    _add_mpw_pass_fail_plot(ax2, all_results, mpw_result_ps)

    plt.tight_layout()
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"    Saved curve plot: {filename.name}")


def _add_mpw_pass_fail_plot(ax2, all_results, mpw_result_ps):
    """Add pass/fail plot to bottom axis."""
    all_widths = [r['delta_ns'] * 1000 for r in all_results]
    all_status = [
        0 if (r.get('t_prop') is None or np.isnan(r.get('t_prop'))) else 1
        for r in all_results
    ]

    colors = ['red' if s == 0 else 'green' for s in all_status]
    ax2.bar(
        all_widths,
        all_status,
        width=max(all_widths) * 0.02,
        color=colors, alpha=0.6,
        edgecolor='black',
        linewidth=0.5
    )

    if not np.isnan(mpw_result_ps):
        ax2.axvline(
            x=mpw_result_ps,
            color='darkgreen',
            linestyle='--',
            linewidth=3,
            label=f'MPW = {mpw_result_ps:.1f}ps'
        )

    ax2.set_xlabel("Pulse Width (ps)", fontsize=11)
    ax2.set_ylabel("Status", fontsize=11)
    ax2.set_ylim(-0.1, 1.2)
    ax2.set_yticks([0, 1])
    ax2.set_yticklabels(['FAIL', 'PASS'])
    ax2.grid(True, alpha=0.3, axis='x')
    ax2.legend(loc='upper left')

    num_pass = sum(all_status)
    num_fail = len(all_status) - num_pass
    ax2.text(
        0.98, 0.95,
        (
            f"Pass: {num_pass}/{len(all_status)}  "
            f"Fail: {num_fail}/{len(all_status)}"
        ),
        transform=ax2.transAxes, ha='right', va='top',
        bbox={'boxstyle': 'round', 'facecolor': 'wheat', 'alpha': 0.5},
        fontsize=10
    )
