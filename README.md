# Tulip

Tulip is an open-source standard-cell characterizer for the IHP SG13G2 PDK.
It simulates extracted cell netlists with ngspice and writes NLDM timing data
to a Liberty (`.lib`) file.

It characterizes:

- Propagation delay and output transition
- Setup and hold (flip-flops and latches)
- Recovery and removal
- Minimum pulse width
- Input pin capacitance

## Requirements

- Python 3.8 or newer
- ngspice built as a shared library (`libngspice`), with OSDI support
- [IHP-Open-PDK](https://github.com/IHP-GmbH/IHP-Open-PDK) (`ihp-sg13g2`)
- Python packages: `numpy`, `PySpice`, `matplotlib`, `tqdm`


E.g., when using Python venv:

```sh
set -o errexit
python3 -m venv venv
source venv/bin/activate

pip install numpy PySpice matplotlib tqdm
```

## Setup

Run every command from the repository root. Tulip looks for
`template_pex.sp`, `config.json`, `pex/` and ngspice's `.spiceinit` in the
current directory.

1. Get the code:

   ```sh
   git clone https://github.com/electronicvisions/tulip.git
   cd tulip
   export PYTHONPATH=$PWD/src/py
   ```

2. Point ngspice at the PDK. Copy the `.spiceinit` shipped with the PDK
   (`ihp-sg13g2/libs.tech/ngspice/.spiceinit`) into the repository root, and
   set the environment variables it uses (`PDK_ROOT`, `PDK`). It adds the
   model directory to ngspice's search path and loads the PSP OSDI model.
   Without it, ngspice cannot find `cornerMOSlv.lib`.

3. Set `lef_file` in `config.json` to the LEF of the SG13G2 standard cells.
   Cell areas are read from it:

   ```json
   "lef_file": "<PDK_ROOT>/ihp-sg13g2/libs.ref/sg13g2_stdcell/lef/sg13g2_stdcell.lef"
   ```

## Usage

Characterization runs in two steps. First, `run_characterization.py`
simulates the cells and writes the results as JSON. Then `generate_lib.py`
assembles those results into a Liberty file.

### Characterize one cell

`config/configs/` holds one configuration file per cell:

```sh
python3 src/py/tulip/scripts/run_characterization.py \
    --config config/configs/inv_1.json
```

### Characterize the whole library

`config.json` in the repository root holds all 84 cells and is the default:

```sh
python3 src/py/tulip/scripts/run_characterization.py
```

### Generate the Liberty file

```sh
python3 src/py/tulip/scripts/generate_lib.py
```

This reads `config.json` and the results in `results/`, and writes
`<lib_name>.lib` to the repository root. Cells with no results are skipped
and a warning is printed.

### Run other corners

Temperature and supply voltage can be overridden from the command line.
Repeat an option to sweep several values:

```sh
python3 src/py/tulip/scripts/run_characterization.py \
    --config config/configs/dfrbpq_1.json --temp -40 --temp 125 --vdd 1.35
```

The process corner is set by `model_corner` in the configuration
(`mos_tt`, `mos_ss`, `mos_ff`, ...).

### Debug a single simulation point

Simulate one point of one arc, and save the SPICE decks:

```sh
python3 src/py/tulip/scripts/run_characterization.py \
    --config config/configs/dfrbpq_1.json \
    --cell sg13g2_dfrbpq_1 --arc hold_D_fall \
    --point 25 1.5 0.0186 0.0186 0.05 0.0 \
    --dump-decks decks/
```

`--point` takes `TEMP VDD CLOCK_SLEW DATA_SLEW LOAD DELTA`, in °C, V, ns, ns,
pF and ns.

### Options

| Option | Description |
|---|---|
| `--config FILE` | Configuration file (default: `config.json`) |
| `--temp T` | Override temperatures in °C (repeatable) |
| `--vdd V` | Override supply voltages in V (repeatable) |
| `--load C` | Override output loads in pF (repeatable) |
| `--cell`, `--arc`, `--point` | Simulate a single point (see above) |
| `--dump-decks DIR` | Write every generated SPICE deck to `DIR` |
| `--save-curves` | Save the timing curve of every corner as a plot |
| `--debug-plots` | Save one transient waveform per arc |

## Output

```
results/<cell>/<arc>_delay_table.json        propagation delay
results/<cell>/<arc>_transition_table.json   output transition
results/<cell>/<arc>_timing_table.json       setup, hold, recovery, removal
results/<cell>/<arc>_mpw_table.json          minimum pulse width
results/<cell>/<arc>_capacitance.json        input capacitance
curve_plots/, debug_plots/                   plots, if enabled
failed_decks/                                decks of simulations that failed
<lib_name>.lib                               Liberty file
```

Times are in ns and loads in pF.

## Configuration

A configuration file has two sections: `global_settings` and `cells`.

### Global settings

| Key | Description |
|---|---|
| `temp_values`, `vdd_values` | Corners to characterize (°C, V) |
| `data_slew_ns`, `clock_slew_ns` | Table index 1: input and clock transition (ns, 20–80 %) |
| `output_loads` | Table index 2 for delay arcs (pF) |
| `sequential_loads` | Output load for constraint arcs (pF) |
| `model_lib`, `model_corner` | ngspice model library and process corner |
| `lef_file` | LEF used for cell areas |
| `lib_name` | Liberty library name and output file name |
| `slew_lower_threshold_pct`, `slew_upper_threshold_pct` | Transition measurement thresholds (% of VDD) |
| `delay_threshold_pct` | Delay measurement threshold (% of VDD) |
| `slew_scale_factor` | Converts the 20–80 % slew to the full ramp time of the stimulus (1/0.6) |
| `delay_degradation_percent` | Clock-to-Q pushout that defines a setup/hold failure |
| `binary_search_iterations` | Bisection steps for constraint arcs |
| `num_processes` | Parallel ngspice processes |
| `sim_*` | ngspice transient and convergence options |

### Cells

Each cell entry names the extracted netlist and lists the arcs to
characterize:

```json
{
  "name": "sg13g2_inv_1_flat_pex",
  "spice_model": "sg13g2_inv_1_flat",
  "pin_mapping": "Y A VSS VDD",
  "has_qn_output": false,
  "arcs": [
    {
      "name": "cell_fall_A",
      "type": "propagation_delay",
      "data_signal": "A",
      "data_transition": "rise",
      "output_signal": "Y",
      "expected_output_transition": "fall",
      "timing_sense": "negative_unate"
    }
  ],
  "output_pin_attributes": { "Y": { "function": "!(A)" } }
}
```

`pin_mapping` must match the port order of the `.subckt` in the netlist. The
netlist is read from `pex/<spice_model without _flat>.pex.spice`. Use
`pex_dir` and `pex_file` to override this location. A table index set on an
arc (for example `data_slew_ns`) overrides the global value for that arc.

Arc types: `propagation_delay`, `setup`, `hold`, `setup_latch`, `hold_latch`,
`recovery`, `removal`, `mpw`, `input_capacitance`.


## Development and review status

This software was developed with the assistance of large language models. While
the tool is already functional, and characterization results have been
validated, it is still under active development and has not yet reached the
state intended for sustainable use and continued development.

The author has reviewed all AI-assisted components, though a group code review
process to reach sustainable software quality is still ongoing. Until this
process is complete, users should exercise caution when deploying and using
this tool.


## Acknowledgements

The software has been developed by Adarsh Vincent Parekkattil at Heidelberg
University as part of the research carried out by the Electronic Visions group.
This work has received funding from the DI-OWAS project.


## Licensing

`SPDX-License-Identifier: Apache-2.0`
