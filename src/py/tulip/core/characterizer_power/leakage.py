"""Leakage-power characterization and per-state leakage lookup."""

from pathlib import Path
import json
import multiprocessing
import numpy as np
from tqdm import tqdm
from ..simulation import run_single_simulation
from .states import (
    _resolve_when_to_state,
)

RESULTS_DIR = Path("results")
NUM_PROCESSES = 8


def _characterize_leakage(corner_config, sweep_params, cli_args, tables):
    arc_config = corner_config["arc_config"]
    cell_config = corner_config["cell_config"]
    spice_template = corner_config["spice_template"]
    temp_str = corner_config["temp_str"]
    vdd = corner_config["vdd"]
    arc_name = corner_config["arc_name"]

    # Inject function_mapping from cell_config if missing
    if ("function_mapping" not in arc_config
            and "function_mapping" in cell_config):
        arc_config = dict(arc_config)
        arc_config["function_mapping"] = cell_config["function_mapping"]

    states = arc_config.get("states", [])
    if not states:
        # Single-state degenerate case
        states = [{"when": ""}]

    # Use minimum slew for leakage (it's a DC measurement, slew doesn't matter)
    slew_scale = sweep_params.get("slew_scale_factor", 1.0)
    data_slew_ns = sweep_params["data_slew_ns_list"][0]
    spice_slew = data_slew_ns * slew_scale
    clock_slew_str = f"{spice_slew}n"
    data_slew_str = f"{spice_slew}n"
    load_str = "0.001"

    print(
        f"\n--- Characterizing Leakage: {cell_config['name']}/{arc_name} ---")
    print(f"  States: {len(states)}")

    leak_results = {}

    # Build one task per state. Each state gets its own arc_config copy
    # with _state set so the netlist generator picks it up.
    tasks = []
    for idx, state_def in enumerate(states):
        state_dict = state_def.get("state")
        if state_dict is None:
            state_dict = _resolve_when_to_state(state_def.get("when", ""))
        state_name = state_def.get("name", f"state_{idx:02d}")

        per_state_arc = dict(arc_config)
        per_state_arc["_state"] = state_dict
        per_state_arc["_state_name"] = state_name
        # Pass sim_cfg through so the netlist generator can read settle window
        per_state_arc["_sim_cfg"] = arc_config.get("_sim_cfg", {})

        task = (
            temp_str, vdd, clock_slew_str, data_slew_str, load_str, 0.0,
            cell_config, per_state_arc, spice_template, True,
        )
        tasks.append((state_name, state_dict, task))

    # Run states in parallel (each state is independent).
    n_proc = min(sweep_params.get("num_processes", NUM_PROCESSES), len(tasks))
    print(f"  Running {len(tasks)} states with {n_proc} workers...")
    if n_proc > 1:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(processes=n_proc) as pool:
            results = list(tqdm(
                pool.imap(run_single_simulation, [t[2] for t in tasks]),
                total=len(tasks), desc="Leakage states",
            ))
    else:
        results = [run_single_simulation(t[2]) for t in tasks]

    for (state_name, state_dict, _), result in zip(tasks, results):
        leak_w = result.get("leakage_power", float("nan"))
        v_q = result.get("_v_q_settled")
        leak_results[state_name] = {
            "p_leak_uw": (leak_w * 1e6
                          if leak_w is not None and not np.isnan(leak_w)
                          else float("nan")),
            "i_leak_a": (leak_w / float(vdd)
                         if leak_w is not None and not np.isnan(leak_w)
                         else float("nan")),
            "state": state_dict,
            "v_q_settled": v_q,
        }

    # Save TWO files:
    #   1) Detailed per-arc JSON (metadata for debugging + cross-arc
    #      lookup)
    #   2) Legacy "lib-compatible" JSON (bare scalar uW, consumed by
    #      generate_lib.py)
    out_dir = RESULTS_DIR / cell_config["name"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) Detailed (renamed to avoid collision with legacy filename)
    detailed_path = out_dir / f"{arc_name}_leakage_detailed.json"
    with open(detailed_path, "w", encoding="utf-8") as fobj:
        json.dump(
            {temp_str: {vdd: leak_results}},
            fobj, indent=2,
        )
    print(f"  Saved (detailed): {detailed_path}")

    # 2) Legacy: bare-scalar uW per state (lib-compatible)
    legacy_states = {}
    for state_name, state_data in leak_results.items():
        # state_name is "state_NN", legacy uses "state_N" (no zero-padding).
        # generate_lib parses both via int(split('_')[1]), so leave as-is.
        p_uw = state_data.get("p_leak_uw")
        if p_uw is None or (isinstance(p_uw, float) and np.isnan(p_uw)):
            p_uw = 0.0
        legacy_states[state_name] = float(p_uw)

    legacy_path = out_dir / f"{arc_name}_leakage_power_table.json"
    with open(legacy_path, "w", encoding="utf-8") as fobj:
        json.dump(
            {temp_str: {vdd: legacy_states}},
            fobj, indent=4,
        )
    print(f"  Saved (legacy): {legacy_path}")


def _maybe_lookup_leakage(arc_config, tables, temp_str, vdd):
    """
    Try to find the matching leakage current for this arc's state.

    Two paths:
      1. arc_config['leakage_state_ref'] = "state_03"  ->  look up by name
      2. Match the arc's 'when' clause against any leakage state's 'when'

    Returns the leakage current in amps, or None if not found.
    """
    # `tables` is reset per-arc by characterize_arc(), so cross-arc state
    # has to come from disk. Search RESULTS_DIR/<cell>/ for any
    # *_leakage_power_table.json written by an earlier leakage arc.
    cell_name = arc_config.get("_cell_name")
    if cell_name is None:
        if not RESULTS_DIR.exists():
            return None
        subdirs = [p for p in RESULTS_DIR.iterdir() if p.is_dir()]
        if len(subdirs) != 1:
            return None
        cell_dir = subdirs[0]
    else:
        cell_dir = RESULTS_DIR / cell_name
    if not cell_dir.exists():
        return None

    # Prefer the detailed file (has full state metadata); fall back to legacy.
    leakage_files = list(cell_dir.glob("*_leakage_detailed.json"))
    if not leakage_files:
        leakage_files = list(cell_dir.glob("*_leakage_power_table.json"))
    if not leakage_files:
        return None

    try:
        vdd_f = float(vdd)
    except (TypeError, ValueError):
        vdd_f = 1.0

    leakage_arcs = {}
    for leak_file in leakage_files:
        try:
            with open(leak_file, "r", encoding="utf-8") as fobj:
                data = json.load(fobj)
        except (OSError, ValueError):
            continue
        states = data.get(temp_str, {}).get(str(vdd), {})
        if not states:
            continue
        # Normalize: detailed file has dict per state; legacy has bare scalar.
        # In both cases, expose an "i_leak_a" key for downstream code.
        normalized = {}
        for state_name, state_value in states.items():
            if isinstance(state_value, dict):
                # Detailed format
                normalized[state_name] = state_value
            else:
                # Legacy bare-scalar uW; reconstruct minimal dict
                try:
                    p_uw = float(state_value)
                except (TypeError, ValueError):
                    p_uw = 0.0
                i_a = (p_uw * 1e-6) / vdd_f if vdd_f > 0 else 0.0
                normalized[state_name] = {
                    "p_leak_uw": p_uw,
                    "i_leak_a": i_a,
                    "state": {},
                }
        leakage_arcs[leak_file.stem] = normalized

    if not leakage_arcs:
        return None

    state_ref = arc_config.get("leakage_state_ref")
    if state_ref:
        for states in leakage_arcs.values():
            if state_ref in states:
                return states[state_ref].get("i_leak_a")
        return None

    # Otherwise: try to match the arc's 'when' clause to a leakage state's
    # state dict. The arc's 'when' typically describes the state JUST
    # BEFORE the trigger edge — so we match on the static_pins side only.
    # State = all inputs AFTER the edge + output BEFORE the edge. Exact
    # match only; no match -> average over all states (cell leakage).
    target = {p: (1 if str(v).upper() == "VDD" else 0)
              for p, v in (arc_config.get("static_pins") or {}).items()}
    target.update(_resolve_when_to_state(arc_config.get("when", "")))
    trig = arc_config.get("data_signal")
    if trig:
        _tr = arc_config.get("data_transition") or "rise"
        target[trig] = 1 if _tr == "rise" else 0
    out = arc_config.get("output_signal", "Q")
    exp_trans = arc_config.get("expected_output_transition")
    if exp_trans in ("rise", "fall"):
        target[out] = 0 if exp_trans == "rise" else 1

    all_i = []
    for states in leakage_arcs.values():
        for state_data in states.values():
            state = state_data.get("state", {})
            i_a = state_data.get("i_leak_a")
            if i_a is not None:
                all_i.append(i_a)
            if (isinstance(state, dict) and state
                    and all(state.get(k) == v for k, v in target.items())):
                return i_a
    return float(np.mean(all_i)) if all_i else None
