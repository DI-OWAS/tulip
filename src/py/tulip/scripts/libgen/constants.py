"""Shared constants and the config read at import time."""

import json
import pathlib

RESULTS_DIR = pathlib.Path('results')
CONFIG_FILE = 'config.json'
LIB_NAME = 'sg13g2_custom'

with open(CONFIG_FILE, encoding='utf-8') as f:
    _config = json.load(f)
TARGET_TEMP = str(_config['global_settings']['temp_values'][0])
TARGET_VDD = str(_config['global_settings']['vdd_values'][0])

LEF_AREAS = {}
