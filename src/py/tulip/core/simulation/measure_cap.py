"""Input-capacitance measurement by charge integration."""


import numpy as np
from ..measurements import find_crossing_time
from ..netlist.prop_delay import CAP_SETTLE_NS
from .vectors import (
    find_current_branch,
    get_vector_safe,
    is_active_low_async_pin,
)


def _ramp_ns(sim_cfg):
    """cap_ramp_ns from a sim_cfg dict, defaulting to 2.0."""
    try:
        val = float((sim_cfg or {}).get("cap_ramp_ns", 2.0))
    except (TypeError, ValueError):
        return 2.0
    return val if val > 0 else 2.0


def measure_input_capacitance(plot, arc_config, vdd_numeric, delta_ns):
    """
    Measure input capacitance using charge integration.

    Returns:
        dict with capacitance, rise_cap, fall_cap
    """
    input_pin = arc_config.get("data_signal", "A")
    time_vec = get_vector_safe(plot, 'time')
    v_in = get_vector_safe(plot, input_pin)

    # Find current vector
    i_in_name = find_current_branch(plot, input_pin)

    if i_in_name is None or v_in is None or time_vec is None:
        return {
            "delta_ns": delta_ns, "capacitance": np.nan,
            "rise_cap": np.nan, "fall_cap": np.nan
        }

    i_in = np.asarray(plot[i_in_name].to_waveform())

    # Check if active-low async pin
    is_active_low = is_active_low_async_pin(input_pin)
    # Measure rise and fall capacitance
    from_edge = arc_config.get("_sim_cfg", {}).get(
        "cap_window_from_edge", False
    )
    c_rise_pf, c_fall_pf = _measure_rise_fall_capacitance(
        time_vec, v_in, i_in, vdd_numeric, is_active_low,
        from_edge, arc_config.get("_sim_cfg", {})
    )
    c_avg_pf = np.nanmean([c_rise_pf, c_fall_pf])
    return {
        "delta_ns": delta_ns,
        "capacitance": c_avg_pf,
        "rise_cap": c_rise_pf,
        "fall_cap": c_fall_pf
    }


def _measure_cap_transition(time_vec, v_in, i_in, t_start, v_end,
                            direction):
    """Charge from the start of the driven edge to the v_end crossing.

    Liberate integrates from the start of its own ramp (icap_t1 ~ 1e-24) to
    the 50% crossing and divides by 0.5*VDD. Anchoring t_start to the known
    edge time reproduces that. The old 2%/98% trim cut ~4% of the charge
    while still dividing by 0.5*VDD, so it read low.

    A voltage crossing cannot be used for the start: CAP_SETTLE_NS holds the
    pin at its initial level for several ns, so "crossing v_low" has no
    well-defined instant. With a predriver the pin also lags the stimulus by
    the buffer delay -- the current before the pin moves is ~0, so starting
    at the stimulus edge costs nothing.
    """
    t_end = find_crossing_time(time_vec, v_in, v_end, direction, t_start)
    if t_end is None or t_end <= t_start:
        return np.nan
    mask = (time_vec >= t_start) & (time_vec <= t_end)
    return np.trapz(i_in[mask], time_vec[mask])


def _measure_cap_transition_trim(time_vec, v_in, i_in, v_start, v_end):
    """Historical 2%/98% window. Kept so runs 0 and 2 stay reproducible."""
    direction = 'fall' if v_start > v_end else 'rise'
    settle = 1e-12
    t_start = find_crossing_time(
        time_vec, v_in, v_start, direction, settle
    )
    t_end = find_crossing_time(
        time_vec, v_in, v_end, direction, settle
    )
    if t_start is None or t_end is None:
        return np.nan
    mask = (time_vec >= t_start) & (time_vec <= t_end)
    return np.trapz(i_in[mask], time_vec[mask])


def _measure_rise_fall_capacitance(time_vec, v_in, i_in,
                                   vdd_numeric, is_active_low,
                                   from_edge=False, arc_cfg=None):
    """Measure rise and fall capacitance.

    Returns:
        tuple: (c_rise_pf, c_fall_pf)
    """
    v_low = vdd_numeric * 0.02
    v_mid = vdd_numeric * 0.50
    v_high = vdd_numeric * 0.98
    delta_v = vdd_numeric * 0.5
    if from_edge:
        # The stimulus holds for CAP_SETTLE_NS, ramps over 2 ns, holds 10 ns,
        # then returns. First edge starts at CAP_SETTLE_NS, second 12 ns later.
        ramp = _ramp_ns(arc_cfg)
        first = CAP_SETTLE_NS * 1e-9
        second = (CAP_SETTLE_NS + ramp + 10.0) * 1e-9
        if is_active_low:
            q_fall = _measure_cap_transition(
                time_vec, v_in, i_in, first, v_mid, 'fall')
            q_rise = _measure_cap_transition(
                time_vec, v_in, i_in, second, v_mid, 'rise')
        else:
            q_rise = _measure_cap_transition(
                time_vec, v_in, i_in, first, v_mid, 'rise')
            q_fall = _measure_cap_transition(
                time_vec, v_in, i_in, second, v_mid, 'fall')
    elif is_active_low:
        q_fall = _measure_cap_transition_trim(
            time_vec, v_in, i_in, v_high, v_mid)
        q_rise = _measure_cap_transition_trim(
            time_vec, v_in, i_in, v_low, v_mid)
    else:
        q_rise = _measure_cap_transition_trim(
            time_vec, v_in, i_in, v_low, v_mid)
        q_fall = _measure_cap_transition_trim(
            time_vec, v_in, i_in, v_high, v_mid)
    c_rise_pf = (
        abs(q_rise) / delta_v * 1e12
        if not np.isnan(q_rise)
        else np.nan
    )
    c_fall_pf = (
        abs(q_fall) / delta_v * 1e12
        if not np.isnan(q_fall)
        else np.nan
    )
    return c_rise_pf, c_fall_pf
