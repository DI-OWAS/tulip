"""Combinational debug plots."""

from pathlib import Path
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
matplotlib.use('Agg')


DEBUG_PLOT_DIR = Path('debug_plots')


def save_single_plot(plot_vectors, load, data_slew_ns, comb_config):
    """Save individual debug plot for one slew value."""
    time_vec, v_in, v_out = plot_vectors

    plot_dir = DEBUG_PLOT_DIR / comb_config['cell_config']['name']
    plot_dir.mkdir(parents=True, exist_ok=True)

    filename = (plot_dir / f"COMB_{comb_config['arc_name']}_L{load}_"
                f"Slew{data_slew_ns}.png")

    plt.figure(figsize=(10, 6))
    plt.plot(
        time_vec,
        v_in,
        label=f"Input: {comb_config['arc_config']['data_signal']}",
        linestyle='--'
    )
    plt.plot(
        time_vec,
        v_out,
        label=f"Output: {comb_config['arc_config']['output_signal']}",
        linewidth=2
    )
    plt.title(
        f"Debug: {comb_config['arc_name']}"
        f"(L={load}pF, Slew={data_slew_ns}ns)"
    )
    plt.xlabel("Time (ns)")
    plt.ylabel("Voltage (V)")
    plt.grid(True)
    plt.legend()
    plt.savefig(filename)
    plt.close()
    print(f"      -> Saved comb plot: {filename}")


def generate_overlapped_plot(load, accumulated_plots, comb_config):
    """Generate overlapped plot showing all slew values."""
    plot_dir = DEBUG_PLOT_DIR / comb_config['cell_config']['name']
    filename = (
        plot_dir
        / f"OVERLAP_{comb_config['arc_name']}_Load_{load}pF.png"
    )
    plt.figure(figsize=(12, 8))
    plt.title(
        f"Slew Sweep Analysis: "
        f"{comb_config['arc_name']} @ Load={load}pF"
    )
    cmap = plt.get_cmap('viridis')
    colors = cmap(np.linspace(0, 1, len(accumulated_plots)))

    for (slew_val, (time, v_in_vec, v_out_vec)), color in zip(
            accumulated_plots.items(), colors):
        plt.plot(
            time,
            v_in_vec,
            linestyle='--',
            linewidth=1,
            color=color,
            alpha=0.5
        )
        plt.plot(
            time,
            v_out_vec,
            linestyle='-',
            linewidth=2,
            color=color,
            label=f'Slew={slew_val}ns'
        )

    plt.xlabel("Time (ns)")
    plt.ylabel("Voltage (V)")
    plt.legend(title="Input Slew", bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.grid(True)
    plt.tight_layout()
    plt.savefig(filename)
    plt.close()
    print(f"      -> Saved overlapped plot: {filename}")
