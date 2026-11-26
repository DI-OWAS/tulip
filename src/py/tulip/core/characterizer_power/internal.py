"""Internal (switching) energy characterization."""

from pathlib import Path
import json
import multiprocessing

import numpy as np
from ..ic_precompute import precompute_settle_state
from ..simulation import (
    run_simulation_for_plot,
    run_single_simulation,
)
from .hidden import (
    _hidden_lookup,
    _load_hidden_energy,
)
from .leakage import (
    _maybe_lookup_leakage,
)
from ..plotting import save_power_plot
from .states import (
    _resolve_hidden_pair,
)

RESULTS_DIR = Path("results")


def _characterize_internal(corner_config, sweep_params, cli_args, tables):
    arc_config = corner_config["arc_config"]
    cell_config = corner_config["cell_config"]
    spice_template = corner_config["spice_template"]
    temp_str = corner_config["temp_str"]
    vdd = corner_config["vdd"]
    arc_name = corner_config["arc_name"]

    if ("function_mapping" not in arc_config
            and "function_mapping" in cell_config):
        arc_config = dict(arc_config)
        arc_config["function_mapping"] = cell_config["function_mapping"]

    # Use power_slews_ns and power_loads_pf if defined, else fall back
    slew_scale = sweep_params.get("slew_scale_factor", 1.0)
    data_slew_ns_list = arc_config.get("power_slews_ns",
                                       sweep_params["data_slew_ns_list"])
    if "power_load_pf" in arc_config:
        # Single load specified (used for the per-state CLK arcs in the config)
        loads_to_sweep = [arc_config["power_load_pf"]]
    elif "power_loads_pf" in arc_config:
        loads_to_sweep = arc_config["power_loads_pf"]
    else:
        loads_to_sweep = sweep_params.get(
            "output_loads", ["0.001", "0.039", "0.18"]
        )

    print(f"\n--- Characterizing Internal Power: "
          f"{cell_config['name']}/{arc_name} ---")
    print(f"  Slews: {data_slew_ns_list}")
    print(f"  Loads: {loads_to_sweep}")

    # Optional: precompute the leakage current for the matching state, so
    # the per-corner subtraction Q_leak * window can be exact.
    leak_a = _maybe_lookup_leakage(arc_config, tables, temp_str, vdd)
    if leak_a is not None and not np.isnan(leak_a):
        print(f"  Using leakage subtraction: I_leak = {leak_a * 1e9:.3f} nA")

    # Liberate-style IC precompute: long settle pass, cache internal node
    # voltages so each measurement sim skips DC and starts from steady state.
    ic_dict = precompute_settle_state(
        spice_template,
        cell_config,
        arc_config,
        temp_str,
        str(vdd),
        settle_ns=arc_config.get("_sim_cfg", {}).get(
            "ic_settle_window_ns", 500.0
        ),
    )
    if ic_dict:
        print(f"  IC precompute: cached {len(ic_dict)} internal nodes")

    # All (load, slew) points are independent: run them in parallel, the
    # same pattern as the hidden-power and timing characterizers.
    tasks, keys = [], []
    for load_str in loads_to_sweep:
        load_str = str(load_str)
        for slew_ns in data_slew_ns_list:
            spice_slew = slew_ns * slew_scale
            clock_slew_str = f"{spice_slew}n"
            data_slew_str = f"{spice_slew}n"

            per_arc = dict(arc_config)
            per_arc["_sim_cfg"] = arc_config.get("_sim_cfg", {})
            per_arc["_load_str"] = load_str
            if leak_a is not None:
                per_arc["_leak_current_a"] = leak_a
            if ic_dict:
                per_arc["_ic_dict"] = ic_dict

            tasks.append((
                temp_str, vdd, clock_slew_str, data_slew_str, load_str, 0.0,
                cell_config, per_arc, spice_template, True,
            ))
            keys.append((float(load_str), float(slew_ns)))

    get_cfg = sweep_params.get("get_cfg")
    n_cfg = (get_cfg("internal_num_processes",
                     sweep_params.get("num_processes", 8))
             if get_cfg else sweep_params.get("num_processes", 8))
    n_proc = max(1, min(int(n_cfg), len(tasks)))
    print(f"  Running {len(tasks)} points with {n_proc} workers...")
    with multiprocessing.get_context("spawn").Pool(processes=n_proc) as pool:
        results = pool.map(run_single_simulation, tasks)

    by_load = {}
    for (load_f, slew_f), result in zip(keys, results):
        e_j = result.get("dynamic_energy", float("nan"))
        by_load.setdefault(load_f, {})[slew_f] = (
            e_j * 1e15
            if e_j is not None and not np.isnan(e_j)
            else float("nan")
        )

    # Power waveform plot — one representative point per arc, monolith-style.
    # Gated by --debug-plots, like the other characterizer modules.
    if (sweep_params.get("save_debug_plots") and loads_to_sweep
            and data_slew_ns_list):
        rep_load = str(loads_to_sweep[len(loads_to_sweep) // 2])
        rep_slew_ns = data_slew_ns_list[len(data_slew_ns_list) // 2]
        rep_spice_slew = rep_slew_ns * slew_scale
        rep_slew_str = f"{rep_spice_slew}n"
        plot_arc = dict(arc_config)
        plot_arc["_sim_cfg"] = arc_config.get("_sim_cfg", {})
        plot_args = (
            temp_str, vdd, rep_slew_str, rep_slew_str, rep_load, 0.0,
            cell_config, plot_arc, spice_template,
        )
        plot_data = run_simulation_for_plot(plot_args)
        if plot_data is not None:
            output_transition = arc_config.get(
                "expected_output_transition",
                arc_config.get("data_transition", "rise"),
            )
            e_fj = by_load.get(float(rep_load), {}).get(
                float(rep_slew_ns), float("nan"))
            save_power_plot(
                plot_data=plot_data,
                arc_config=arc_config,
                sim_cfg=arc_config.get("_sim_cfg", {}),
                vdd_numeric=float(vdd),
                leak_a=leak_a,
                dyn_energy_fj=e_fj if not np.isnan(e_fj) else 0.0,
                cell_name=cell_config["name"],
                slew_ns=rep_slew_ns,
                load_pf=rep_load,
                output_transition=output_transition,
            )

    # ----------------------------------------------------------------------
    # Liberate-style hidden subtraction:
    #   E_liberty_internal = E_switching - E_hidden
    # We don't run a fresh hidden sim — we reuse the already-characterized
    # hidden_power_internal arc that matches our before/after Q state.
    # Pairing convention:
    #   Q_rise (Q: 0 -> 1)        : subtract Q=0 hidden  (starting state)
    #   Q_fall (Q: 1 -> 0)        : subtract Q=1 hidden  (starting state)
    #   Q_RESET_B_fall (Q: 1 -> 0): subtract Q=1 hidden  (starting state)
    # ----------------------------------------------------------------------
    cell_dir = RESULTS_DIR / cell_config["name"]
    hidden_pair = _resolve_hidden_pair(arc_name)
    # The hidden energy subtracted must match the active arc's direction:
    # a Q-fall switching arc subtracts the hidden FALL energy, Q-rise the
    # hidden RISE. (When subtraction is disabled this has no effect.)
    active_dir = arc_config.get(
        "expected_output_transition",
        arc_config.get("data_transition", "rise"),
    )
    hidden_dir = "fall" if active_dir == "fall" else "rise"
    hidden_by_slew = _load_hidden_energy(
        cell_dir, hidden_pair, temp_str, vdd, direction=hidden_dir
    )
    if hidden_by_slew is None:
        print(f"  [INTERNAL] No hidden pair '{hidden_pair}' found; "
              f"skipping E_hidden subtraction.")

    out_dir = cell_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) Detailed (full provenance) — renamed to avoid colliding with legacy.
    detailed_path = out_dir / f"{arc_name}_dynamic_detailed.json"
    with open(detailed_path, "w", encoding="utf-8") as fobj:
        json.dump(
            {temp_str: {vdd: {arc_name: by_load}}},
            fobj, indent=2,
        )
    print(f"  Saved (detailed): {detailed_path}")

    # 2) Legacy lib-compatible: {T:{V:{load:{slew: pJ}}}}, no arc_name level,
    #    value in pJ instead of fJ.
    legacy_loads = {}
    hidden_loads = {}  # for the parity _hidden_energy_table.json file
    for load_str, slew_dict in by_load.items():
        if not isinstance(slew_dict, dict):
            continue
        legacy_slews = {}
        hidden_slews = {}
        for slew_str, slew_data in slew_dict.items():
            if isinstance(slew_data, dict):
                e_switch_fj = slew_data.get("e_internal_fj")
            else:
                e_switch_fj = slew_data
            if (e_switch_fj is None
                    or (isinstance(e_switch_fj, float)
                        and np.isnan(e_switch_fj))):
                e_switch_pj = 0.0
            else:
                e_switch_pj = float(e_switch_fj) / 1000.0  # fJ -> pJ

            # Hidden subtraction (disabled by default — Liberate keeps hidden
            # energy in separate input-pin passive_power tables, not subtracted
            # from the output internal_power. Enable only if your downstream
            # flow expects pre-subtracted output power.)
            sim_cfg = arc_config.get("_sim_cfg", {})
            sub_hidden = sim_cfg.get("power_internal_subtract_hidden", False)
            e_hidden_pj = 0.0
            if sub_hidden and hidden_by_slew is not None:
                e_hidden_fj = _hidden_lookup(hidden_by_slew, slew_str)
                if (e_hidden_fj is not None
                        and not (isinstance(e_hidden_fj, float)
                                 and np.isnan(e_hidden_fj))):
                    e_hidden_pj = float(e_hidden_fj) / 1000.0

            e_internal_pj = max(0.0, e_switch_pj - e_hidden_pj)

            legacy_slews[str(slew_str)] = e_internal_pj
            hidden_slews[str(slew_str)] = e_hidden_pj
        legacy_loads[str(load_str)] = legacy_slews
        hidden_loads[str(load_str)] = hidden_slews

    legacy_path = out_dir / f"{arc_name}_dynamic_energy_table.json"
    with open(legacy_path, "w", encoding="utf-8") as fobj:
        json.dump(
            {temp_str: {vdd: legacy_loads}},
            fobj, indent=4,
        )
    print(f"  Saved (legacy): {legacy_path}")

    # Parity with monolith: write the subtracted hidden component so the
    # file list matches what generate_lib (debug) and external tools expect.
    hidden_table_path = out_dir / f"{arc_name}_hidden_energy_table.json"
    with open(hidden_table_path, "w", encoding="utf-8") as fobj:
        json.dump(
            {temp_str: {vdd: hidden_loads}},
            fobj, indent=4,
        )
    print(f"  Saved (hidden parity): {hidden_table_path}")
