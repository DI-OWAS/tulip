"""Flip-flop setup/hold stimulus generation."""

from .common import (
    add_static_pins_to_stimuli,
)
from .init import (
    build_timing_arc_init,
)


def generate_timing_arc_stimuli(sim_conditions, arc_config):
    """Generate stimuli for setup/hold/cell_rise/cell_fall arcs."""
    delta_ns = sim_conditions['delta_ns']

    arc_type = arc_config.get("type")
    init_type = arc_config.get("initialization")

    clock_start_expr = "{{ {{PRELOAD_CLK_TIME}} - ({{CLOCK_SLEW}} / 2) }}"
    clock_def = (
        f"PULSE(0 {{VDD_VAL}} {clock_start_expr} "
        f"{{CLOCK_SLEW}} {{CLOCK_SLEW}} "
        f"{{5e-9 - {{CLOCK_SLEW}}}} 10e-9)"
    )

    data_trans = arc_config.get("data_transition")

    if arc_type == "hold":
        pwl_def = _generate_hold_pwl(data_trans, delta_ns)
    else:
        pwl_def = _generate_setup_pwl(
            data_trans, arc_type, delta_ns, arc_config
        )

    clock_sig = arc_config["clock_signal"]
    data_sig = arc_config["data_signal"]
    stimuli_block = (
        f'V_{clock_sig} {clock_sig} VSS {clock_def}\n'
        f'V_{data_sig} {data_sig} VSS {pwl_def}\n'
    )
    handled_pins = {clock_sig, data_sig}
    init_block = build_timing_arc_init(arc_config, handled_pins, init_type)
    stimuli_block = add_static_pins_to_stimuli(
        stimuli_block, arc_config, handled_pins
    )
    return stimuli_block, init_block


def _generate_hold_pwl(data_trans, delta_ns):
    """Generate PWL for hold arcs."""
    if data_trans == "fall":
        return (
            f"PWL(0s 0 "
            f"{{10ns - ({{DATA_SLEW}} / 2)}} 0 "
            f"{{10ns + ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
            f"{{{{MEASURE_CLK_TIME}} + ({delta_ns} * 1e-9) "
            f"- ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
            f"{{{{MEASURE_CLK_TIME}} + ({delta_ns} * 1e-9) "
            f"+ ({{DATA_SLEW}} / 2)}} 0)"
        )
    return (
        f"PWL(0s {{VDD_VAL}} "
        f"{{10ns - ({{DATA_SLEW}} / 2)}} {{VDD_VAL}} "
        f"{{10ns + ({{DATA_SLEW}} / 2)}} 0 "
        f"{{{{MEASURE_CLK_TIME}} + ({delta_ns} * 1e-9) "
        f"- ({{DATA_SLEW}} / 2)}} 0 "
        f"{{{{MEASURE_CLK_TIME}} + ({delta_ns} * 1e-9) "
        f"+ ({{DATA_SLEW}} / 2)}} {{VDD_VAL}})"
    )


def _generate_setup_pwl(data_trans, arc_type, delta_ns, arc_config):
    """Generate PWL for setup/cell_rise/cell_fall arcs."""
    if data_trans:
        if data_trans == "fall":
            measure_start_level = "{VDD_VAL}"
            measure_end_level = "0"
        else:
            measure_start_level = "0"
            measure_end_level = "{VDD_VAL}"
    else:
        expected_out = arc_config.get("expected_output_transition")
        if expected_out == "fall":
            measure_start_level = "{VDD_VAL}"
            measure_end_level = "0"
        else:
            measure_start_level = "0"
            measure_end_level = "{VDD_VAL}"

    if arc_type == "hold":
        base_time = f"{{MEASURE_CLK_TIME + ({delta_ns} * 1e-9)}}"
    else:
        base_time = f"{{MEASURE_CLK_TIME - ({delta_ns} * 1e-9)}}"

    start_time = f"{{ {base_time} - ({{DATA_SLEW}} / 2) }}"
    end_time = f"{{ {base_time} + ({{DATA_SLEW}} / 2) }}"

    return (
        f"PWL(0s {measure_start_level} {start_time} "
        f"{measure_start_level} {end_time} {measure_end_level})"
    )
