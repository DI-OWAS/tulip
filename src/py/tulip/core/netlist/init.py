"""Initialization blocks (async assert/release preload)."""


def generate_init_block(init_type, arc_config):
    """Generate initialization block for async pins."""
    func_map = arc_config.get("function_mapping", {})
    reset_pin = func_map.get("reset_b")
    set_pin = func_map.get("set_b")
    init_block = ""

    if init_type == "reset":
        if reset_pin:
            init_block += (
                f"V_{reset_pin} {reset_pin} VSS "
                f"PULSE({{VDD_VAL}} 0 1ns 10p 10p 2.0n {{VDD_VAL}})\n"
            )
        if set_pin:
            init_block += f"V_{set_pin} {set_pin} VSS DC {{VDD_VAL}}\n"
    elif init_type == "set":
        if reset_pin:
            init_block += f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
        if set_pin:
            init_block += (
                f"V_{set_pin} {set_pin} VSS "
                f"PULSE({{VDD_VAL}} 0 1ns 10p 10p 2.0n {{VDD_VAL}})\n"
            )
    else:
        if reset_pin:
            init_block += f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
        if set_pin:
            init_block += f"V_{set_pin} {set_pin} VSS DC {{VDD_VAL}}\n"

    return init_block


def build_timing_arc_init(arc_config, handled_pins, init_type):
    """Build init block for timing arcs handling init_pin_pwl."""
    init_pin_pwl = arc_config.get("init_pin_pwl", {})
    if init_pin_pwl:
        init_block = ""
        for pin, pwl_str in init_pin_pwl.items():
            init_block += f'V_{pin} {pin} VSS {pwl_str}\n'
            handled_pins.add(pin)
    else:
        init_block = generate_init_block(init_type, arc_config)
        func_map = arc_config.get("function_mapping", {})
        if func_map.get("reset_b"):
            handled_pins.add(func_map["reset_b"])
        if func_map.get("set_b"):
            handled_pins.add(func_map["set_b"])
    return init_block


def build_prop_delay_init(arc_config, handled_pins):
    """Build initialization block for propagation delay."""
    init_type = arc_config.get('initialization')
    func_map = arc_config.get('function_mapping', {})
    reset_pin = func_map.get('reset_b')
    set_pin = func_map.get('set_b')
    init_block = ""

    if init_type == "reset":
        if reset_pin and reset_pin not in handled_pins:
            init_block += (
                f"V_{reset_pin} {reset_pin} VSS "
                f"PULSE({{VDD_VAL}} 0 1ns 10p 10p 2.0n 1us)\n"
            )
            handled_pins.add(reset_pin)
        if set_pin and set_pin not in handled_pins:
            init_block += f"V_{set_pin} {set_pin} VSS DC {{VDD_VAL}}\n"
            handled_pins.add(set_pin)
    elif init_type == "set":
        if reset_pin and reset_pin not in handled_pins:
            init_block += f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
            handled_pins.add(reset_pin)
        if set_pin and set_pin not in handled_pins:
            init_block += (
                f"V_{set_pin} {set_pin} VSS "
                f"PULSE({{VDD_VAL}} 0 1ns 10p 10p 2.0n 1us)\n"
            )
            handled_pins.add(set_pin)
    else:
        if reset_pin and reset_pin not in handled_pins:
            init_block += f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
            handled_pins.add(reset_pin)
        if set_pin and set_pin not in handled_pins:
            init_block += f"V_{set_pin} {set_pin} VSS DC {{VDD_VAL}}\n"
            handled_pins.add(set_pin)

    return init_block
