"""Shared helpers: voltage resolution, template substitution,
robust ngspice options, and static-pin emission."""

import re


def resolve_voltage(state):
    """Resolve static pin state string to SPICE voltage expression."""
    state_str = str(state).strip().upper()
    if state_str in ("VDD", "VVDD", "VCC", "POWER"):
        return "{VDD_VAL}"
    if state_str in ("VSS", "GND", "0", "GROUND"):
        return "0"
    try:
        float(state)
        return str(state)
    except ValueError:
        return "0"


def add_series_resistors(spice_block, res_ohm=10):
    def repl(match):
        name = match.group(1)
        # Never insert a series R into measurement fixtures: the 0V tri-state
        # sense source must stay a pure ammeter in series with the pull switch.
        if name.startswith("SENSE_TSP"):
            return match.group(0)
        node = match.group(2)
        defn = match.group(3)
        return (
            f"V_{name} {node}_src VSS {defn}\n"
            f"R_{name} {node}_src {node} {res_ohm}"
        )
    # Regex: Start of line, V_Name, whitespace,
    # Node, whitespace, VSS, whitespace, Rest of line
    pattern = r"^V_(\S+)\s+(\S+)\s+VSS\s+(.+)$"
    return re.sub(pattern, repl, spice_block, flags=re.MULTILINE)


def apply_standard_replacements(template, sim_conditions):
    """Apply standard template variable replacements."""
    preload_ns = sim_conditions.get('preload_clk_time_ns', 5.0)
    measure_ns = sim_conditions.get('measure_clk_time_ns', 15.0)
    return (template
            .replace('%%TEMP_VAL%%', sim_conditions['temp_str'])
            .replace('%%VDD_VAL%%', sim_conditions['vdd_str'])
            .replace('%%C_LOAD%%', sim_conditions['load_str'])
            .replace('%%CLOCK_SLEW%%', sim_conditions['clock_slew_str'])
            .replace('%%DATA_SLEW%%', sim_conditions['data_slew_str'])
            .replace('%%PRELOAD_CLK_TIME%%', f"{preload_ns}e-9")
            .replace('%%MEASURE_CLK_TIME%%', f"{measure_ns}e-9"))


def apply_robust_options(netlist, temp_str, sim_cfg=None):
    """Apply robust simulation options with METHOD=GEAR."""
    if sim_cfg is None:
        sim_cfg = {}
    method = sim_cfg.get("sim_method", "GEAR")
    gmin = sim_cfg.get("sim_gmin", "1e-12")
    reltol = sim_cfg.get("sim_reltol", 0.005)
    abstol = sim_cfg.get("sim_abstol", "1n")
    vntol = sim_cfg.get("sim_vntol", "10u")
    itl4 = sim_cfg.get("sim_itl4", 100)
    robust_options = (
        f".options TEMP={temp_str} TNOM=25 METHOD={method} "
        f"GMIN={gmin} RELTOL={reltol} ABSTOL={abstol} VNTOL={vntol} "
        f"TRT=1 ITL4={itl4} CHGTOL=1e-15"
    )

    netlist = re.sub(
        r"^\.options.*TEMP=.*$", robust_options, netlist,
        flags=re.MULTILINE
    )

    if "METHOD=GEAR" not in netlist:
        if ".options" in netlist:
            netlist = netlist.replace(
                ".options TEMP",
                robust_options + "\n* Old options: .options TEMP"
            )
        else:
            netlist = netlist.replace(
                "*--- Analysis ---",
                f"*--- Analysis ---\n{robust_options}"
            )

    return netlist


def add_static_pins_to_stimuli(stimuli_block, arc_config, handled_pins):
    """Add static pins to stimuli block."""
    if "static_pins" in arc_config:
        for pin, state in arc_config["static_pins"].items():
            if pin not in handled_pins:
                stimuli_block += (
                    f'V_{pin} {pin} VSS DC {resolve_voltage(state)}\n'
                )
    return stimuli_block


def add_preload_static_pins(stimuli_block, arc_config, handled_pins,
                            preload_pins, switch_ns):
    """Add static pins with preload PWL support."""
    if "static_pins" not in arc_config:
        return stimuli_block
    for pin, state in arc_config["static_pins"].items():
        if pin in handled_pins:
            continue
        if pin in preload_pins:
            preload_v = resolve_voltage(preload_pins[pin])
            static_v = resolve_voltage(state)
            stimuli_block += (
                f'V_{pin} {pin} VSS '
                f'PWL(0 {preload_v} {switch_ns}ns {preload_v} '
                f'{switch_ns+0.1}ns {static_v} 100ns {static_v})\n'
            )
        else:
            stimuli_block += (
                f'V_{pin} {pin} VSS DC {resolve_voltage(state)}\n'
            )
        handled_pins.add(pin)
    return stimuli_block
