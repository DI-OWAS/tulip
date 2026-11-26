"""Plot vector lookup helpers, branch current resolution, and
the shared failure result shape."""

import numpy as np


def get_vector_safe(plot, name):
    plot_lower = {str(k).lower(): k for k in plot.keys()}
    name_lower = name.lower()
    v_name_lower = f'v({name_lower})'
    if v_name_lower in plot_lower:
        return np.asarray(plot[plot_lower[v_name_lower]].to_waveform())
    if name_lower in plot_lower:
        return np.asarray(plot[plot_lower[name_lower]].to_waveform())
    return None


def plot_lookup(plot):
    return {str(k).lower(): k for k in plot.keys()}


def vec_lower(plot, name, lookup=None):
    """Look up a vector by node name. Returns numpy array or None."""
    lval = lookup if lookup is not None else plot_lookup(plot)
    nlow = name.lower()
    for cand in (f"v({nlow})", nlow):
        if cand in lval:
            return np.asarray(plot[lval[cand]].to_waveform())
    return None


def branch_current(plot, source_name, lookup=None):
    """
    Look up branch current of a voltage source. NgSpice uses
    'v_<name>#branch' or 'v<name>#branch' depending on naming.
    """
    lval = lookup if lookup is not None else plot_lookup(plot)
    nlow = source_name.lower()
    for cand in (f"v_{nlow}#branch", f"v{nlow}#branch"):
        if cand in lval:
            return np.asarray(plot[lval[cand]].to_waveform())
    return None


def find_current_branch(plot, input_pin):
    """Find current branch name for input pin."""
    for key in plot.keys():
        if f'v_{input_pin.lower()}#branch' in str(key).lower():
            return key
    return None


def is_active_low_async_pin(input_pin):
    """Check if pin is active-low async (RESET_B, SET_B, etc.)."""
    input_upper = input_pin.upper()
    return any(
        x in input_upper for x in ["RESET", "SET", "CLR", "PRE", "RST"]
    )


def failed_result(delta_ns):
    """Return a result dict with all NaN values."""
    return {
        "delta_ns": delta_ns,
        "t_prop": np.nan,
        "t_tran": np.nan,
        "leakage_power": np.nan,
        "dynamic_energy": np.nan
    }
