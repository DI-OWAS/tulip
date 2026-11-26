"""Propagation delay, reference-edge and output-slew
measurement, including the tri-state disable knee reader."""

import numpy as np
from ..measurements import find_crossing_time
from .vectors import (
    get_vector_safe,
)


def _get_prop_delay_search_time(arc_config, sa_settle, sa_comb):
    """Determine search_after time for propagation delay reference."""
    data_signal_upper = arc_config["data_signal"].upper()
    is_async_arc = any(
        x in data_signal_upper
        for x in ["RESET", "SET", "CLR", "PRE", "RST"]
    )
    is_gate_arc = "GATE" in data_signal_upper
    func_map = arc_config.get("function_mapping", {})
    is_ff_async = is_async_arc and (
        func_map.get("reset_b") or func_map.get("set_b")
    )
    has_preload = bool(arc_config.get("preload_pins"))
    input_trans = arc_config.get('data_transition', '')
    expected_trans = arc_config.get('expected_output_transition', '')
    needs_settling = (
        has_preload
        or (input_trans == "fall")
        or (expected_trans == "fall")
    )
    return (
        sa_settle
        if (is_ff_async or is_gate_arc or needs_settling)
        else sa_comb
    )


def measure_reference_time(arc_type, arc_config, plot, vdd_numeric):
    sim_cfg = arc_config.get('_sim_cfg', {})
    delay_t = sim_cfg.get("delay_threshold_pct", 50) / 100.0
    sa_ref_seq = sim_cfg.get("search_after_ref_seq_ns", 14.5) * 1e-9
    sa_comb = sim_cfg.get("search_after_comb_ns", 4.5) * 1e-9
    sa_settle = sim_cfg.get("search_after_settling_ns", 9.5) * 1e-9

    if arc_type in ["setup", "hold", "recovery", "removal"]:
        v_ref = get_vector_safe(plot, arc_config["related_pin"])
        if v_ref is not None:
            time_vec = get_vector_safe(plot, 'time')
            return find_crossing_time(
                time_vec, v_ref, vdd_numeric * delay_t, 'rise',
                search_after=sa_ref_seq
            )
    elif arc_type in ["setup_latch", "hold_latch"]:
        gate_sig = arc_config["gate_signal"]
        v_ref = get_vector_safe(plot, gate_sig)
        if v_ref is not None:
            time_vec = get_vector_safe(plot, 'time')
            close_edge = (
                'rise' if gate_sig.upper().endswith("_N") else 'fall'
            )
            return find_crossing_time(
                time_vec, v_ref, vdd_numeric * delay_t, close_edge,
                search_after=sa_ref_seq
            )
    elif arc_type == "propagation_delay":
        v_ref = get_vector_safe(plot, arc_config["data_signal"])
        if v_ref is not None:
            search_time = _get_prop_delay_search_time(
                arc_config, sa_settle, sa_comb
            )
            time_vec = get_vector_safe(plot, 'time')
            return find_crossing_time(
                time_vec, v_ref, vdd_numeric * delay_t,
                arc_config["data_transition"], search_after=search_time
            )
    return None


def _get_output_search_time(arc_type, arc_config):
    """Determine search start time for output measurement based on arc type."""
    if arc_type in ["setup_latch", "hold_latch"]:
        return 8.5e-9 if arc_type == "hold_latch" else 9.0e-9

    if arc_type in ["propagation_delay", "internal_power"]:
        sim_cfg = arc_config.get('_sim_cfg', {})
        sa_comb = sim_cfg.get("search_after_comb_ns", 4.5) * 1e-9
        sa_settle = sim_cfg.get("search_after_settling_ns", 9.5) * 1e-9
        data_signal_upper = arc_config.get("data_signal", "").upper()
        is_gate_arc = "GATE" in data_signal_upper
        has_preload = bool(arc_config.get("preload_pins"))
        input_trans = arc_config.get('data_transition', '')
        expected_trans = arc_config.get('expected_output_transition', '')
        needs_settling = (
            has_preload or input_trans == "fall" or expected_trans == "fall"
        )
        return sa_settle if (is_gate_arc or needs_settling) else sa_comb
    if arc_type in ["recovery", "removal", "setup", "hold"]:
        sim_cfg = arc_config.get('_sim_cfg', {})
        return sim_cfg.get("search_after_seq_ns", 14.0) * 1e-9
    return 0.0


def _get_timing_thresholds(arc_config):
    """Extract delay and slew thresholds from sim config."""
    sim_cfg = arc_config.get('_sim_cfg', {})
    delay_t = sim_cfg.get("delay_threshold_pct", 50) / 100.0
    slew_lo = sim_cfg.get("slew_lower_threshold_pct", 20) / 100.0
    slew_hi = sim_cfg.get("slew_upper_threshold_pct", 80) / 100.0
    return delay_t, slew_lo, slew_hi


def _measure_disable_collapse(plot, arc_config, time_vec, t_ref):
    out = arc_config["output_signal"]
    want = f"v_sense_dut_{out.lower()}#branch"
    i_drv = None
    for vec_name in plot.keys():
        if str(vec_name).lower() == want:
            i_drv = get_vector_safe(plot, vec_name)
            break
    if i_drv is None:
        print(f"      [TSD_DEBUG] {want} not found")
        return np.nan, np.nan
    i_mag = np.abs(np.asarray(i_drv))
    idx0 = np.searchsorted(time_vec, t_ref)
    if idx0 >= len(i_mag) - 2:
        return np.nan, np.nan
    i_start = float(np.nanmedian(i_mag[max(0, idx0 - 20):idx0 + 1]))
    if not np.isfinite(i_start) or i_start < 1e-8:
        return np.nan, np.nan
    peak = idx0 + int(np.nanargmax(i_mag[idx0:]))
    knee_pct = float(arc_config.get("disable_i_knee_pct", 0.2))
    t_dis = find_crossing_time(
        time_vec, i_mag, knee_pct * i_start, "fall",
        search_after=float(time_vec[peak])
    )
    if t_dis is None:
        return np.nan, np.nan
    return t_dis - t_ref, np.nan


def measure_standard_timing(plot, arc_config, vdd_numeric, t_ref):
    """
    Measure standard propagation delay and transition time.
    Returns:
        tuple: (t_prop_sec, t_tran_sec)
    """
    time_vec = get_vector_safe(plot, 'time')
    v_q = get_vector_safe(plot, arc_config["output_signal"])
    if v_q is None or time_vec is None or t_ref is None:
        return np.nan, np.nan
    arc_type = arc_config.get("type")
    search_start = _get_output_search_time(arc_type, arc_config)
    thresholds = _get_timing_thresholds(arc_config)

    # Tri-state DISABLE arcs are measured from the driver-current collapse;
    # enable arcs fall through to the standard 50% path below.
    if arc_config.get("timing_type", "").startswith("three_state_disable"):
        return _measure_disable_collapse(plot, arc_config, time_vec, t_ref)

    t_q_50 = find_crossing_time(
        time_vec, v_q, vdd_numeric * thresholds[0],
        arc_config["expected_output_transition"],
        search_after=search_start
    )
    if t_q_50 is None:
        return np.nan, np.nan
    t_prop_sec = t_q_50 - t_ref
    t_q_10 = find_crossing_time(
        time_vec, v_q, vdd_numeric * thresholds[1],
        arc_config["expected_output_transition"],
        search_after=search_start
    )
    t_q_90 = find_crossing_time(
        time_vec, v_q, vdd_numeric * thresholds[2],
        arc_config["expected_output_transition"],
        search_after=search_start
    )
    t_tran_sec = abs(t_q_90 - t_q_10) if (t_q_10 and t_q_90) else np.nan
    return t_prop_sec, t_tran_sec
