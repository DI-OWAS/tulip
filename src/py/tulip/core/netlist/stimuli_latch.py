"""Latch setup/hold stimulus generation (gate-polarity aware)."""

from .common import (
    add_static_pins_to_stimuli,
)
from .init import (
    generate_init_block,
)


def _gate_levels(gate_sig):
    """PULSE start/end levels for the gate: inverted for active-low pins."""
    if gate_sig.upper().endswith("_N"):
        return "{VDD_VAL} 0"
    return "0 {VDD_VAL}"


def generate_latch_stimuli(sim_conditions, arc_config):
    """Generate stimuli for latch setup/hold arcs."""
    delta_ns = sim_conditions['delta_ns']

    arc_type = arc_config.get("type")
    init_type = arc_config.get("initialization")
    gate_sig = arc_config["gate_signal"]
    # Active-low gates (GATE_N) need inverted levels so the functional
    # phases match the active-high case: closed until 10ns, transparent
    # 10-15ns, closed after. MEASURE_CLK_TIME anchoring is unchanged.
    gate_def = (
        f"PULSE({_gate_levels(gate_sig)} "
        f"{{{{ 10e-9 - ({{CLOCK_SLEW}} / 2) }}}} "
        f"{{CLOCK_SLEW}} {{CLOCK_SLEW}} "
        f"{{5e-9 - {{CLOCK_SLEW}}}} 10e-9)"
    )
    data_trans = arc_config.get("data_transition")
    if arc_type == "hold_latch":
        pwl_def = _generate_hold_latch_pwl(data_trans, delta_ns)
    else:
        pwl_def = _generate_setup_latch_pwl(data_trans, delta_ns)
    data_sig = arc_config["data_signal"]
    stimuli_block = (
        f'V_{gate_sig} {gate_sig} VSS {gate_def}\n'
        f'V_{data_sig} {data_sig} VSS {pwl_def}\n'
    )

    init_block = generate_init_block(init_type, arc_config)
    handled_pins = {gate_sig, data_sig}
    func_map = arc_config.get("function_mapping", {})
    if func_map.get("reset_b"):
        handled_pins.add(func_map["reset_b"])
    if func_map.get("set_b"):
        handled_pins.add(func_map["set_b"])
    stimuli_block = add_static_pins_to_stimuli(
        stimuli_block, arc_config, handled_pins
    )
    return stimuli_block, init_block


def _generate_hold_latch_pwl(data_trans, delta_ns):
    """Generate PWL for latch hold."""
    if data_trans == "rise":
        return (
            f"PWL(0s {{VDD_VAL}} "
            f"{{8e-9 - ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
            f"{{8e-9 + ({{DATA_SLEW}} / 2)}} 0 "
            f"{{{{MEASURE_CLK_TIME}} + ({delta_ns} * 1e-9) "
            f"- ({{DATA_SLEW}} / 2)}} 0 "
            f"{{{{MEASURE_CLK_TIME}} + ({delta_ns} * 1e-9) "
            f"+ ({{DATA_SLEW}} / 2)}} {{VDD_VAL}})"
        )
    return (
        f"PWL(0s 0 "
        f"{{8e-9 - ({{DATA_SLEW}} / 2)}} 0 "
        f"{{8e-9 + ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
        f"{{{{MEASURE_CLK_TIME}} + ({delta_ns} * 1e-9) "
        f"- ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
        f"{{{{MEASURE_CLK_TIME}} + ({delta_ns} * 1e-9) "
        f"+ ({{DATA_SLEW}} / 2)}} 0)"
    )


def _generate_setup_latch_pwl(data_trans, delta_ns):
    """Generate PWL for latch setup."""
    if data_trans == "rise":
        initial = "0"
        final = "{VDD_VAL}"
    else:
        initial = "{VDD_VAL}"
        final = "0"

    transition = f"{{MEASURE_CLK_TIME - ({delta_ns} * 1e-9)}}"
    start_time = f"{{ {transition} - ({{DATA_SLEW}} / 2) }}"
    end_time = f"{{ {transition} + ({{DATA_SLEW}} / 2) }}"

    return f"PWL(0s {initial} {start_time} {initial} {end_time} {final})"
