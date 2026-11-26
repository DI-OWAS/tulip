"""Preload-set/reset sequences (clock- and gate-driven)."""

from .common import (
    resolve_voltage,
)


def _preload_base_ns(arc_config):
    """Time of the measured edge in a preload sequence, in ns.

    The sequence is defined relative to this edge: the preload clock primes
    at base-8.1/-8 and releases at base-6.1/-6, the preload data settles at
    base-2.1/-2, the measured transition is at base, and the PWL tail runs
    to base+90. A base of 10 reproduces the values this module used to
    hardcode, so nothing changes unless pulse_start_preload_ns is set.
    """
    sim_cfg = arc_config.get('_sim_cfg', {})
    return float(sim_cfg.get("pulse_start_preload_ns", 10))


def _pulse_start_ns(arc_config):
    """Measured pulse start for the non-preload standard pulse, in ns."""
    sim_cfg = arc_config.get('_sim_cfg', {})
    return float(sim_cfg.get("pulse_start_ns", 5))


def generate_preload_set_stimuli(pulsed_pin, reset_pin, arc_config):
    """Generate stimuli for cells needing preload to set Q=1."""
    pin_upper = pulsed_pin.upper()
    is_clk = pin_upper in ["CLK", "CK", "CP", "GATE", "GATE_N"]
    is_reset = pin_upper in ["RESET_B", "RESETB", "RST_B", "CLR_B", "CLEAR_B"]

    if is_clk:
        return _build_clk_preload_set(pulsed_pin, reset_pin, arc_config)
    if is_reset:
        return _build_reset_preload_set(pulsed_pin, arc_config)
    return _build_standard_preload_pulse(pulsed_pin, arc_config)


def _build_clk_preload_set(pulsed_pin, reset_pin, arc_config):
    """Build CLK/GATE preload for set Q=1. Data pin is configurable
    (preload_data_pin, e.g. SCD when SCE selects the scan input), and an
    active-low gate (name ending _N) gets the inverted waveform: preload
    window is LOW (open), measured edge at base FALLS (opens)."""
    base_ns = _preload_base_ns(arc_config)
    stimuli_block = ""
    init_block = ""
    handled_pins = set()

    d_pin = arc_config.get("preload_data_pin", "D")
    d_pwl = (
        "PWL(0 {{VDD_VAL}} "
        f"{base_ns - 2.1:g}ns {{{{VDD_VAL}}}} "
        f"{base_ns - 2:g}ns 0 "
        f"{base_ns + 90:g}ns 0)"
    )
    stimuli_block += f'V_{d_pin} {d_pin} VSS {d_pwl}\n'
    handled_pins.add(d_pin)

    if pulsed_pin.upper().endswith("_N"):
        clk_def = (
            "PWL("
            "0 {VDD_VAL} "
            f"{base_ns - 8.1:g}ns {{VDD_VAL}} "
            f"{base_ns - 8:g}ns 0 "
            f"{base_ns - 6.1:g}ns 0 "
            f"{base_ns - 6:g}ns {{VDD_VAL}} "
            f"{{{base_ns:g}ns - ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
            f"{{{base_ns:g}ns + ({{DATA_SLEW}} / 2)}} 0 "
            f"{base_ns + 90:g}ns 0"
            ")"
        )
    else:
        clk_def = (
            "PWL("
            "0 0 "
            f"{base_ns - 8.1:g}ns 0 "
            f"{base_ns - 8:g}ns {{VDD_VAL}} "
            f"{base_ns - 6.1:g}ns {{VDD_VAL}} "
            f"{base_ns - 6:g}ns 0 "
            f"{{{base_ns:g}ns - ({{DATA_SLEW}} / 2)}} 0 "
            f"{{{base_ns:g}ns + ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
            f"{base_ns + 90:g}ns {{VDD_VAL}}"
            ")"
        )
    stimuli_block += f'V_{pulsed_pin} {pulsed_pin} VSS {clk_def}\n'
    handled_pins.add(pulsed_pin)
    func_map = arc_config.get("function_mapping", {})
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


def _build_reset_preload_set(pulsed_pin, arc_config):
    """Build RESET preload for set Q=1."""
    base_ns = _preload_base_ns(arc_config)
    stimuli_block = ""
    init_block = ""
    handled_pins = set()

    d_pin = arc_config.get("preload_data_pin", "D")
    stimuli_block += f'V_{d_pin} {d_pin} VSS DC {{{{VDD_VAL}}}}\n'
    handled_pins.add(d_pin)
    clk_pin = arc_config.get("preload_clock_pin", "CLK")
    if clk_pin.upper().endswith("_N"):
        clk_pwl = (
            "PWL(0 {{VDD_VAL}} "
            f"{base_ns - 8.1:g}ns {{{{VDD_VAL}}}} "
            f"{base_ns - 8:g}ns 0 "
            f"{base_ns + 90:g}ns 0)"
        )
    else:
        clk_pwl = (
            "PWL(0 0 "
            f"{base_ns - 8.1:g}ns 0 "
            f"{base_ns - 8:g}ns {{{{VDD_VAL}}}} "
            f"{base_ns + 90:g}ns {{{{VDD_VAL}}}})"
        )
    stimuli_block += f'V_{clk_pin} {clk_pin} VSS {clk_pwl}\n'
    handled_pins.add(clk_pin)

    reset_pwl = (
        "PWL("
        "0 {VDD_VAL} "
        f"{{{base_ns:g}ns - ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
        f"{{{base_ns:g}ns + ({{DATA_SLEW}} / 2)}} 0 "
        f"{base_ns + 90:g}ns 0"
        ")"
    )
    stimuli_block += f'V_{pulsed_pin} {pulsed_pin} VSS {reset_pwl}\n'
    handled_pins.add(pulsed_pin)
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


def _build_standard_preload_pulse(pulsed_pin, arc_config):
    """Build standard pulse for preload."""
    trans = arc_config.get("data_transition")
    if trans == "rise":
        start_val, end_val = "0", "{VDD_VAL}"
    else:
        start_val, end_val = "{VDD_VAL}", "0"

    start_ns = _pulse_start_ns(arc_config)
    pulse = (
        f"PULSE({start_val} {end_val} {start_ns:g}ns "
        f"{{DATA_SLEW}} {{DATA_SLEW}} 50n 100n)"
    )
    stimuli_block = f'V_{pulsed_pin} {pulsed_pin} VSS {pulse}\n'
    handled_pins = {pulsed_pin}
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


def generate_preload_reset_stimuli(pulsed_pin, set_pin, arc_config):
    """Generate stimuli for cells needing preload to reset Q=0."""
    is_clk = pulsed_pin.upper() in ["CLK", "CK", "CP", "GATE"]

    if is_clk:
        return _build_clk_preload_reset(pulsed_pin, set_pin, arc_config)
    return _build_standard_preload_pulse(pulsed_pin, arc_config)


def _build_clk_preload_reset(pulsed_pin, set_pin, arc_config):
    """Build CLK preload for reset Q=0."""
    base_ns = _preload_base_ns(arc_config)
    stimuli_block = ""
    init_block = ""
    handled_pins = set()

    d_pwl = (
        "PWL(0 0 "
        f"{base_ns - 2.1:g}ns 0 "
        f"{base_ns - 2:g}ns {{{{VDD_VAL}}}} "
        f"{base_ns + 90:g}ns {{{{VDD_VAL}}}})"
    )
    stimuli_block += f'V_D D VSS {d_pwl}\n'
    handled_pins.add("D")

    if pulsed_pin.upper().endswith("_N"):
        clk_def = (
            "PWL("
            "0 {VDD_VAL} "
            f"{base_ns - 8.1:g}ns {{VDD_VAL}} "
            f"{base_ns - 8:g}ns 0 "
            f"{base_ns - 6.1:g}ns 0 "
            f"{base_ns - 6:g}ns {{VDD_VAL}} "
            f"{{{base_ns:g}ns - ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
            f"{{{base_ns:g}ns + ({{DATA_SLEW}} / 2)}} 0 "
            f"{base_ns + 90:g}ns 0"
            ")"
        )
    else:
        clk_def = (
            "PWL("
            "0 0 "
            f"{base_ns - 8.1:g}ns 0 "
            f"{base_ns - 8:g}ns {{VDD_VAL}} "
            f"{base_ns - 6.1:g}ns {{VDD_VAL}} "
            f"{base_ns - 6:g}ns 0 "
            f"{{{base_ns:g}ns - ({{DATA_SLEW}} / 2)}} 0 "
            f"{{{base_ns:g}ns + ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
            f"{base_ns + 90:g}ns {{VDD_VAL}}"
            ")"
        )
    stimuli_block += f'V_{pulsed_pin} {pulsed_pin} VSS {clk_def}\n'
    handled_pins.add(pulsed_pin)
    func_map = arc_config.get("function_mapping", {})
    reset_pin = func_map.get("reset_b")
    if set_pin and set_pin not in handled_pins:
        init_block += f"V_{set_pin} {set_pin} VSS DC {{VDD_VAL}}\n"
        handled_pins.add(set_pin)
    if reset_pin and reset_pin not in handled_pins:
        init_block += f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
        handled_pins.add(reset_pin)
    if "static_pins" in arc_config:
        for pin, state in arc_config["static_pins"].items():
            if pin not in handled_pins:
                stimuli_block += (
                    f'V_{pin} {pin} VSS DC {resolve_voltage(state)}\n'
                )
    return stimuli_block, init_block
