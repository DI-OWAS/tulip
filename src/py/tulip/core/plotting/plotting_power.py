"""Power debug plots."""

from pathlib import Path

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
matplotlib.use('Agg')

POWER_PLOT_DIR = Path("power_plots")


def save_power_plot(plot_data, arc_config, sim_cfg, vdd_numeric, leak_a,
                    dyn_energy_fj, cell_name, slew_ns, load_pf,
                    output_transition):
    """
    Two-panel power plot:
      - Top: full power waveform with leakage line and integration markers
      - Bottom: zoomed integration window with shaded dynamic-energy region

    Direct port of the monolith's plot_power_waveforms, adapted to read
    its waveforms from a NgSpice plot dict (returned by
    run_simulation_for_plot) rather than receiving pre-computed arrays.
    """
    if plot_data is None:
        return

    # Look up waveforms (case-insensitive, like the existing modules)
    plot_lower = {str(k).lower(): k for k in plot_data.keys()}

    def _vec(name):
        low = name.lower()
        for cand in (f"v({low})", low):
            if cand in plot_lower:
                return np.asarray(plot_data[plot_lower[cand]].to_waveform())
        return None

    def _branch(name):
        low = name.lower()
        for cand in (f"v_{low}#branch", f"v{low}#branch"):
            if cand in plot_lower:
                return np.asarray(plot_data[plot_lower[cand]].to_waveform())
        return None

    time_vec = _vec("time")
    v_vdd = _vec("vdd")
    i_vdd = _branch("VVDD") if _branch("VVDD") is not None else _branch("VDD")
    if time_vec is None or v_vdd is None or i_vdd is None:
        return

    # Instantaneous power, monolith convention
    power_inst = np.maximum(v_vdd * (-i_vdd), 0)

    # Integration window: must match the math in measure_internal_energy
    # (toggling branch — save_power_plot is only called for internal_power
    # arcs, never hidden).
    integ_start_ns = sim_cfg.get("power_toggle_start_ns", 4.0)
    toggle_factor = sim_cfg.get("power_toggle_window_factor", 3.0)
    longest_delay_ns = arc_config.get("longest_expected_delay_ns", 5.0)
    integ_window_ns = max(toggle_factor * longest_delay_ns, 5.0)
    integ_start_s = integ_start_ns * 1e-9
    integ_end_s = (integ_start_ns + integ_window_ns) * 1e-9

    # Leakage power line (from cross-arc lookup); 0 if unknown
    leak_w = (abs(leak_a) * vdd_numeric
              if leak_a is not None and not np.isnan(leak_a) else 0.0)

    # ---- Build figure ----
    POWER_PLOT_DIR.mkdir(parents=True, exist_ok=True)
    plot_dir = POWER_PLOT_DIR / cell_name
    plot_dir.mkdir(parents=True, exist_ok=True)
    fname = (plot_dir
             / f"{arc_config['name']}_{output_transition}"
             f"_slew{slew_ns:.4f}ns_load{load_pf}pf.png")

    _fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    time_ns = time_vec * 1e9
    power_uw = power_inst * 1e6
    leak_uw = leak_w * 1e6

    # Top: full waveform
    ax1.plot(time_ns, power_uw, "b-", linewidth=1.5,
             label="Instantaneous Power")
    ax1.axhline(y=leak_uw, color="r", linestyle="--", linewidth=1,
                label=f"Leakage: {leak_uw:.3f} \u00b5W")
    ax1.axvline(x=integ_start_s * 1e9, color="g", linestyle="--",
                linewidth=1.5, label="Integration Start")
    ax1.axvline(x=integ_end_s * 1e9, color="purple", linestyle="--",
                linewidth=1.5, label="Integration End")
    ax1.set_xlabel("Time (ns)", fontsize=11)
    ax1.set_ylabel("Power (\u00b5W)", fontsize=11)
    ax1.set_title(
        f"Power Waveform - {cell_name}/{arc_config['name']} - "
        f"{output_transition.upper()}\n"
        f"Slew: {slew_ns:.4f} ns, Load: {load_pf} pF, "
        f"E_switch: {dyn_energy_fj:.3f} fJ",
        fontsize=12,
    )
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="best", fontsize=9)

    # Bottom: zoomed integration window
    zoom_start = max(0.0, integ_start_s - 0.5e-9)
    zoom_end = min(time_vec[-1], integ_end_s + 0.5e-9)
    zoom_mask = (time_vec >= zoom_start) & (time_vec <= zoom_end)
    if np.any(zoom_mask):
        ax2.plot(time_ns[zoom_mask], power_uw[zoom_mask], "b-", linewidth=1.5)
        ax2.axhline(y=leak_uw, color="r", linestyle="--", linewidth=1,
                    label=f"Leakage: {leak_uw:.3f} \u00b5W")
        ax2.axvspan(integ_start_s * 1e9, integ_end_s * 1e9,
                    alpha=0.2, color="green", label="Integration Window")
        integ_mask = (time_vec >= integ_start_s) & (time_vec <= integ_end_s)
        if np.any(integ_mask):
            ax2.fill_between(time_ns[integ_mask], leak_uw,
                             power_uw[integ_mask], alpha=0.3,
                             color="yellow", label="Dynamic Energy")
    ax2.set_xlabel("Time (ns)", fontsize=11)
    ax2.set_ylabel("Power (\u00b5W)", fontsize=11)
    ax2.set_title("Zoomed Integration Window", fontsize=12)
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="best", fontsize=9)

    plt.tight_layout()
    plt.savefig(fname, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved power plot: {fname}")
