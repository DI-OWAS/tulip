"""Hidden internal-power characterization and energy lookup."""

from pathlib import Path
import json
import multiprocessing
import numpy as np
from ..ic_precompute import precompute_settle_state
from ..simulation import run_single_simulation
from .leakage import (
    _maybe_lookup_leakage,
)
from ..netlist.power import _infer_hidden_q

RESULTS_DIR = Path("results")
NUM_PROCESSES = 8


def _characterize_hidden(corner_config, sweep_params, cli_args, tables):
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

    slew_scale = sweep_params.get("slew_scale_factor", 1.0)
    data_slew_ns_list = arc_config.get("power_slews_ns",
                                       sweep_params["data_slew_ns_list"])
    load_str = str(arc_config.get("power_load_pf", "0.001"))

    print(f"\n--- Characterizing Hidden Power: "
          f"{cell_config['name']}/{arc_name} ---")
    print(f"  Slews: {data_slew_ns_list}")

    hidden_config = {
        'temp_str': temp_str,
        'vdd': vdd,
        'data_slew_ns_list': data_slew_ns_list,
        'slew_scale_factor': slew_scale,
        'load_str': load_str,
        'cell_config': cell_config,
        'arc_config': arc_config,
        'spice_template': spice_template,
        'directions': tuple(arc_config.get("directions", ("rise", "fall"))),
        'ic_settle_ns': arc_config.get("_sim_cfg", {}).get(
            "ic_settle_window_ns", 500.0),
        # Hidden sims are 10 us at 10 ps (~1.5 GB each): own, smaller pool.
        'num_processes': int(
            sweep_params["get_cfg"]("hidden_num_processes", 4))
        if sweep_params.get("get_cfg") else 4,
    }

    # Build simulation tasks (one per slew x direction)
    tasks = _build_hidden_tasks(hidden_config, tables)

    # Run simulations in parallel
    n_proc = min(hidden_config['num_processes'], len(tasks))
    print(f"    -> Running {len(tasks)} hidden sims in parallel "
          f"({n_proc} processes)...")
    with multiprocessing.get_context("spawn").Pool(processes=n_proc) as pool:
        results = pool.map(run_single_simulation, [t['task'] for t in tasks])

    # Store results
    by_slew = _init_hidden_table(data_slew_ns_list)
    for task_info, result in zip(tasks, results):
        _store_hidden_result(task_info, result, by_slew)

    out_dir = RESULTS_DIR / cell_config["name"]
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) Detailed per-arc JSON (existing)
    out_path = out_dir / f"{arc_name}_hidden_energy_table.json"
    with open(out_path, "w", encoding="utf-8") as fobj:
        json.dump(
            {temp_str: {vdd: {arc_name: by_slew}}},
            fobj, indent=2,
        )
    print(f"  Saved (detailed): {out_path}")

    # 2) Legacy passive_power JSON (consumed by generate_lib.py).
    # Convention: store ENERGY in pJ under keys called "rise_power" /
    # "fall_power". For true hidden arcs (Q does not toggle) there is no
    # input-transition direction split, so we put the same value in both.
    rise_pj = {}
    fall_pj = {}
    for slew_ns, slew_data in by_slew.items():
        if isinstance(slew_data, dict):
            e_rise_fj = slew_data.get("e_rise_fj")
            e_fall_fj = slew_data.get("e_fall_fj")
        else:
            e_rise_fj = e_fall_fj = slew_data

        def _to_pj(e_fj):
            if e_fj is None or (isinstance(e_fj, float) and np.isnan(e_fj)):
                return 0.0
            return float(e_fj) / 1000.0  # fJ -> pJ

        if "rise" in hidden_config['directions']:
            rise_pj[str(slew_ns)] = _to_pj(e_rise_fj)
        if "fall" in hidden_config['directions']:
            fall_pj[str(slew_ns)] = _to_pj(e_fall_fj)

    legacy_path = out_dir / f"{arc_name}_passive_power.json"
    with open(legacy_path, "w", encoding="utf-8") as fobj:
        json.dump(
            {temp_str: {vdd: {"rise_power": rise_pj, "fall_power": fall_pj}}},
            fobj, indent=4,
        )
    print(f"  Saved (legacy): {legacy_path}")


def _build_hidden_tasks(hidden_config, tables):
    """Build list of simulation tasks for all slew values and directions."""
    arc_config = hidden_config['arc_config']
    cell_config = hidden_config['cell_config']
    spice_template = hidden_config['spice_template']
    temp_str = hidden_config['temp_str']
    vdd = hidden_config['vdd']
    tasks = []

    for slew_ns in hidden_config['data_slew_ns_list']:
        spice_slew = slew_ns * hidden_config['slew_scale_factor']
        clock_slew_str = f"{spice_slew}n"
        data_slew_str = f"{spice_slew}n"

        # Measure both toggle directions separately: rise and fall passive
        # energy differ (internal clock/latch nodes charge asymmetrically),
        # so a single sim copied into both slots is wrong.
        for direction in hidden_config['directions']:
            per_arc = dict(arc_config)
            per_arc["_sim_cfg"] = arc_config.get("_sim_cfg", {})
            # Mark arc type so the netlist generator picks the right stimuli
            per_arc["type"] = "hidden_power_internal"
            per_arc["data_transition"] = direction
            # Leakage state = inputs after THIS edge + Q as held.
            leak_cfg = dict(per_arc)
            when_raw = str(per_arc.get("when", "")).strip()
            # Q term only for sequential outputs; a combinational output
            # (Y, X) has no Q, and a Q in the lookup would never match a
            # leakage state, falling back to the cell average.
            out_sig = str(per_arc.get("output_signal", "Q")).upper()
            if "Q" not in when_raw and out_sig.startswith("Q"):
                q_term = "Q" if _infer_hidden_q(per_arc, {}) else "!Q"
                leak_cfg["when"] = (f"{when_raw} & {q_term}" if when_raw
                                    else q_term)
            leak_dir = _maybe_lookup_leakage(leak_cfg, tables, temp_str, vdd)
            if leak_dir is not None and not np.isnan(leak_dir):
                per_arc["_leak_current_a"] = leak_dir
                if slew_ns == hidden_config['data_slew_ns_list'][0]:
                    print(f"    [{direction}] I_leak = "
                          f"{leak_dir * 1e9:.3f} nA")

            # Liberate-style 2-pass: settle the held state for THIS direction
            # (trigger pin held at its pre-edge level), then the measurement
            # sim loads those ICs and applies a clean single ramp instead of
            # an in-sim preload pulse. Cached by state_key, so rise/fall reuse
            # the same settle when the internal state matches.
            ic_dict = precompute_settle_state(
                spice_template, cell_config, per_arc, temp_str, str(vdd),
                settle_ns=hidden_config['ic_settle_ns'],
            )
            if ic_dict:
                per_arc["_ic_dict"] = ic_dict

            task = (
                temp_str, vdd, clock_slew_str, data_slew_str,
                hidden_config['load_str'], 0.0,
                cell_config, per_arc, spice_template, True,
            )
            tasks.append({
                'data_slew_ns': slew_ns,
                'direction': direction,
                'task': task,
            })

    return tasks


def _init_hidden_table(data_slew_ns_list):
    """Initialize per-slew entries (NaN for directions not simulated)."""
    return {
        slew_ns: {
            "e_rise_fj": float("nan"),
            "e_fall_fj": float("nan"),
            # Back-compat key consumed by _load_hidden_energy when no
            # direction is requested; keep it as the rise value.
            "e_internal_fj": float("nan"),
            "q_vdd_C": None,
            "q_pin_C": None,
        }
        for slew_ns in data_slew_ns_list
    }


def _store_hidden_result(task_info, result, by_slew):
    """Store one simulation result into the per-slew table."""
    entry = by_slew[task_info['data_slew_ns']]
    direction = task_info['direction']
    e_j = result.get("dynamic_energy", float("nan"))
    e_fj = (e_j * 1e15
            if e_j is not None and not np.isnan(e_j)
            else float("nan"))
    entry[f"e_{direction}_fj"] = e_fj
    if direction == "rise":
        entry["e_internal_fj"] = e_fj
        entry["q_vdd_C"] = result.get("_q_vdd")
        entry["q_pin_C"] = result.get("_q_pin_total")


def _load_hidden_energy(cell_dir, hidden_arc_name, temp_str, vdd,
                        direction=None):
    """Read the detailed hidden_energy_table.json for `hidden_arc_name` and
    return the {slew_str: e_fj} dict, or None on miss.

    `direction` selects which toggle direction's energy to return:
      'rise' -> e_rise_fj, 'fall' -> e_fall_fj, None -> e_internal_fj
    (back-compat, == rise). Tries detailed file first; falls back to legacy
    passive_power.json (energy in pJ under rise_power/fall_power).
    """
    if not hidden_arc_name:
        return None

    if direction == "rise":
        det_key = "e_rise_fj"
    elif direction == "fall":
        det_key = "e_fall_fj"
    else:
        det_key = "e_internal_fj"

    # Detailed file (preferred — already in fJ)
    detailed = cell_dir / f"{hidden_arc_name}_hidden_energy_table.json"
    if detailed.exists():
        try:
            with open(detailed, "r", encoding="utf-8") as fobj:
                data = json.load(fobj)
        except (OSError, ValueError):
            data = None
        if data:
            slew_block = (data.get(temp_str, {})
                          .get(str(vdd), {})
                          .get(hidden_arc_name, {}))
            if slew_block:
                # Normalize to {slew_str: e_fj_float} for the requested dir
                out = {}
                for slew_key, slew_val in slew_block.items():
                    if isinstance(slew_val, dict):
                        val = slew_val.get(det_key)
                        if val is None:
                            val = slew_val.get("e_internal_fj")
                        out[str(slew_key)] = val
                    else:
                        out[str(slew_key)] = slew_val
                return out

    # Legacy fallback: passive_power.json (in pJ, rise_power/fall_power keys)
    legacy = cell_dir / f"{hidden_arc_name}_passive_power.json"
    if legacy.exists():
        try:
            with open(legacy, "r", encoding="utf-8") as fobj:
                data = json.load(fobj)
        except (OSError, ValueError):
            return None
        corner = data.get(temp_str, {}).get(str(vdd), {})
        rise = corner.get("rise_power", {})
        fall = corner.get("fall_power", {})
        if not rise and not fall:
            return None
        out = {}
        all_slews = set(rise.keys()) | set(fall.keys())
        for slew_key in all_slews:
            rise_e = rise.get(slew_key, 0.0)
            fall_e = fall.get(slew_key, rise_e if rise_e else 0.0)
            if direction == "rise":
                val_pj = float(rise_e)
            elif direction == "fall":
                val_pj = float(fall_e)
            else:
                val_pj = (float(rise_e) + float(fall_e)) / 2.0
            out[str(slew_key)] = val_pj * 1000.0  # pJ -> fJ to match detailed
        return out

    return None


def _hidden_lookup(hidden_by_slew, slew_str):
    """Look up hidden energy for a slew with float-tolerant matching."""
    if hidden_by_slew is None:
        return None
    if slew_str in hidden_by_slew:
        return hidden_by_slew[slew_str]
    try:
        target = float(slew_str)
    except (TypeError, ValueError):
        return None
    for k, val in hidden_by_slew.items():
        try:
            if abs(float(k) - target) < 1e-9:
                return val
        except (TypeError, ValueError):
            continue
    return None
