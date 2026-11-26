"""Recovery/removal stimulus generation for flops and latches."""

from .common import (
    add_static_pins_to_stimuli,
    resolve_voltage,
)


def generate_recovery_removal(sim_conditions, arc_config):
    """Generate stimuli for recovery/removal arcs."""
    delta_ns = sim_conditions['delta_ns']

    arc_type = arc_config.get("type")
    async_polarity = arc_config.get("async_polarity", "active_low")

    if async_polarity == "active_low":
        assert_level = "0"
        deassert_level = "{VDD_VAL}"
    else:
        assert_level = "{VDD_VAL}"
        deassert_level = "0"

    is_latch = (
        arc_config.get("cell_type") == "latch"
        or arc_config.get("gate_signal") is not None
    )

    if is_latch:
        return _generate_latch_rec_rem(
            arc_type, assert_level, deassert_level, delta_ns, arc_config
        )
    return _generate_ff_recovery_removal(
        arc_type, assert_level, deassert_level, delta_ns,
        arc_config
    )


def _generate_latch_rec_rem(arc_type, assert_level, deassert_level,
                            delta_ns, arc_config):
    """Generate recovery/removal stimuli for latches."""
    gate_signal = arc_config.get("gate_signal", "GATE")
    data_sig = arc_config["data_signal"]

    if arc_type == "recovery":
        gate_def, async_pwl = _build_latch_recovery_waveforms(
            assert_level, deassert_level, delta_ns
        )
    else:
        gate_def, async_pwl = _build_latch_removal_waveforms(
            assert_level, deassert_level, delta_ns, arc_config
        )

    stimuli_block = f'V_{gate_signal} {gate_signal} VSS {gate_def}\n'
    stimuli_block += f'V_{data_sig} {data_sig} VSS {async_pwl}\n'

    # Add D for removal case
    if arc_type == "removal":
        func_map = arc_config.get("function_mapping", {})
        reset_pin = func_map.get("reset_b")
        set_pin = func_map.get("set_b")
        if data_sig == reset_pin:
            stimuli_block += 'V_D D VSS DC {{VDD_VAL}}\n'
        elif data_sig == set_pin:
            stimuli_block += 'V_D D VSS DC 0\n'
    # Add static pins using shared helper
    handled_pins = {gate_signal, data_sig, "D"}
    stimuli_block = add_static_pins_to_stimuli(
        stimuli_block, arc_config, handled_pins
    )

    return stimuli_block, ""


def _build_latch_recovery_waveforms(assert_level, deassert_level, delta_ns):
    """Build waveforms for latch recovery."""
    gate_def = (
        "PWL(0 0 "
        "{10ns - ({CLOCK_SLEW}/2)} 0 "
        "{10ns + ({CLOCK_SLEW}/2)} {VDD_VAL} "
        "{15ns - ({CLOCK_SLEW}/2)} {VDD_VAL} "
        "{15ns + ({CLOCK_SLEW}/2)} 0 "
        "100ns 0)"
    )

    transition = f"{{15ns - ({delta_ns} * 1e-9)}}"
    async_pwl = (
        f"PWL(0 {assert_level} "
        f"{{ {transition} - ({{DATA_SLEW}}/2) }} {assert_level} "
        f"{{ {transition} + ({{DATA_SLEW}}/2) }} {deassert_level} "
        f"100ns {deassert_level})"
    )

    return gate_def, async_pwl


def _build_latch_removal_waveforms(
        assert_level, deassert_level, delta_ns, _arc_config
):
    """Build waveforms for latch removal."""
    gate_def = (
        "PWL(0 0 "
        "2ns 0 "
        "{2ns + ({CLOCK_SLEW}/2)} {VDD_VAL} "
        "4ns {VDD_VAL} "
        "{4ns + ({CLOCK_SLEW}/2)} 0 "
        "{10ns - ({CLOCK_SLEW}/2)} 0 "
        "{10ns + ({CLOCK_SLEW}/2)} {VDD_VAL} "
        "{15ns - ({CLOCK_SLEW}/2)} {VDD_VAL} "
        "{15ns + ({CLOCK_SLEW}/2)} 0 "
        "100ns 0)"
    )

    transition = f"{{15ns + ({delta_ns} * 1e-9)}}"
    async_pwl = (
        f"PWL(0 {deassert_level} "
        f"2ns {deassert_level} "
        f"2.1ns {assert_level} "
        f"{{ {transition} - ({{DATA_SLEW}}/2) }} {assert_level} "
        f"{{ {transition} + ({{DATA_SLEW}}/2) }} {deassert_level} "
        f"100ns {deassert_level})"
    )
    return gate_def, async_pwl


def _build_ff_rec_rem_pins(arc_config, stimuli_block, handled_pins):
    """Add async and static pins for FF recovery/removal."""
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


def _generate_ff_recovery_removal(arc_type, assert_level, deassert_level,
                                  delta_ns, arc_config):
    """Generate recovery/removal stimuli for flip-flops."""
    clock_def = _build_ff_clock_pulse()
    pwl_def = _build_ff_recovery_removal_pwl(
        arc_type, assert_level, deassert_level, delta_ns, arc_config
    )

    clock_sig = arc_config["clock_signal"]
    data_sig = arc_config["data_signal"]

    stimuli_block = f'V_{clock_sig} {clock_sig} VSS {clock_def}\n'
    stimuli_block += f'V_{data_sig} {data_sig} VSS {pwl_def}\n'
    handled_pins = {clock_sig, data_sig}
    stimuli_block, init_block = _build_ff_rec_rem_pins(
        arc_config, stimuli_block, handled_pins
    )
    return stimuli_block, init_block


def _build_ff_clock_pulse():
    """Build clock pulse for FF recovery/removal."""
    clock_start = "{ {PRELOAD_CLK_TIME} - ({CLOCK_SLEW} / 2) }"
    return (
        f"PULSE(0 {{VDD_VAL}} {clock_start} "
        f"{{CLOCK_SLEW}} {{CLOCK_SLEW}} "
        f"{{5e-9 - {{CLOCK_SLEW}}}} 10e-9)"
    )


def _build_ff_recovery_removal_pwl(arc_type, assert_level, deassert_level,
                                   delta_ns, arc_config):
    """Build PWL for FF recovery/removal."""
    if arc_type == "recovery":
        transition = f"{{MEASURE_CLK_TIME - ({delta_ns} * 1e-9)}}"
        start_time = f"{{ {transition} - ({{DATA_SLEW}} / 2) }}"
        end_time = f"{{ {transition} + ({{DATA_SLEW}} / 2) }}"
        return (
            f"PWL(0s {assert_level} {start_time} "
            f"{assert_level} {end_time} {deassert_level})"
        )
    async_polarity = arc_config.get('async_polarity', 'active_low')
    rise_time = f"{{{{MEASURE_CLK_TIME}} + ({delta_ns} * 1e-9)}}"

    if async_polarity == "active_low":
        return (
            f"PWL(0s {{VDD_VAL}} "
            f"2ns {{VDD_VAL}} "
            f"2.1ns 0 "
            f"{{ {rise_time} - ({{DATA_SLEW}} / 2) }} 0 "
            f"{{ {rise_time} + ({{DATA_SLEW}} / 2) }} {{VDD_VAL}} "
            f"100ns {{VDD_VAL}})"
        )
    return (
        f"PWL(0s 0 "
        f"2ns 0 "
        f"2.1ns {{VDD_VAL}} "
        f"{{ {rise_time} - ({{DATA_SLEW}} / 2) }} {{VDD_VAL}} "
        f"{{ {rise_time} + ({{DATA_SLEW}} / 2) }} 0 "
        f"100ns 0)"
    )
