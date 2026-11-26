"""Liberty library generation.

Run via the ``generate_lib.py`` launcher in this directory, which
keeps the historical command line working.
"""

import json
import pathlib
import re
from ..liberty_models import (
    Library,
)
from .constants import (
    CONFIG_FILE,
    LEF_AREAS,
    LIB_NAME,
    TARGET_TEMP,
    TARGET_VDD,
)
from .cells import process_cell


def load_lef_areas(lef_path):
    """Map MACRO name -> SIZE x*y (um^2) from a LEF file."""
    areas = {}
    if not lef_path:
        return areas
    try:
        with open(lef_path, encoding='utf-8') as flef:
            txt = flef.read()
    except OSError as exc:
        print(f"Warning: cannot read LEF '{lef_path}': {exc}")
        return areas
    for match in re.finditer(
            r'^MACRO (\S+)\s.*?^\s*SIZE\s+([\d.]+)\s+BY\s+([\d.]+)',
            txt, re.S | re.M):
        areas[match.group(1)] = round(
            float(match.group(2)) * float(match.group(3)), 4)
    return areas


def main():
    """Entry point — build library and write .lib file."""
    with open(CONFIG_FILE, encoding='utf-8') as fconf:
        config = json.load(fconf)
    settings = config['global_settings']
    lib_name = settings.get('lib_name', LIB_NAME)
    nom_process = settings.get('nom_process', 1)
    my_lib = Library(
        lib_name,
        nom_voltage=float(TARGET_VDD),
        nom_temperature=float(TARGET_TEMP)
    )
    my_lib.nom_process = nom_process
    my_lib.revision = settings.get('lib_revision', '0.0')
    # Static operating_conditions / wire_load / wire_load_selection groups
    # (verbatim reference block); ships next to the config.
    static_file = pathlib.Path(
        settings.get('lib_static_groups_file', 'lib_static_groups.txt')
    )
    if static_file.exists():
        my_lib.static_groups = static_file.read_text(encoding='utf-8')
    slew_lo = settings.get("slew_lower_threshold_pct", 20)
    slew_hi = settings.get("slew_upper_threshold_pct", 80)
    delay_t = settings.get("delay_threshold_pct", 50)
    my_lib.slew_lower_threshold_pct_rise = slew_lo
    my_lib.slew_upper_threshold_pct_rise = slew_hi
    my_lib.slew_lower_threshold_pct_fall = slew_lo
    my_lib.slew_upper_threshold_pct_fall = slew_hi
    my_lib.input_threshold_pct_rise = delay_t
    my_lib.input_threshold_pct_fall = delay_t
    my_lib.output_threshold_pct_rise = delay_t
    my_lib.output_threshold_pct_fall = delay_t
    LEF_AREAS.update(load_lef_areas(settings.get('lef_file')))
    if LEF_AREAS:
        print(f"Loaded {len(LEF_AREAS)} cell areas from LEF")
    for cell_conf in config['cells']:
        process_cell(cell_conf, my_lib)
    with open(f"{lib_name}.lib", "w", encoding='utf-8') as flib:
        flib.write(str(my_lib))
    print(f"\nSuccessfully generated {lib_name}.lib")


__all__ = ["main"]
