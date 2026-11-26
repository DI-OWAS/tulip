"""Netlist generation package.
"""

import re
try:  # optional:if power characterization is not built,this module is omitted
    from ..ic_precompute import build_ic_block
except ImportError:  # pragma: no cover
    build_ic_block = None
from .common import (
    apply_robust_options,
    apply_standard_replacements,
    add_series_resistors,
)
from .mpw import (
    generate_mpw_stimuli,
)
from .prop_delay import (
    CAP_SETTLE_NS,
    generate_input_cap,
    generate_prop_delay_stimuli,
)
from .recovery import (
    generate_recovery_removal,
)
from .stimuli_latch import (
    generate_latch_stimuli,
)
from .stimuli_seq import (
    generate_timing_arc_stimuli,
)

try:  # optional: timing-only builds omit this module
    from .power import (
        _generate_internal_ic_stimuli as generate_internal_ic_stimuli,
        generate_hidden_internal_stimuli,
        generate_leakage_stimuli,
    )
    HAS_POWER = True
except ImportError:
    HAS_POWER = False


def _cfg_ramp(sim_cfg):
    """cap_ramp_ns from a sim_cfg dict, defaulting to 2.0."""
    try:
        val = float((sim_cfg or {}).get("cap_ramp_ns", 2.0))
    except (TypeError, ValueError):
        return 2.0
    return val if val > 0 else 2.0


def _tran_directive(arc_type, arc_config, sim_cfg):
    """Return the .tran line for this arc type, or None to keep the
    template's own directive."""
    step = sim_cfg.get("sim_timestep_ps", 10)
    if arc_type in ("mpw", "input_capacitance"):
        if arc_type == "mpw":
            return f".tran {step}p 250n"
        stop = CAP_SETTLE_NS + 2 * _cfg_ramp(sim_cfg) + 22
        return f".tran 1p {stop:g}n uic"
    # Delay arcs measure one edge; they don't need the long power-settle
    # window (sim_window_ns exists for internal_power internal-node
    # settling.
    # default; per-cell/global 'delay_window_ns' overrides.
    windows = {
        "propagation_delay": (
            arc_config.get('_cell_delay_window_ns',
                           sim_cfg.get("delay_window_ns", 50)), True),
        "internal_power": (
            arc_config.get('_cell_sim_window_ns',
                           sim_cfg.get("sim_window_ns", 30)), True),
        "leakage_power": (
            sim_cfg.get("power_settle_window_ns", 20000), False),
        "hidden_power_internal": (
            sim_cfg.get("power_hidden_window_ns", 10000), False),
    }
    if arc_type not in windows:
        return None
    window, uic = windows[arc_type]
    if arc_type == "leakage_power":
        # steady-state only: cap the step at 5 ns (20 us at 10 ps = 2M points)
        return f".tran {step}p {window}n 0 5n"
    return f".tran {step}p {window}n" + (" uic" if uic else "")


def _apply_tran_window(netlist, arc_type, arc_config, sim_cfg):
    """Replace the template's .tran directive for this arc type."""
    directive = _tran_directive(arc_type, arc_config, sim_cfg)
    if directive is None:
        return netlist
    return re.sub(
        r"^\.tran\s+.*$", directive, netlist,
        flags=re.MULTILINE | re.IGNORECASE
    )


def generate_netlist_string(template, sim_conditions, arc_config):
    """
    Generate SPICE netlist for standard cell characterization.

    Args:
        template: Base SPICE template string
        sim_conditions: Dict with keys: temp_str, vdd_str, clock_slew_str,
                        data_slew_str, load_str, delta_ns
        arc_config: Arc configuration dict

    Returns:
        Complete SPICE netlist string
    """
    sim_cfg = arc_config.get('_sim_cfg', {})
    arc_type = arc_config.get("type")
    netlist = apply_standard_replacements(template, sim_conditions)
    netlist = apply_robust_options(
        netlist, sim_conditions['temp_str'], sim_cfg
    )
    netlist = _apply_tran_window(netlist, arc_type, arc_config, sim_cfg)
    stimuli_block, init_block = _generate_arc_stimuli(
        arc_type, sim_conditions, arc_config
    )
    damping = sim_cfg.get("series_damping_ohm", 10)
    stimuli_block = add_series_resistors(stimuli_block, damping)
    init_block = add_series_resistors(init_block, damping)
    netlist = netlist.replace('%%STIMULI_BLOCK%%', stimuli_block)
    scan_pins = arc_config.get('_scan_pins', ["SCE", "SCD"])
    for pin in scan_pins:
        if f"V_{pin}" not in stimuli_block and f"V_{pin}" not in init_block:
            init_block += f"\nV_{pin} {pin} VSS DC 0"
    netlist = netlist.replace('%%INIT_BLOCK%%', init_block)

    # If precomputed ICs are attached to this arc, inject them and drop UIC
    ic_dict = arc_config.get('_ic_dict')
    if ic_dict and build_ic_block:
        ic_block = build_ic_block(ic_dict)
        # Splice IC block before .end
        netlist = netlist.replace(".end", f"{ic_block}\n.end")
        # Drop 'uic' from .tran so the ICs are actually applied
        netlist = re.sub(
            r"^(\.tran\s+\S+\s+\S+)(\s+uic)\s*$",
            r"\1",
            netlist,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        # With ICs the trigger ramp starts at t=0 and the fixed integration
        # window is [0, power_window_ns]; simulating past it is wasted time
        # (at a 1 ps step a 300 ns stop costs ~25x a 12 ns window).
        if (arc_type == "internal_power"
                and sim_cfg.get("power_fixed_edge_window", False)):
            win_ns = float(arc_config.get(
                "power_window_ns", sim_cfg.get("power_window_ns", 8.2)))
            stop = win_ns + float(sim_cfg.get("power_ic_tail_ns", 3.0))
            netlist = re.sub(
                r"^(\.tran\s+\S+\s+)\S+",
                lambda m: f"{m.group(1)}{stop:g}n",
                netlist, count=1,
                flags=re.MULTILINE | re.IGNORECASE,
            )

    return netlist


def _generate_arc_stimuli(arc_type, sim_conditions, arc_config):
    """
    Dispatcher to route to appropriate arc handler.

    Returns:
        tuple: (stimuli_block, init_block)
    """
    handlers = {
        "setup": generate_timing_arc_stimuli,
        "hold": generate_timing_arc_stimuli,
        "cell_rise": generate_timing_arc_stimuli,
        "cell_fall": generate_timing_arc_stimuli,
        "setup_latch": generate_latch_stimuli,
        "hold_latch": generate_latch_stimuli,
        "recovery": generate_recovery_removal,
        "removal": generate_recovery_removal,
        "propagation_delay": generate_prop_delay_stimuli,
        "internal_power": generate_prop_delay_stimuli,
        "mpw": generate_mpw_stimuli,
        "input_capacitance": generate_input_cap,
    }

    if HAS_POWER:
        handlers["leakage_power"] = generate_leakage_stimuli
        handlers["hidden_power_internal"] = (
            generate_hidden_internal_stimuli
        )
        # .ic-settled internal power: DC pins, ramp at t=0; falls back to
        # the prop-delay stimulus when no .ic is attached.
        handlers["internal_power"] = generate_internal_ic_stimuli
    handler = handlers.get(arc_type)
    if handler:
        return handler(sim_conditions, arc_config)
    return "", ""


__all__ = ["generate_netlist_string", "add_series_resistors"]
