"""Power characterization package.

Public API is unchanged:
``from .characterizer_power import characterize_power``
keeps working because this package re-exports it.

Handles three arc types: leakage_power, internal_power and
hidden_power_internal. Timing-only distributions may omit this
package entirely; callers guard the import.
"""

from .hidden import (
    _characterize_hidden,
)
from .internal import (
    _characterize_internal,
)
from .leakage import (
    _characterize_leakage,
)


def characterize_power(corner_config, sweep_params, cli_args, tables):
    """Top-level dispatch for power arcs.

    corner_config keys:
        temp_str, vdd, cell_config, arc_config, spice_template,
        arc_name, arc_type
    sweep_params keys:
        loads_to_sweep, clock_slew_ns_list, data_slew_ns_list,
        output_loads, slew_scale_factor, do_power_char,
        save_debug_plots, num_processes, get_cfg
    """
    arc_type = corner_config["arc_type"]

    if arc_type == "leakage_power":
        _characterize_leakage(corner_config, sweep_params, cli_args, tables)
    elif arc_type == "hidden_power_internal":
        _characterize_hidden(corner_config, sweep_params, cli_args, tables)
    elif arc_type == "internal_power":
        _characterize_internal(corner_config, sweep_params, cli_args, tables)
    else:
        print(f"  [POWER] Unknown arc_type: {arc_type}")


__all__ = ["characterize_power"]
