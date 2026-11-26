"""Minimum-pulse-width stimulus generation."""

from .common import (
    add_static_pins_to_stimuli,
)


def generate_mpw_stimuli(sim_conditions, arc_config):
    """Generate stimuli for MPW (Minimum Pulse Width) arcs."""
    delta_ns = sim_conditions['delta_ns']
    pulsed_pin = arc_config["data_signal"]
    pulse_polarity = arc_config.get("pulse_polarity", "active_low")

    # Build pulse waveform
    pwl_def = _build_mpw_pulse_waveform(pulse_polarity, delta_ns, arc_config)
    stimuli_block = f'V_{pulsed_pin} {pulsed_pin} VSS {pwl_def}\n'
    handled_pins = {pulsed_pin}

    # Build init block
    init_block = _build_mpw_init_block(arc_config, pulsed_pin, handled_pins)

    # Add special pins (D, CLK for special cases)
    stimuli_block = _add_mpw_special_pins(
        arc_config, pulsed_pin, handled_pins, stimuli_block
    )

    # Add default async pins
    init_block = _add_mpw_default_pins(arc_config, handled_pins, init_block)

    # Add static pins
    stimuli_block = add_static_pins_to_stimuli(
        stimuli_block, arc_config, handled_pins
    )

    return stimuli_block, init_block


def _build_mpw_pulse_waveform(pulse_polarity, delta_ns, arc_config):
    """Generate PWL waveform for MPW pulse."""
    pulse_start_ns = 50

    # Check if this is CLK with init=reset (needs preload)
    pulsed_pin = arc_config.get("data_signal", "")
    init_type = arc_config.get("initialization")
    func_map = arc_config.get("function_mapping", {})
    reset_pin = func_map.get("reset_b")

    is_clk = any(x in pulsed_pin.upper() for x in ["CLK", "CK", "CP"])
    needs_preload = (init_type == "reset" and reset_pin and is_clk)

    if pulse_polarity == "active_high":
        if needs_preload:
            # CLK with reset: add preload pulse at 10-15ns
            return (
                f"PWL("
                f"0 0 "
                f"10n 0 "
                f"10.1n {{{{VDD_VAL}}}} "  # Preload pulse
                f"15n {{{{VDD_VAL}}}} "
                f"15.1n 0 "
                f"{pulse_start_ns}n 0 "
                f"{{{pulse_start_ns}n + {{DATA_SLEW}}}} {{{{VDD_VAL}}}} "
                f"{{{pulse_start_ns}n + {{DATA_SLEW}} + {delta_ns}n}} "
                f"{{{{VDD_VAL}}}} "
                f"{{{pulse_start_ns}n + (2 * {{DATA_SLEW}}) + {delta_ns}n}} 0 "
                f"300n 0"
                f")"
            )
        # No preload needed - start directly at test pulse
        return (
            f"PWL("
            f"0 0 "
            f"{pulse_start_ns}n 0 "
            f"{{{pulse_start_ns}n + {{DATA_SLEW}}}} {{{{VDD_VAL}}}} "
            f"{{{pulse_start_ns}n + {{DATA_SLEW}} + {delta_ns}n}} "
            f"{{{{VDD_VAL}}}} "
            f"{{{pulse_start_ns}n + (2 * {{DATA_SLEW}}) + {delta_ns}n}} 0 "
            f"300n 0"
            f")"
        )
    return (
        f"PWL("
        f"0 {{{{VDD_VAL}}}} "
        f"{pulse_start_ns}n {{{{VDD_VAL}}}} "
        f"{{{pulse_start_ns}n + {{DATA_SLEW}}}} 0 "
        f"{{{pulse_start_ns}n + {{DATA_SLEW}} + {delta_ns}n}} 0 "
        f"{{{pulse_start_ns}n + (2 * {{DATA_SLEW}}) + {delta_ns}n}} "
        f"{{{{VDD_VAL}}}} "
        f"300n {{{{VDD_VAL}}}}"
        f")"
    )


def _build_mpw_init_block(arc_config, pulsed_pin, handled_pins):
    """Build initialization block for MPW."""
    init_type = arc_config.get("initialization")
    func_map = arc_config.get("function_mapping", {})
    reset_pin = func_map.get("reset_b")
    set_pin = func_map.get("set_b")
    init_block = ""

    if init_type == "set" and set_pin and set_pin not in handled_pins:
        init_block += (
            f"V_{set_pin} {set_pin} VSS "
            f"PULSE({{VDD_VAL}} 0 2ns 100p 100p 5.0n 120ns)\n"
        )
        handled_pins.add(set_pin)

    elif init_type == "reset" and reset_pin and reset_pin not in handled_pins:
        pulsed_upper = pulsed_pin.upper()
        is_clk_pulse = any(x in pulsed_upper for x in ["CLK", "CK", "CP"])

        if is_clk_pulse:
            init_block += f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
            handled_pins.add(reset_pin)
        else:
            init_block += (
                f"V_{reset_pin} {reset_pin} VSS "
                f"PULSE({{VDD_VAL}} 0 2ns 100p 100p 5.0n 120ns)\n"
            )
            handled_pins.add(reset_pin)

    return init_block


def _add_mpw_special_pins(arc_config, pulsed_pin, handled_pins, stimuli_block):
    """Add special pins for MPW (D, CLK for async cases)."""
    init_type = arc_config.get("initialization")
    func_map = arc_config.get("function_mapping", {})
    set_pin = func_map.get("set_b")
    reset_pin = func_map.get("reset_b")
    # Handle set without set_pin
    if init_type == "set" and not set_pin:
        pulsed_upper = pulsed_pin.upper()
        is_async = any(
            x in pulsed_upper for x in ["RESET", "SET", "CLR", "PRE", "RST"]
        )
        if is_async:
            gate_pin = arc_config.get("preload_clock_pin", "CLK")
            d_pin = arc_config.get("preload_data_pin", "D")
            if gate_pin.upper().endswith("_N"):
                preload_clk = (
                    "PWL(0 {VDD_VAL} 10n {VDD_VAL} 10.1n 0 "
                    "15n 0 15.1n {VDD_VAL} 300n {VDD_VAL})"
                )
            else:
                preload_clk = (
                    "PWL(0 0 10n 0 10.1n {VDD_VAL} "
                    "15n {VDD_VAL} 15.1n 0 300n 0)"
                )
            stimuli_block += f'V_{gate_pin} {gate_pin} VSS {preload_clk}\n'
            stimuli_block += f'V_{d_pin} {d_pin} VSS DC {{{{VDD_VAL}}}}\n'
            handled_pins.add(gate_pin)
            handled_pins.add(d_pin)
    # Handle reset with CLK pulse
    elif init_type == "reset" and reset_pin:
        pulsed_upper = pulsed_pin.upper()
        is_clk = any(x in pulsed_upper for x in ["CLK", "CK", "CP"])
        if is_clk:
            d_pwl = "PWL(0 0 20n 0 20.1n {VDD_VAL} 300n {VDD_VAL})"
            stimuli_block += f'V_D D VSS {d_pwl}\n'
            handled_pins.add("D")
    return stimuli_block


def _add_mpw_default_pins(arc_config, handled_pins, init_block):
    """Add default reset/set pins for MPW."""
    func_map = arc_config.get("function_mapping", {})
    reset_pin = func_map.get("reset_b")
    set_pin = func_map.get("set_b")

    if reset_pin and reset_pin not in handled_pins:
        init_block += f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
        handled_pins.add(reset_pin)

    if set_pin and set_pin not in handled_pins:
        init_block += f"V_{set_pin} {set_pin} VSS DC {{VDD_VAL}}\n"
        handled_pins.add(set_pin)

    return init_block
