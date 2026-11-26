"""Cell assembly: pin collection, ff/latch/test-cell groups,
post-processing."""

from ..liberty_models import (
    Cell,
)
from .context import PinSets
from .constants import (
    LEF_AREAS,
    RESULTS_DIR,
    _config,
)
from .arcs import (
    is_active_low,
    merge_delay_groups,
    process_arc,
)

try:  # optional: timing-only builds omit this module
    from .power import (
        emit_hidden_defaults,
        merge_active_power_groups,
    )
    HAS_POWER = True
except ImportError:  # pragma: no cover
    HAS_POWER = False


def generate_ff_group(cell_conf):
    """Generate FF group string from config or use defaults."""
    ff_def = cell_conf.get('ff_definition')
    if ff_def:
        return ff_def

    func_map = cell_conf.get('function_mapping', {})
    has_reset = func_map.get('reset_b') is not None
    has_set = func_map.get('set_b') is not None
    has_scan = any('SC' in arc.get('data_signal', '').upper()
                   or 'SC' in arc.get('name', '').upper()
                   for arc in cell_conf.get('arcs', []))

    # Reference format: ff (IQ,IQN) with alphabetical attributes and a
    # compact next_state expression, e.g. (SCE*SCD)+(!SCE*D).
    next_state = '(SCE*SCD)+(!SCE*D)' if has_scan else 'D'
    lines = ['    ff (IQ,IQN) {']
    if has_reset:
        lines.append('      clear : "!RESET_B";')
    if has_reset and has_set:
        lines.append('      clear_preset_var1 : "H";')
        lines.append('      clear_preset_var2 : "L";')
    lines.append('      clocked_on : "CLK";')
    lines.append(f'      next_state : "{next_state}";')
    if has_set:
        lines.append('      preset : "!SET_B";')
    lines.append('    }')
    return '\n'.join(lines)


def _cell_has_scan(cell_conf):
    return any('SC' in arc.get('data_signal', '').upper()
               or 'SC' in arc.get('name', '').upper()
               for arc in cell_conf.get('arcs', []))


def generate_test_cell_group(cell_conf):
    """Synthesize the test_cell () group for scan cells. Mirrors the
    reference: scan pins carry signal_type, the embedded ff drops the
    scan mux (next_state : "D")."""
    func_map = cell_conf.get('function_mapping', {})
    has_reset = func_map.get('reset_b') is not None
    has_set = func_map.get('set_b') is not None
    has_qn = cell_conf.get('has_qn_output', True)

    def pin_block(name, direction, function=None, signal_type=None):
        blk = [f'      pin ({name}) {{',
               f'        direction : "{direction}";']
        if function:
            blk.append(f'        function : "{function}";')
        if signal_type:
            blk.append(f'        signal_type : {signal_type};')
        blk.append('      }')
        return '\n'.join(blk)

    lines = ['    test_cell () {']
    lines.append(pin_block('Q', 'output', 'IQ', 'test_scan_out'))
    if has_qn:
        lines.append(
            pin_block('Q_N', 'output', 'IQN', 'test_scan_out_inverted')
        )
    lines.append(pin_block('CLK', 'input'))
    lines.append(pin_block('D', 'input'))
    if has_reset:
        lines.append(pin_block(func_map['reset_b'], 'input'))
    lines.append(pin_block('SCD', 'input', signal_type='test_scan_in'))
    lines.append(pin_block('SCE', 'input', signal_type='test_scan_enable'))
    if has_set:
        lines.append(pin_block(func_map['set_b'], 'input'))
    ff_lines = ['      ff (IQ,IQN) {']
    if has_reset:
        ff_lines.append(f'        clear : "!{func_map["reset_b"]}";')
    if has_reset and has_set:
        ff_lines.append('        clear_preset_var1 : "H";')
        ff_lines.append('        clear_preset_var2 : "L";')
    ff_lines.append('        clocked_on : "CLK";')
    ff_lines.append('        next_state : "D";')
    if has_set:
        ff_lines.append(f'        preset : "!{func_map["set_b"]}";')
    ff_lines.append('      }')
    lines.append('\n'.join(ff_lines))
    lines.append('    }')
    return '\n'.join(lines)


def generate_latch_group(cell_conf):
    """Generate latch group string from config or use defaults."""
    latch_def = cell_conf.get('latch_definition')
    if latch_def:
        return f"    {latch_def}"

    gate_pin = "GATE"
    data_pin = "D"
    for arc in cell_conf.get('arcs', []):
        if arc.get('gate_signal'):
            gate_pin = arc['gate_signal']
        if arc.get('type') in ['setup_latch', 'hold_latch']:
            data_pin = arc.get('data_signal', 'D')

    # enable is the transparent-when-true expression: active-low gates
    # (GATE_N) are transparent when low -> "!GATE_N" (reference: dllr).
    enable_expr = f'!{gate_pin}' if is_active_low(gate_pin) else gate_pin

    func_map = cell_conf.get('function_mapping', {})
    reset_pin = func_map.get('reset_b')
    set_pin = func_map.get('set_b')

    # Reference format: latch (IQ,IQN) with alphabetical attributes:
    # clear, data_in, enable, preset.
    lines = ['    latch (IQ,IQN) {']
    if reset_pin:
        lines.append(f'      clear : "!{reset_pin}";')
    lines.append(f'      data_in : "{data_pin}";')
    lines.append(f'      enable : "{enable_expr}";')
    if set_pin:
        lines.append(f'      preset : "!{set_pin}";')
    lines.append('    }')
    return '\n'.join(lines)


def _collect_pin_info(cell_conf):
    """First pass — returns PinSets."""
    input_pins = set()
    output_pins = set()
    clock_pins = set()
    is_sequential = False

    for arc in cell_conf['arcs']:
        out_pin = arc.get('output_signal')
        in_pin = arc.get('data_signal')
        clk_pin = arc.get('clock_signal') or arc.get('gate_signal')
        rel_pin = arc.get('related_pin')

        if out_pin:
            output_pins.add(out_pin)
        if in_pin:
            input_pins.add(in_pin)
        if clk_pin:
            clock_pins.add(clk_pin)
            is_sequential = True
        if rel_pin and rel_pin not in output_pins:
            if arc.get('type') in ('setup', 'hold', 'recovery', 'removal'):
                clock_pins.add(rel_pin)

    return PinSets(input_pins, output_pins, clock_pins, is_sequential)


def _apply_output_pin_defaults(pin_obj, pin_name, output_pin_attrs):
    """Apply function / min/max cap to output pins from config or defaults."""
    # Reference: every output pin carries min_capacitance (min of the
    # load axis) and max_capacitance (max of the load axis; per-cell
    # overrides, e.g. ebufn max_capacitance 0.6, come from config).
    loads = _config['global_settings'].get('output_loads', ['0.001', '0.3'])
    default_min_cap = min(float(x) for x in loads)
    default_max_cap = max(float(x) for x in loads)
    if pin_name in output_pin_attrs:
        attrs = output_pin_attrs[pin_name]
        if 'function' in attrs:
            pin_obj.function = attrs['function']
        if 'state_function' in attrs:
            # Clock-gate outputs (GCLK) use state_function, not function.
            pin_obj.state_function = attrs['state_function']
            pin_obj.function = None
        if attrs.get('clock_gate_out_pin'):
            pin_obj.clock_gate_out_pin = True
        if 'three_state' in attrs:
            pin_obj.three_state = attrs['three_state']
        pin_obj.min_capacitance = attrs.get(
            'min_capacitance', default_min_cap
        )
        pin_obj.max_capacitance = attrs.get(
            'max_capacitance', default_max_cap
        )
        return
    pin_obj.min_capacitance = default_min_cap
    pin_obj.max_capacitance = default_max_cap

    default_functions = {"Q": "IQ", "Q_N": "IQN", "QN": "IQN", "Y": "!A"}
    if pin_name in default_functions:
        pin_obj.function = default_functions[pin_name]
    else:
        print(f"  WARNING: No function defined for output pin '{pin_name}'. "
              f"Add to output_pin_attributes in config.")


def _apply_post_processing(cell_conf, lib_cell, pin_sets: PinSets):
    """Apply pin attributes, clock flags, and FF/latch groups."""
    output_pin_attrs = cell_conf.get('output_pin_attributes', {})
    input_pin_attrs = cell_conf.get('input_pin_attributes', {})

    # Reference: every input pin carries max_transition (default = max of
    # the global data slew axis); D/SCD/SCE on sequential cells carry
    # nextstate_type.
    default_max_trans = max(
        float(s) for s in _config['global_settings'].get(
            'data_slew_ns', [2.5074]
        )
    )
    default_nextstate = {'D': 'data', 'SCD': 'scan_in', 'SCE': 'scan_enable'}
    # nextstate_type applies to FF data pins only -- reference latch D pins
    # (dlhq/dlhr/dllr/dllrq) do not carry it.
    is_ff_cell = (
        pin_sets.is_sequential and cell_conf.get('cell_type') != 'latch'
    )

    for pin_name in pin_sets.input_pins | pin_sets.clock_pins:
        pin_obj = lib_cell.get_or_create_pin(pin_name, "input")
        attrs = input_pin_attrs.get(pin_name, {})
        pin_obj.max_transition = attrs.get(
            'max_transition', default_max_trans
        )
        if 'nextstate_type' in attrs:
            pin_obj.nextstate_type = attrs['nextstate_type']
        elif is_ff_cell and pin_name in default_nextstate:
            pin_obj.nextstate_type = default_nextstate[pin_name]
        # Constraint arcs (setup/hold/mpw) live on input pins and are
        # simulated rise/fall separately -> merge into single groups
        # carrying rise_constraint + fall_constraint like the reference.
        merge_delay_groups(pin_obj)

    for pin_name in pin_sets.output_pins:
        pin_obj = lib_cell.get_or_create_pin(pin_name, "output")
        _apply_output_pin_defaults(pin_obj, pin_name, output_pin_attrs)
        merge_delay_groups(pin_obj)
        if HAS_POWER:
            merge_active_power_groups(pin_obj)

    for pin_name in pin_sets.clock_pins:
        # Reference marks all clocking pins (CLK, GATE, GATE_N) clock:true.
        lib_cell.get_or_create_pin(pin_name, "input").clock = True

    is_latch = cell_conf.get('cell_type') == 'latch'
    if is_latch:
        lib_cell.attributes.append(generate_latch_group(cell_conf))
        print(f"  Added latch() group for {cell_conf['name']}")
    elif pin_sets.is_sequential or 'ff_definition' in cell_conf:
        lib_cell.attributes.append(generate_ff_group(cell_conf))
        if _cell_has_scan(cell_conf):
            lib_cell.attributes.append(generate_test_cell_group(cell_conf))


def process_cell(cell_conf, my_lib):
    """Process a single cell config entry into my_lib."""
    cell_name = cell_conf['name']
    print(f"Processing Cell: {cell_name}...")

    base_name = cell_name.replace('_flat_pex', '').replace('_flat', '')
    area = cell_conf.get('area', LEF_AREAS.get(base_name, 0.0))
    if area == 0.0 and base_name not in LEF_AREAS:
        print(f"  Warning: no area for {cell_name} "
              f"(not in LEF, no config override)")
    lib_cell = Cell(cell_name, area=area)
    my_lib.add_cell(lib_cell)

    if 'cell_footprint' in cell_conf:
        lib_cell.cell_footprint = cell_conf['cell_footprint']

    # Explicit pin declarations for cells with no (or incomplete) arcs
    # (tiehi/tielo/sighold/antennanp/buf_1). Attributes are taken verbatim
    # from the config entry; no defaults are injected here so the emitted
    # pin matches the declaration exactly.
    for pdecl in cell_conf.get('pins', []):
        pin_obj = lib_cell.get_or_create_pin(
            pdecl['name'], pdecl.get('direction', 'input')
        )
        pin_obj.direction = pdecl.get('direction', 'input')
        for key in ('function', 'three_state', 'max_transition',
                    'capacitance', 'rise_capacitance', 'fall_capacitance',
                    'min_capacitance', 'max_capacitance', 'nextstate_type',
                    'internal_node', 'state_function', 'clock',
                    'clock_gate_out_pin', 'clock_gate_clock_pin',
                    'clock_gate_enable_pin', 'clock_gate_test_pin'):
            if key in pdecl:
                setattr(pin_obj, key, pdecl[key])

    # Cell-level scalar attributes and pre-pin groups (clock-gating cells:
    # clock_gating_integrated_cell, dont_use, statetable).
    if 'clock_gating_integrated_cell' in cell_conf:
        lib_cell.clock_gating_integrated_cell = (
            cell_conf['clock_gating_integrated_cell']
        )
    for flag in ('dont_touch', 'dont_use', 'is_decap_cell',
                 'is_filler_cell'):
        if cell_conf.get(flag):
            setattr(lib_cell, flag, True)
    if 'statetable_definition' in cell_conf:
        lib_cell.pre_pin_groups.append(cell_conf['statetable_definition'])

    pin_sets = _collect_pin_info(cell_conf)
    res_path = RESULTS_DIR / cell_name

    # Liberty/Liberate convention: when Q and Q_N switch in the same event,
    # the event energy is split over the switching outputs (Liberate divides
    # by switching_outputs). Tulip measures the whole event once per output
    # arc, so divide internal power of Q/Q_N cells by the number of outputs.
    # Override per arc or per cell with 'switching_outputs'.
    q_pin = cell_conf.get('q_output_node', 'Q')
    qn_pin = cell_conf.get('qn_output_node', 'Q_N')
    complementary = {q_pin, qn_pin} <= pin_sets.output_pins
    for arc in cell_conf['arcs']:
        if arc.get('type') == 'internal_power':
            n_sw = arc.get('switching_outputs', cell_conf.get(
                'switching_outputs',
                2 if (complementary
                      and arc.get('output_signal') in (q_pin, qn_pin))
                else 1))
            if n_sw != 1:
                arc = dict(arc, _switching_outputs=n_sw)
        process_arc(arc, lib_cell, res_path, pin_sets.clock_pins)
    if HAS_POWER:
        emit_hidden_defaults(cell_conf, lib_cell, res_path)

    _apply_post_processing(cell_conf, lib_cell, pin_sets)
