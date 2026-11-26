"""
Liberty Models
"""


from datetime import datetime
import numpy as np


# --- Helpers ---
def fmt_arr(arr):
    """Format a 1-D sequence as a comma-separated Liberty value list."""
    return ", ".join(f"{val:.6g}" for val in arr)


def fmt_matrix(matrix):
    """Format a 2-D sequence as quoted, backslash-joined Liberty rows."""
    lines = [f'"{fmt_arr(row)}"' for row in matrix]
    return ", \\\n            ".join(lines)


def import_datetime():
    """Return the current timestamp for the library header."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --- Unit Classes ---
class Unit:
    """A Liberty unit attribute: numeric value plus unit suffix."""
    def __init__(self, value, unit_str):
        self.value = value
        self.unit_str = unit_str

    def str_spice(self):
        return f"{self.unit_str}"


class UnitsSettings:
    """Library-level unit declarations (time, capacitance, voltage...)."""
    def __init__(self, **_):
        self.time = Unit(1e-9, "ns")
        self.voltage = Unit(1.0, "V")
        self.current = Unit(1e-6, "uA")
        self.resistance = Unit(1000, "kohm")
        self.capacitance = Unit(1e-12, "pf")
        self.power = Unit(1e-12, "pW")


# --- Core Liberty Models ---
class LookupTable1D:
    """Models a 1D table (e.g., mpw, passive power)"""
    def __init__(self, name, template_name, index_1, values):
        self.name = name
        self.template_name = template_name
        self.index_1 = index_1
        self.values = values

    def __str__(self):
        return (f'{self.name} ({self.template_name}) {{\n'
                f'index_1 ("{fmt_arr(self.index_1)}");\n'
                f'values ("{fmt_arr(self.values)}");\n'
                f'}}')


class LookupTable:
    """Models a 2D lookup table"""
    def __init__(self, name, template_name, indices, values):
        self.name = name
        self.template_name = template_name
        self.index_1 = indices[0]
        self.index_2 = indices[1]
        self.values = values

    def __str__(self):
        return (f'{self.name} ({self.template_name}) {{\n'
                f'index_1 ("{fmt_arr(self.index_1)}");\n'
                f'index_2 ("{fmt_arr(self.index_2)}");\n'
                f'values (\\\n {fmt_matrix(self.values)} \\\n);\n'
                f'}}')


class TimingGroup:
    """Models timing () group"""
    def __init__(self, related_pin, timing_type, timing_sense):
        self.related_pin = related_pin
        self.timing_type = timing_type
        self.timing_sense = timing_sense
        self.when_condition = None
        self.sdf_cond = None
        self.sdf_edges = None
        self.tables = []

    def add_table(self, table):
        self.tables.append(table)

    def __str__(self):
        # Reference (Liberate) emits timing attributes alphabetically:
        # related_pin, sdf_cond, sdf_edges, timing_sense, timing_type, when.
        # timing_type is suppressed when None/'' (ref omits it on the
        # clock-gate GCLK combinational arc).
        lines = ['      timing () {',
                 f'related_pin : "{self.related_pin}";']
        if self.sdf_cond:
            lines.append(f'sdf_cond : "{self.sdf_cond}";')
        if self.sdf_edges:
            lines.append(f'sdf_edges : {self.sdf_edges};')
        if self.timing_sense:
            lines.append(f'timing_sense : {self.timing_sense};')
        if self.timing_type:
            lines.append(f'timing_type : {self.timing_type};')
        if self.when_condition:
            lines.append(f'when : "{self.when_condition}";')
        for tbl in self.tables:
            lines.append(str(tbl))
        lines.append('      }')
        return '\n'.join([line for line in lines if line.strip()])


class InternalPowerGroup:
    """Models internal_power () group"""
    def __init__(self, related_pin):
        self.related_pin = related_pin
        self.when_condition = None
        self.tables = []

    def add_table(self, table):
        self.tables.append(table)

    def __str__(self):
        lines = ['      internal_power () {']
        if self.related_pin:
            lines.append(f'related_pin : "{self.related_pin}";')
        if self.when_condition:
            lines.append(f'when : "{self.when_condition}";')
        for tbl in self.tables:
            lines.append(str(tbl))
        lines.append('}')
        return '\n'.join(lines)


class LeakagePowerGroup:
    """Models leakage_power () group"""
    def __init__(self, value, when_condition=None):
        self.value = value
        self.when_condition = when_condition

    def __str__(self):
        lines = ['    leakage_power () {',
                 f'value : {self.value:.6g};']
        if self.when_condition:
            lines.append(f'when : "{self.when_condition}";')
        lines.append('}')
        return '\n'.join(lines)


class Pin:
    """Models a pin in a cell"""
    def __init__(self, name, direction, capacitance=0.001):
        self.name = name
        self.direction = direction
        self.capacitance = capacitance
        self.rise_capacitance = None
        self.fall_capacitance = None
        self.rise_capacitance_range = None  # (min, max) tuple
        self.fall_capacitance_range = None  # (min, max) tuple
        self.function = None
        self.clock = False
        self.nextstate_type = None
        self.internal_node = None       # direction : internal pins
        self.state_function = None      # clock-gate GCLK
        self.clock_gate_out_pin = False
        self.clock_gate_clock_pin = False
        self.clock_gate_enable_pin = False
        self.clock_gate_test_pin = False
        self.max_capacitance = None
        self.min_capacitance = None
        self.max_transition = None  # For input pins
        self.three_state = None
        self.timing_groups = []
        self.power_groups = []

    def add_timing_group(self, group):
        self.timing_groups.append(group)

    def add_power_group(self, group):
        self.power_groups.append(group)

    def cap_lines(self, lines):
        """capacitance / rise / range / fall / range in reference order."""
        cap_val = (
            self.capacitance
            if (self.capacitance is not None
                and not np.isnan(self.capacitance))
            else 0.001
        )
        lines.append(f'capacitance : {cap_val:.6g};')
        rise_cap = self.rise_capacitance
        if rise_cap is not None and not np.isnan(rise_cap):
            lines.append(f'rise_capacitance : {rise_cap:.6g};')
        if self.rise_capacitance_range is not None:
            cap_lo, cap_hi = self.rise_capacitance_range
            lines.append(
                f'rise_capacitance_range ({cap_lo:.6g}, {cap_hi:.6g});'
            )
        fall_cap = self.fall_capacitance
        if fall_cap is not None and not np.isnan(fall_cap):
            lines.append(f'fall_capacitance : {fall_cap:.6g};')
        if self.fall_capacitance_range is not None:
            cap_lo, cap_hi = self.fall_capacitance_range
            lines.append(
                f'fall_capacitance_range ({cap_lo:.6g}, {cap_hi:.6g});'
            )

    def clock_gate_lines(self):
        """Clock-gating pin flags, emitted only when set."""
        flags = (
            ('clock', 'clock'),
            ('clock_gate_clock_pin', 'clock_gate_clock_pin'),
            ('clock_gate_enable_pin', 'clock_gate_enable_pin'),
            ('clock_gate_out_pin', 'clock_gate_out_pin'),
            ('clock_gate_test_pin', 'clock_gate_test_pin'),
        )
        return [f'{name} : true;'
                for attr, name in flags if getattr(self, attr)]

    def output_lines(self):
        """Attributes emitted for output pins, in reference order."""
        lines = []
        if self.function:
            lines.append(f'function : "{self.function}";')
        if self.min_capacitance is not None:
            lines.append(f'min_capacitance : {self.min_capacitance:.6g};')
        if self.three_state:
            lines.append(f'three_state : "{self.three_state}";')
        if self.state_function:
            lines.append(f'state_function : "{self.state_function}";')
        if self.max_capacitance is not None:
            lines.append(f'max_capacitance : {self.max_capacitance:.6g};')
        # Tristate outputs carry pin capacitance in the reference.
        if (self.capacitance is not None
                and not np.isnan(self.capacitance)
                and (self.rise_capacitance is not None
                     or self.fall_capacitance is not None)):
            self.cap_lines(lines)
        return lines

    def __str__(self):
        # Reference attribute order.
        # inputs:  clock, direction, nextstate_type, max_transition,
        #          capacitance, rise_capacitance, rise_capacitance_range,
        #          fall_capacitance, fall_capacitance_range
        # outputs: direction, function, min_capacitance, three_state,
        #          max_capacitance [, capacitance block for tristate]
        lines = [f'pin ({self.name}) {{']
        lines.extend(self.clock_gate_lines())
        lines.append(f'direction : "{self.direction}";')
        if self.direction == "internal":
            if self.internal_node:
                lines.append(f'internal_node : "{self.internal_node}";')
        if self.direction == "output":
            lines.extend(self.output_lines())
        if self.direction == "input":
            if self.nextstate_type:
                lines.append(f'nextstate_type : "{self.nextstate_type}";')
            if self.max_transition is not None:
                lines.append(f'max_transition : {self.max_transition:.6g};')
            self.cap_lines(lines)
        if self.direction == "inout":
            # e.g. sighold SH in the reference: direction + capacitances.
            self.cap_lines(lines)
        for timing_grp in self.timing_groups:
            lines.append(str(timing_grp))
        for power_grp in self.power_groups:
            lines.append(str(power_grp))
        lines.append('    }')
        return '\n'.join(lines)


class Cell:
    """Models a cell in a library"""
    def __init__(self, name, area=0.0):
        self.name = name
        self.area = area
        self.cell_footprint = None
        self.cell_leakage_power = None  # Average leakage
        self.clock_gating_integrated_cell = None
        self.dont_touch = False
        self.dont_use = False
        self.is_decap_cell = False
        self.is_filler_cell = False
        self.pins = {}
        self.templates_used = set()
        self.leakage_groups = []
        self.attributes = []
        self.pre_pin_groups = []
        self.ff_group = None

    def add_attribute(self, name, value):
        """Adds a generic attribute or group to the cell."""
        if value.strip().startswith('{'):
            self.attributes.append(f"    {name} {value}")
        else:
            self.attributes.append(f"    {name} : {value};")

    def get_or_create_pin(self, name, direction="input"):
        if name not in self.pins:
            self.pins[name] = Pin(name, direction)
        return self.pins[name]

    def add_template(self, template_def):
        self.templates_used.add(template_def)

    def add_leakage_group(self, group):
        self.leakage_groups.append(group)

    def templates(self):
        return list(self.templates_used)

    def flag_lines(self):
        """Boolean cell attributes, emitted only when set."""
        flags = (
            ('dont_touch', 'dont_touch'),
            ('dont_use', 'dont_use'),
            ('is_decap_cell', 'is_decap_cell'),
            ('is_filler_cell', 'is_filler_cell'),
        )
        return [f'    {name} : true;'
                for attr, name in flags if getattr(self, attr)]

    def __str__(self):
        lines = [
            f'  cell ({self.name}) {{',
            f'    area : {self.area};'
        ]
        # Cell footprint (if set)
        if self.cell_footprint:
            lines.append(f'cell_footprint : "{self.cell_footprint}";')
        # Cell leakage power (average)
        if self.cell_leakage_power is not None:
            lines.append(
                f'cell_leakage_power : {self.cell_leakage_power:.6g};'
            )
        elif self.leakage_groups:
            # Calculate average from leakage groups
            avg_leak = (
                sum(grp.value for grp in self.leakage_groups)
                / len(self.leakage_groups)
            )
            lines.append(f'cell_leakage_power : {avg_leak:.6g};')
        if self.clock_gating_integrated_cell:
            lines.append(
                '    clock_gating_integrated_cell : '
                f'"{self.clock_gating_integrated_cell}";'
            )
        lines.extend(self.flag_lines())
        # Print leakage power groups
        for leak_grp in self.leakage_groups:
            lines.append(str(leak_grp))
        # Pre-pin groups (e.g. statetable on clock-gating cells sits
        # between the leakage groups and the pins in the reference).
        for grp in self.pre_pin_groups:
            lines.append(grp)
        # Print pins: internal, then outputs, inouts, inputs, each
        # alphabetical -- matches reference ordering (lgcp: int_GATE,
        # GCLK, CLK, GATE; combinational: X, A1, A2, B1).
        for direction in ("internal", "output", "inout", "input"):
            for pin in sorted(
                    (candidate for candidate in self.pins.values()
                     if candidate.direction == direction),
                    key=lambda pin_obj: pin_obj.name):
                lines.append(str(pin))
        # Print FF group if present
        for attr in self.attributes:
            lines.append(attr)

        lines.append('  }')
        return '\n'.join(lines)


class Library:
    """Models a Liberty library"""
    def __init__(self, name, **attrs):
        self.name = name
        self.cells = {}
        self.delay_model = 'table_lookup'
        self.revision = '0.0'
        # Verbatim static groups (operating_conditions, wire_load tables,
        # wire_load_selection + trailing default_* attrs). Loaded from an
        # external file by the generator; emitted between the header
        # attributes and the templates, matching the reference layout.
        self.static_groups = None
        # Library-level defaults (reference values for SG13G2 typ corner).
        self.default_cell_leakage_power = 0
        self.default_fanout_load = 1
        self.default_inout_pin_cap = 0.006
        self.default_input_pin_cap = 0.006
        self.default_leakage_power_density = 0
        self.default_max_capacitance = 0.3
        self.default_max_fanout = 8
        self.default_max_transition = 2.5074
        self.default_output_pin_cap = 0
        self.default_wire_load_area = 0.29
        self.default_wire_load_capacitance = 0.0003
        self.default_wire_load_mode = 'top'
        self.default_wire_load_resistance = 0.0057
        self.in_place_swap_mode = 'match_footprint'
        self.slew_derate_from_library = 1
        self.units = UnitsSettings()
        # Threshold settings (match IHP reference)
        self.slew_lower_threshold_pct_rise = 20
        self.slew_upper_threshold_pct_rise = 80
        self.slew_lower_threshold_pct_fall = 20
        self.slew_upper_threshold_pct_fall = 80
        self.input_threshold_pct_rise = 50
        self.input_threshold_pct_fall = 50
        self.output_threshold_pct_rise = 50
        self.output_threshold_pct_fall = 50
        # Dynamic from attrs
        self.nom_process = 1
        self.nom_voltage = attrs.get('nom_voltage', 1.2)
        self.nom_temperature = attrs.get('nom_temperature', 25)

    def add_cell(self, cell):
        self.cells[cell.name] = cell

    def __str__(self):
        def spice_unit(unit):
            return unit.str_spice()
        # Header attribute order mirrors the reference lib exactly:
        # comment, date, revision, delay_model, units, default_* (alpha),
        # in_place_swap_mode, thresholds/nom (alpha), slew_*, then the
        # static groups (operating_conditions .. default_wire_load_*),
        # then templates, then cells.
        lib_str = [
            f'library ({self.name}) {{',
            '  comment : "Generated by Tulip Characterizer";',
            f'  date : "{import_datetime()}";',
            f'  revision : "{self.revision}";',
            f'  delay_model : {self.delay_model};',
            (f'  capacitive_load_unit '
             f'(1,{spice_unit(self.units.capacitance)});'),
            f'  current_unit : "1{spice_unit(self.units.current)}";',
            f'  leakage_power_unit : "1{spice_unit(self.units.power)}";',
            (f'  pulling_resistance_unit : '
             f'"1{spice_unit(self.units.resistance)}";'),
            f'  time_unit : "1{spice_unit(self.units.time)}";',
            f'  voltage_unit : "1{spice_unit(self.units.voltage)}";',
            (f'  default_cell_leakage_power : '
             f'{self.default_cell_leakage_power};'),
            f'  default_fanout_load : {self.default_fanout_load};',
            f'  default_inout_pin_cap : {self.default_inout_pin_cap};',
            f'  default_input_pin_cap : {self.default_input_pin_cap};',
            (f'  default_leakage_power_density : '
             f'{self.default_leakage_power_density};'),
            f'  default_max_capacitance : {self.default_max_capacitance};',
            f'  default_max_fanout : {self.default_max_fanout};',
            f'  default_max_transition : {self.default_max_transition};',
            f'  default_output_pin_cap : {self.default_output_pin_cap};',
            f'  default_wire_load_area : {self.default_wire_load_area};',
            (f'  default_wire_load_capacitance : '
             f'{self.default_wire_load_capacitance};'),
            f'  default_wire_load_mode : "{self.default_wire_load_mode}";',
            (f'  default_wire_load_resistance : '
             f'{self.default_wire_load_resistance};'),
            f'  in_place_swap_mode : {self.in_place_swap_mode};',
            f'  input_threshold_pct_fall : {self.input_threshold_pct_fall};',
            f'  input_threshold_pct_rise : {self.input_threshold_pct_rise};',
            f'  nom_process : {self.nom_process};',
            f'  nom_temperature : {self.nom_temperature:g};',
            f'  nom_voltage : {self.nom_voltage:g};',
            f'  output_threshold_pct_fall : {self.output_threshold_pct_fall};',
            f'  output_threshold_pct_rise : {self.output_threshold_pct_rise};',
            (f'  slew_derate_from_library : '
             f'{self.slew_derate_from_library};'),
            (f'  slew_lower_threshold_pct_fall'
             f' : {self.slew_lower_threshold_pct_fall};'),
            (f'  slew_lower_threshold_pct_rise'
             f' : {self.slew_lower_threshold_pct_rise};'),
            (f'  slew_upper_threshold_pct_fall'
             f' : {self.slew_upper_threshold_pct_fall};'),
            (f'  slew_upper_threshold_pct_rise'
             f' : {self.slew_upper_threshold_pct_rise};'),
        ]
        # operating_conditions generated from the characterized corner
        # (nom_* come from config temp_values/vdd_values/nom_process),
        # so a corner change never desyncs from a static text file.
        oc_name = self.name
        lib_str.append(f'  operating_conditions ({oc_name}) {{')
        lib_str.append(f'    process : {self.nom_process};')
        lib_str.append(f'    temperature : {self.nom_temperature:g};')
        lib_str.append(f'    voltage : {self.nom_voltage:g};')
        lib_str.append('  }')
        if self.static_groups:
            lib_str.append(self.static_groups.rstrip('\n'))
        lib_str.append(f'  default_operating_conditions : {oc_name};')
        # Collect and print templates
        all_templates = set()
        for cell in self.cells.values():
            all_templates.update(cell.templates())
        for tmpl in sorted(list(all_templates)):
            lib_str.append(tmpl)
        # Print cells
        lib_str.append('')
        for cell in self.cells.values():
            lib_str.append(str(cell))
        lib_str.append('}')
        return '\n'.join(lib_str)
