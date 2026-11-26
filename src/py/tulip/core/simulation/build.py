"""Netlist assembly: template substitution and finalization."""


import os
import re
from ..netlist import generate_netlist_string


def build_netlist(
    spice_template_str,
    sim_conditions,
    arc_config,
    cell_config
):
    netlist_string = generate_netlist_string(
        spice_template_str, sim_conditions, arc_config
    )
    out = arc_config.get('output_signal', '')
    pin_mapping = cell_config['pin_mapping']
    sense_line = ""
    if out and arc_config.get("timing_type", "").startswith(
            "three_state_disable"):
        # Route the DUT output through a 0V sense source so that
        # i(V_SENSE_DUT_<out>) is the current the CELL drives. Everything
        # else (load, pull) stays on the external node.
        pin_mapping = " ".join(
            f"{out}_drv" if tok == out else tok
            for tok in pin_mapping.split()
        )
        sense_line = f"\nV_SENSE_DUT_{out} {out}_drv {out} DC 0"
    # Model library, corner and cell netlist come from the config, so the
    # template needs no per-run editing.
    sim_cfg = arc_config.get('_sim_cfg', {})
    model = cell_config['spice_model']
    pex_file = cell_config.get(
        'pex_file',
        (model[:-5] if model.endswith('_flat') else model) + '.pex.spice'
    )
    pex_dir = cell_config.get('pex_dir', sim_cfg.get('pex_dir', 'pex'))
    netlist_string = (
        netlist_string
        .replace('%%PIN_MAPPING%%', pin_mapping)
        .replace('%%SPICE_MODEL%%', model)
        .replace('%%OUTPUT_NODE%%', out)
        .replace('%%MODEL_LIB%%', cell_config.get(
            'model_lib', sim_cfg.get('model_lib', 'cornerMOSlv.lib')))
        .replace('%%MODEL_CORNER%%', cell_config.get(
            'model_corner', sim_cfg.get('model_corner', 'mos_tt')))
        .replace('%%PEX_INCLUDES%%', f".include '{pex_dir}/{pex_file}'")
    )
    qn_node = cell_config.get("qn_output_node", "Q_N")
    # The template already loads the measured output (CLoad_Q on
    # %%OUTPUT_NODE%%). The second load goes on the OTHER output: Q_N for a
    # Q arc, Q for a Q_N arc. Placing it on qn_node unconditionally put
    # 2*C on Q_N and left Q unloaded for every Q_N-output arc.
    q_node = cell_config.get("q_output_node", "Q")
    second = q_node if out.upper() == qn_node.upper() else qn_node
    qn_load_block = (
        f"CLoad_QN {second} VSS {{C_LOAD}}"
        if cell_config.get("has_qn_output", True)
        else "* QN output is not present."
    ) + sense_line
    netlist_string = netlist_string.replace('%%QN_LOAD_BLOCK%%', qn_load_block)
    if arc_config.get("type") in _POWER_TYPES:
        netlist_string = _restrict_saves(netlist_string)
    return netlist_string


_POWER_TYPES = ("leakage_power", "internal_power", "hidden_power_internal")


def _restrict_saves(netlist):
    """Power decks: save only top-level node voltages and source branch
    currents (all the measurement reads). '.save_currents' plus the default
    save-all keeps every PEX node and device current: ~16 GB per 10 us
    hidden sim, which OOM-kills pool workers and hangs pool.map."""
    if os.environ.get("TULIP_SAVE_ALL", "0") == "1":
        return netlist
    nodes, vsrc = set(), []
    depth = 0
    for raw in netlist.splitlines():
        line = raw.strip()
        low = line.lower()
        if low.startswith(".subckt"):
            depth += 1
            continue
        if low.startswith(".ends"):
            depth = max(0, depth - 1)
            continue
        if depth or not line or line[0] in "*.+":
            continue
        tok = line.split()
        kind = tok[0][0].upper()
        if kind == "V" and len(tok) >= 3:
            vsrc.append(tok[0])
            nodes.update(tok[1:3])
        elif kind in "RC" and len(tok) >= 3:
            nodes.update(tok[1:3])
        elif kind == "X" and len(tok) >= 3:
            nodes.update(tok[1:-1])
    nodes.discard("0")
    save = (".save " + " ".join(f"v({n})" for n in sorted(nodes))
            + " " + " ".join(f"i({v})" for v in vsrc))
    netlist = re.sub(r"^\.save_currents\s*$", "", netlist,
                     flags=re.MULTILINE | re.IGNORECASE)
    return re.sub(r"^(\.tran\b)", save + "\n\\1", netlist, count=1,
                  flags=re.MULTILINE | re.IGNORECASE)


def finalize_netlist(netlist_string, vdd_str, load_str,
                     clock_slew_str, data_slew_str):
    netlist_string = (
        netlist_string
        .replace('{VDD_VAL}', vdd_str)
        .replace('{C_LOAD}', f"{load_str}p")
        .replace('{CLOCK_SLEW}', clock_slew_str)
        .replace('{DATA_SLEW}', data_slew_str)
    )
    if os.environ.get("TULIP_INPUTS_TO_GND", "0") == "1":
        # Rewrite "V_<pin> <node> VSS " -> "V_<pin> <node> 0 "
        netlist_string = re.sub(
            r'(?m)^(V_\S+\s+\S+\s+)VSS(\s)',
            r'\g<1>0\g<2>',
            netlist_string,
        )
    return netlist_string


def prepare_netlist(args):
    """
    Build and finalize SPICE netlist.
    Args:
        args: Full args tuple from run_single_simulation
    Returns:
        str: Complete SPICE netlist ready for simulation
    """
    # Build simulation conditions
    arc_sim_cfg = args[7].get('_sim_cfg', {})
    sim_conditions = {
        'temp_str': args[0],
        'vdd_str': args[1],
        'clock_slew_str': args[2],
        'data_slew_str': args[3],
        'load_str': args[4],
        'delta_ns': args[5],
        'preload_clk_time_ns': arc_sim_cfg.get('preload_clk_time_ns', 5.0),
        'measure_clk_time_ns': arc_sim_cfg.get('measure_clk_time_ns', 15.0),
    }
    # Generate netlist
    netlist_string = build_netlist(
        args[8],  # spice_template_str
        sim_conditions,
        args[7],  # arc_config
        args[6]   # cell_config
    )
    # Finalize netlist
    final = finalize_netlist(
        netlist_string,
        args[1],  # vdd_str
        args[4],  # load_str
        args[2],  # clock_slew_str
        args[3]   # data_slew_str
    )
    dump_deck(final, args[6], args[7], sim_conditions)
    return final


def dump_deck(netlist_string, cell_config, arc_config, sim_conditions):
    """Write the finalized deck to the --dump-decks directory, if set."""
    deck_dir = arc_config.get('_sim_cfg', {}).get('dump_decks')
    if not deck_dir:
        return
    os.makedirs(deck_dir, exist_ok=True)
    name = (f"{cell_config.get('name', 'cell')}"
            f"_{arc_config.get('name', 'arc')}"
            f"_L{sim_conditions.get('load_str', 'x')}"
            f"_S{sim_conditions.get('data_slew_str', 'x')}.sp")
    with open(os.path.join(deck_dir, name.replace('/', '_')),
              'w', encoding='utf-8') as fdeck:
        fdeck.write(netlist_string)
