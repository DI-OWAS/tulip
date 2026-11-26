"""Propagation-delay and input-capacitance stimuli, including
the tri-state gated-pull fixture."""

from .common import (
    add_preload_static_pins,
    resolve_voltage,
)
from .init import (
    build_prop_delay_init,
)
from .preload import (
    generate_preload_reset_stimuli,
    generate_preload_set_stimuli,
)


# The capacitance deck runs with uic, so every node starts at 0 V and the
# cell needs time to reach its DC state. Ramping from t=0 put that settling
# inside the measured edge: internal nodes charging beyond the measured pin
# couple back through its Cgd with the opposite sign and cancel part of the
# integrated charge. The error grows with depth in a series PMOS stack
# (nor4_1/D read 4.8% of the reference) and is absent on parallel-PMOS
# cells such as NAND. Holding the start level for CAP_SETTLE_NS separates
# the settling from the edge. Zero reproduces the old stimulus exactly.
CAP_SETTLE_NS = 5.0


def generate_prop_delay_stimuli(_sim_conditions, arc_config):
    """
    Generate stimuli for propagation delay and internal power.

    Note: sim_conditions unused but kept for API consistency
    """
    pulsed_pin = arc_config.get("data_signal")
    output_trans = arc_config.get("expected_output_transition")

    func_map = arc_config.get("function_mapping", {})
    reset_pin = func_map.get("reset_b")
    set_pin = func_map.get("set_b")
    init_type = arc_config.get("initialization")

    # Q_N expresses the same state with inverted polarity: a Q_N-rise from
    # the set state is a Q-fall. Use the Q-equivalent transition so preload
    # dispatch works for both outputs.
    out_sig = arc_config.get("output_signal", "")
    eff_trans = output_trans
    if out_sig.upper().endswith("_N") and output_trans in ("rise", "fall"):
        eff_trans = "fall" if output_trans == "rise" else "rise"

    needs_preload_set = (
        init_type == "set"
        and eff_trans == "fall"
        and not set_pin
    )

    needs_preload_reset = (
        init_type == "reset"
        and eff_trans == "rise"
        and not reset_pin
    )

    if needs_preload_set:
        return generate_preload_set_stimuli(
            pulsed_pin, reset_pin, arc_config
        )
    if needs_preload_reset:
        return generate_preload_reset_stimuli(
            pulsed_pin, set_pin, arc_config
        )
    return _generate_std_prop_delay(pulsed_pin, arc_config)


def _get_prop_delay_pulse_params(arc_config):
    """Extract pulse timing parameters for propagation delay."""
    sim_cfg = arc_config.get('_sim_cfg', {})
    input_trans = arc_config.get('data_transition')
    expected_trans = arc_config.get('expected_output_transition')
    preload_pins = arc_config.get("preload_pins", {})
    has_preload = bool(preload_pins)
    needs_settling = (
        has_preload or (input_trans == "fall") or (expected_trans == "fall")
    )
    ps_norm = sim_cfg.get("pulse_start_ns", 5)
    ps_settle = sim_cfg.get("pulse_start_preload_ns", 10)
    switch_ns = sim_cfg.get("preload_switch_ns", 8.0)
    pulse_start = f"{ps_settle}ns" if needs_settling else f"{ps_norm}ns"
    return input_trans, preload_pins, pulse_start, switch_ns


def _build_pull_fixture(arc_config):
    """Return the tri-state pull resistor line, or "" if not a tri-state arc.

    Always-on pull: while the driver holds the rail it sources a steady
    current through this resistor, and at release that current collapses.
    The collapse is measured on the DUT's own output branch (see the
    V_SENSE_DUT_<out> source added in simulation.build), which makes the
    disable delay load- independent and drive-strength dependent,
    """
    out = arc_config.get("output_signal")
    pull_to = arc_config.get("output_pull_to")      # e.g. "VSS" or "VDD"
    if not (out and pull_to):
        return ""
    if arc_config.get("output_pull_adaptive", False):
        tau_ns = arc_config.get("output_pull_tau_ns", 1.0)
        floor = arc_config.get("output_pull_floor_ohm", 1e4)
        ron_expr = f"max({tau_ns}*1e-9/(C_LOAD*1e-12),{floor})"
    else:
        ron_expr = str(arc_config.get("output_pull_ohm", 6800))
    return f"R_PULL_{out} {out} {pull_to} {ron_expr}\n"


def _generate_std_prop_delay(pulsed_pin, arc_config):
    """Generate standard propagation delay stimuli."""
    (input_trans, preload_pins, pulse_start,
     switch_ns) = _get_prop_delay_pulse_params(arc_config)
    if input_trans == "rise":
        start_val, end_val = "0", "{VDD_VAL}"
    else:
        start_val, end_val = "{VDD_VAL}", "0"
    custom_pwl = arc_config.get("pulsed_pin_pwl")
    if custom_pwl:
        stimuli_block = f'V_{pulsed_pin} {pulsed_pin} VSS {custom_pwl}\n'
    else:
        pulse = (
            f"PULSE({start_val} {end_val} {pulse_start} "
            f"{{DATA_SLEW}} {{DATA_SLEW}} 50n 100n)"
        )
        stimuli_block = f'V_{pulsed_pin} {pulsed_pin} VSS {pulse}\n'
    handled_pins = {pulsed_pin}
    init_block = build_prop_delay_init(arc_config, handled_pins)
    stimuli_block = add_preload_static_pins(
        stimuli_block, arc_config, handled_pins, preload_pins, switch_ns
    )

    stimuli_block += _build_pull_fixture(arc_config)

    return stimuli_block, init_block


def _cap_stimulus(arc_config, input_pin, pwl_def):
    """Pin stimulus, optionally through a predriver cell.

    With no driver configured this is the historical ideal-source form, which
    is also what Liberate's own decks use. With one, the PWL drives the
    predriver and a 0 V source in series with the DUT pin is the ammeter: it
    must be named V_<pin> because find_current_branch looks up
    v_<pin>#branch, and it carries no VSS node so add_series_resistors
    leaves it alone.
    """
    sim_cfg = arc_config.get("_sim_cfg", {})
    # Arc-level override wins, including an explicit null: the driver cell
    # cannot drive itself, because cap_driver_include would define its
    # subckt a second time in the same deck.
    if "cap_driver_model" in arc_config:
        model = arc_config["cap_driver_model"]
    else:
        model = sim_cfg.get("cap_driver_model")
    if not model:
        return f"V_{input_pin} {input_pin} VSS {pwl_def}\n"
    ports = sim_cfg.get("cap_driver_pins", "VSS VDD %IN% %OUT%")
    src, out = f"CAPD_{input_pin}", f"CAPB_{input_pin}"
    ports = ports.replace("%IN%", src).replace("%OUT%", out)
    if "cap_driver_include" in arc_config:
        include = arc_config["cap_driver_include"]
    else:
        include = sim_cfg.get("cap_driver_include")
    head = f".include '{include}'\n" if include else ""
    return (
        f"{head}"
        f"V_CAPD_{input_pin} {src} VSS {pwl_def}\n"
        f"X_CAPDRV_{input_pin} {ports} {model}\n"
        f"V_{input_pin} {out} {input_pin} DC 0\n"
    )


def _cap_ramp_ns(arc_config):
    """The 0->100% ramp of the cap stimulus, in ns."""
    try:
        val = float(arc_config.get("_sim_cfg", {}).get("cap_ramp_ns", 2.0))
    except (TypeError, ValueError):
        return 2.0
    return val if val > 0 else 2.0


def generate_input_cap(_sim_conditions, arc_config):
    """Generate stimuli for input capacitance measurement."""
    input_pin = arc_config.get("data_signal", "A")

    # Check if active-low async pin
    input_upper = input_pin.upper()
    is_active_low_async = any(
        x in input_upper for x in ["RESET", "SET", "CLR", "PRE", "RST"]
    )

    settle = CAP_SETTLE_NS
    ramp = _cap_ramp_ns(arc_config)
    if is_active_low_async:
        # Active-low: Start HIGH (inactive), pulse LOW, return HIGH
        pwl_def = (
            "PWL(0 {{VDD_VAL}} "
            f"{settle:g}n " "{{VDD_VAL}} "
            f"{settle + ramp:g}n 0 "
            f"{settle + ramp + 10:g}n 0 "
            f"{settle + 2 * ramp + 10:g}n " "{{VDD_VAL}} "
            f"{settle + 2 * ramp + 20:g}n " "{{VDD_VAL}})"
        )
    else:
        # Normal pins: Start LOW, pulse HIGH, return LOW
        pwl_def = (
            "PWL(0 0 "
            f"{settle:g}n 0 "
            f"{settle + ramp:g}n " "{{VDD_VAL}} "
            f"{settle + ramp + 10:g}n " "{{VDD_VAL}} "
            f"{settle + 2 * ramp + 10:g}n 0 "
            f"{settle + 2 * ramp + 20:g}n 0)"
        )

    stimuli_block = _cap_stimulus(arc_config, input_pin, pwl_def)
    handled_pins = {input_pin}
    init_block = ""
    func_map = arc_config.get("function_mapping", {})
    reset_pin = func_map.get("reset_b")
    set_pin = func_map.get("set_b")
    if reset_pin and reset_pin not in handled_pins:
        init_block += f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
        handled_pins.add(reset_pin)
    if set_pin and set_pin not in handled_pins:
        init_block += f"V_{set_pin} {set_pin} VSS DC {{VDD_VAL}}\n"
        handled_pins.add(set_pin)
    if "static_pins" in arc_config:
        for pin, state in arc_config["static_pins"].items():
            if pin not in handled_pins:
                stimuli_block += (
                    f'V_{pin} {pin} VSS DC {resolve_voltage(state)}\n'
                )
    return stimuli_block, init_block
