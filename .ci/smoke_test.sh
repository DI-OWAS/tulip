#!/bin/bash
# Tulip smoke test for CI: characterize sg13g2_inv_1 at TT, 25 C, 1.2 V on a
# 2x2 slew/load grid and fail if any simulation failed.
#
# usage: .ci/smoke_test.sh [PDK_ROOT]
#   PDK_ROOT: IHP-Open-PDK checkout (needs ihp-sg13g2/libs.tech/ngspice/models)
#             default: $PDK_ROOT, else ../IHP-Open-PDK next to tulip
#             (chip-di-owas: vendor/IHP-Open-PDK)
# All compiled device models in .ci/osdi are loaded (psp103 and psp103_nqs:
# the PDK model files use both psp103va and pspnqs103va, r3_cmc: resistors).
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
pdk_root=$(readlink -f "${1:-${PDK_ROOT:-$here/../IHP-Open-PDK}}")
models=$pdk_root/ihp-sg13g2/libs.tech/ngspice/models
osdi=$here/.ci/osdi
cd "$here"

[ -f "$models/cornerMOSlv.lib" ] || { echo "no SG13G2 models in $models"; exit 1; }

# ngspice reads .spiceinit from the working directory
{
    echo "setcs sourcepath = ( \$sourcepath $models )"
    for f in "$osdi"/*.osdi; do echo "osdi '$f'"; done
} > .spiceinit
cfg=smoke_config.json
trap 'rm -f .spiceinit "$cfg"' EXIT

# inv_1 config with a 2x2 grid (the --slew/--load options do not shrink it)
python - config/configs/inv_1.json "$cfg" <<'PY'
import json
import sys

cfg = json.load(open(sys.argv[1]))
small = {'temp_values': [25], 'vdd_values': ['1.2'],
         'clock_slew_ns': [0.0186, 0.51636], 'data_slew_ns': [0.0186, 0.174],
         'output_loads': ['0.001', '0.039'], 'sequential_loads': ['0.05'],
         'num_processes': 4}


def shrink(node):
    if isinstance(node, dict):
        for key, val in node.items():
            if key in small:
                node[key] = small[key]
            else:
                shrink(val)
    elif isinstance(node, list):
        for val in node:
            shrink(val)


shrink(cfg)
json.dump(cfg, open(sys.argv[2], 'w'), indent=1)
PY

rm -rf failed_decks
log=smoke_test.log
PYTHONPATH=$here/src/py python src/py/tulip/scripts/run_characterization.py \
    --config "$cfg" 2>&1 | tee "$log"

if [ -d failed_decks ] || grep -q 'FATAL EXCEPTION\|Traceback\|not found' "$log"; then
    echo "SMOKE TEST FAILED"
    exit 1
fi
echo "SMOKE TEST PASSED"
