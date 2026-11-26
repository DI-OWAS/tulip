
import sys
from pathlib import Path
import argparse
import json
import os
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
# toml later
project_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project_root / 'src' / 'py'))

# import from our 'tulip' library
from tulip.core.characterizer import (  # noqa: E402  pylint: disable=wrong-import-position
    characterize_arc,
    CURVE_PLOT_DIR,
    DEBUG_PLOT_DIR,
    RESULTS_DIR
)
# --- Constants for input files ---
# Assumes these files are in the project root,
# where this script is typically run from
# SPICE_TEMPLATE_FILE = 'template.sp'
# DEFAULT_CONFIG_FILE = 'config.json'
# in run_characterization.py


SPICE_TEMPLATE_FILE = 'template_pex.sp'
DEFAULT_CONFIG_FILE = 'config.json'


def _run_single_point(args, config, spice_template, global_settings):
    """Execute a single-point simulation from --point CLI args."""
    temp, vdd, clk_slew, data_slew, load, delta = args.point

    cell_config = next(
        (c for c in config['cells'] if c['name'] == args.cell), None
    )
    if cell_config is None:
        print(
            f"ERROR: Cell '{args.cell}' not found in config.",
            file=sys.stderr
        )
        sys.exit(1)

    arc_config = next(
        (a for a in cell_config['arcs'] if a['name'] == args.arc), None
    )
    if arc_config is None:
        print(
            f"ERROR: Arc '{args.arc}' not found in "
            f"cell '{args.cell}'.",
            file=sys.stderr
        )
        sys.exit(1)

    point_settings = dict(global_settings)
    point_settings['temp_values'] = [float(temp)]
    point_settings['vdd_values'] = [vdd]
    point_settings['data_slew_ns'] = [float(data_slew)]
    point_settings['clock_slew_ns'] = [float(clk_slew)]
    point_settings['output_loads'] = [load]
    point_settings['sequential_loads'] = [load]
    point_settings['_single_point'] = True

    print("\n--- Single Point Simulation ---")
    print(f"  Cell : {args.cell}")
    print(f"  Arc  : {args.arc}")
    print(f"  Corner : T={temp}C  VDD={vdd}V  "
          f"CKSlew={clk_slew}ns  DSlew={data_slew}ns  "
          f"Load={load}pF  Delta={delta}ns")

    characterize_arc(
        cell_config, arc_config, spice_template,
        point_settings, args
    )


def main():
    """
    Main entry point for the characterization script.
    Parses arguments, loads configs, and
    orchestrates the characterization process.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run 4D SPICE characterization for standard cells "
            "with command-line overrides."
        ),
        formatter_class=argparse.RawTextHelpFormatter
    )
    # --- Standard Arguments ---
    parser.add_argument(
        '--config', type=str, default=DEFAULT_CONFIG_FILE,
        help=(
            f"Path to the JSON configuration file. "
            f"(Default: {DEFAULT_CONFIG_FILE})"
        )
    )
    parser.add_argument(
        '--debug', action='store_true',
        help=(
            "Save a waveform plot for every failed simulation point "
            "(Not yet implemented)."
        )
    )
    parser.add_argument(
        '--save-curves', action='store_true',
        help="Save the 2D timing curve plot for every corner."
    )
    parser.add_argument(
        '--char-power', action='store_true',
        help="Enable characterization of power (leakage and dynamic)."
    )

    # --- Override Arguments ---
    override_help = (
        "Override {0} from config file.\n "
        "Can be specified multiple times (e.g., --{1} val1 --{1} val2)."
    )
    parser.add_argument(
        '--temp', type=float, action='append',
        help=override_help.format('temperatures (C)', 'temp')
    )
    parser.add_argument(
        '--vdd', type=str, action='append',
        help=override_help.format('VDD values (V)', 'vdd')
    )
    parser.add_argument(
        '--slew', type=float, action='append',
        help=override_help.format('input slews (ns)', 'slew')
    )
    parser.add_argument(
        '--load', type=str, action='append',
        help=override_help.format('output loads (F)', 'load')
    )
    parser.add_argument(
        '--debug-plots', action='store_true',
        help=(
            "Save a representative transient waveform "
            "for each characterized arc."
        )
    )
    parser.add_argument('--cell', type=str, default=None,
                        help='Cell name for single-point simulation')
    parser.add_argument('--arc', type=str, default=None,
                        help='Arc name for single-point simulation')
    parser.add_argument(
        '--point', nargs=6,
        metavar=('TEMP', 'VDD', 'CKSLEW', 'DSLEW', 'LOAD', 'DELTA'),
        help=(
            'Single point: temp vdd ck_slew d_slew load delta_ns'
        )
    )
    parser.add_argument('--dump-decks', type=str, default=None,
                        metavar='DIR',
                        help="write every generated SPICE deck to DIR")
    args = parser.parse_args()

    # --- File Loading & Setup ---
    try:
        with open(args.config, 'r', encoding='utf-8') as fconf:
            config = json.load(fconf)
        with open(SPICE_TEMPLATE_FILE, 'r', encoding='utf-8') as ftmpl:
            spice_template = ftmpl.read()
    except FileNotFoundError as exc:
        print(
            f"ERROR: Missing required file: {exc.filename}. "
            "Make sure you are running from the project root.",
            file=sys.stderr
        )
        sys.exit(1)

    global_settings = config.get("global_settings", {})

    # --- Override Logic ---
    if args.temp:
        global_settings['temp_values'] = args.temp
    if args.vdd:
        global_settings['vdd_values'] = args.vdd
    if args.slew:
        global_settings['input_slew_ns'] = args.slew
    if args.load:
        global_settings['output_loads'] = args.load

    if args.char_power:
        print("INFO: Power characterization is ENABLED.")
    # --- Directory Creation ---
    print("INFO: Creating output directories...")
    for out_dir in [CURVE_PLOT_DIR, DEBUG_PLOT_DIR, RESULTS_DIR]:
        out_dir.mkdir(exist_ok=True)

    if args.dump_decks:
        config['global_settings']['dump_decks'] = args.dump_decks
        global_settings['dump_decks'] = args.dump_decks

    # --- Single Point Simulation ---
    if args.point:
        _run_single_point(args, config, spice_template, global_settings)
        sys.exit(0)
    # --- Main Characterization Loop ---
    for cell in config['cells']:
        for arc in cell['arcs']:
            characterize_arc(cell, arc, spice_template, global_settings, args)

    print("\n--- All Characterizations Complete ---")


if __name__ == "__main__":
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    main()
