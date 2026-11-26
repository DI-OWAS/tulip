"""Minimum-pulse-width measurement."""

import numpy as np
from ..measurements import find_crossing_time
from .vectors import (
    get_vector_safe,
)


def measure_mpw_timing(
    plot,
    arc_config,
    vdd_numeric,
    delta_ns,
    data_slew_str
):
    """Measure minimum pulse width timing."""
    # Get vectors
    time_vec = get_vector_safe(plot, 'time')
    v_pulsed = get_vector_safe(plot, arc_config["data_signal"])
    v_q = get_vector_safe(plot, arc_config["output_signal"])

    if v_pulsed is None or v_q is None or time_vec is None:
        return np.nan, np.nan

    # CHANGE: Add delta_ns and data_slew_str to arc_config for helpers
    arc_config_extended = dict(arc_config)
    arc_config_extended["delta_ns"] = delta_ns
    arc_config_extended["data_slew_str"] = data_slew_str

    # Find pulse edges
    pulse_edges = _find_mpw_pulse_edges(
        time_vec, v_pulsed, vdd_numeric, arc_config_extended
    )
    if pulse_edges is None:
        return np.nan, np.nan

    t_pulse_start, t_pulse_end = pulse_edges

    # Verify pulse formation - NOW 4 ARGS ✅
    if not _verify_mpw_pulse_formed(
        time_vec, v_pulsed, vdd_numeric, arc_config_extended
    ):
        return np.nan, np.nan
    t_ref = _get_mpw_reference_time(t_pulse_start, t_pulse_end, arc_config)
    t_prop_sec = _measure_mpw_output_delay(
        time_vec, v_q, vdd_numeric, t_ref, arc_config
    )
    if t_prop_sec is None:
        return np.nan, np.nan

    t_tran_sec = _measure_mpw_transition(
        time_vec, v_q, vdd_numeric, t_pulse_start, arc_config
    )

    return t_prop_sec, t_tran_sec


def _find_mpw_pulse_edges(time_vec, v_pulsed, vdd_numeric, arc_config):

    pulse_polarity = arc_config.get("pulse_polarity", "active_low")
    data_slew_str = arc_config.get("data_slew_str")

    if pulse_polarity == "active_low":
        edge1, edge2 = 'fall', 'rise'
    else:
        edge1, edge2 = 'rise', 'fall'

    # Find pulse start
    t_start = find_crossing_time(
        time_vec, v_pulsed, vdd_numeric * 0.5, edge1,
        search_after=49.5e-9
    )
    if t_start is None:
        return None

    # Parse slew value
    try:
        slew_val = float(data_slew_str.rstrip('n')) * 1e-9
    except (ValueError, AttributeError):
        slew_val = 10e-12

    # Find pulse end
    t_end = find_crossing_time(
        time_vec, v_pulsed, vdd_numeric * 0.5, edge2,
        search_after=t_start + (slew_val * 0.5)
    )
    if t_end is None:
        return None

    return t_start, t_end


def _verify_mpw_pulse_formed(time_vec, v_pulsed, vdd_numeric, arc_config):
    """
    Check if pulse reached required amplitude.

    Args:
        time_vec: Time vector
        v_pulsed: Pulsed signal voltage
        vdd_numeric: Supply voltage
        arc_config: Arc configuration (includes delta_ns, data_slew_str,
                    pulse_polarity, mpw_input_threshold)

    Returns:
        bool: True if pulse formed correctly
    """
    pulse_polarity = arc_config.get("pulse_polarity", "active_low")
    mpw_input_threshold = arc_config.get("mpw_input_threshold", 0.9)

    # Extract delta_ns and data_slew_str from arc_config
    delta_ns = arc_config.get("delta_ns")
    data_slew_str = arc_config.get("data_slew_str")

    # Parse slew
    try:
        slew_val = float(data_slew_str.rstrip('n')) * 1e-9
    except (ValueError, AttributeError):
        slew_val = 10e-12

    # Define search window
    requested_width_sec = delta_ns * 1e-9
    netlist_pulse_start = 50.0e-9
    search_end = netlist_pulse_start + requested_width_sec + slew_val + 1e-9
    search_window = (
        (time_vec >= netlist_pulse_start)
        & (time_vec <= search_end)
    )

    # Check peak voltage
    if pulse_polarity == "active_low":
        v_actual_peak = np.min(v_pulsed[search_window])
        threshold = vdd_numeric * (1.0 - mpw_input_threshold)
        return v_actual_peak < threshold

    v_actual_peak = np.max(v_pulsed[search_window])
    threshold = vdd_numeric * mpw_input_threshold
    return v_actual_peak > threshold


def _get_mpw_reference_time(t_pulse_start, t_pulse_end, arc_config):
    """Determine reference edge for MPW delay measurement."""
    pulsed_signal = arc_config.get("data_signal", "").upper()
    is_async_pin = any(
        x in pulsed_signal for x in ["RESET", "SET", "CLR", "PRE", "RST"]
    )

    if is_async_pin:
        return t_pulse_start

    pulse_polarity = arc_config.get("pulse_polarity", "active_low")
    return t_pulse_end if pulse_polarity == "active_low" else t_pulse_start


def _measure_mpw_output_delay(time_vec, v_q, vdd_numeric, t_ref, arc_config):
    """Measure output delay for MPW."""
    output_transition = arc_config.get("expected_output_transition", "rise")
    mpw_output_threshold = (
        0.05 if output_transition == "fall"
        else arc_config.get("mpw_output_threshold", 0.80)
    )
    t_q_thresh = find_crossing_time(
        time_vec, v_q, vdd_numeric * mpw_output_threshold,
        arc_config["expected_output_transition"],
        search_after=t_ref
    )

    if t_q_thresh is None:
        return None

    return t_q_thresh - t_ref


def _measure_mpw_transition(time_vec, v_q, vdd_numeric,
                            t_pulse_start, arc_config):
    """Measure MPW output transition time (10% to 90%)."""
    output_transition = arc_config.get("expected_output_transition", "rise")
    sim_cfg = arc_config.get('_sim_cfg', {})
    slew_lo = sim_cfg.get("slew_lower_threshold_pct", 20) / 100.0
    slew_hi = sim_cfg.get("slew_upper_threshold_pct", 80) / 100.0
    t_q_10 = find_crossing_time(
        time_vec, v_q, vdd_numeric * slew_lo,
        output_transition, search_after=t_pulse_start
    )
    t_q_90 = find_crossing_time(
        time_vec, v_q, vdd_numeric * slew_hi,
        output_transition, search_after=t_pulse_start
    )

    if t_q_10 and t_q_90:
        return abs(t_q_90 - t_q_10)
    return np.nan
