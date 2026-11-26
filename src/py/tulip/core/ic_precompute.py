"""
IC precompute: Liberate-style settle pass to find FF/latch internal-node
steady-state values, cached per (cell, logic-state).

Workflow:
    1. discover_internal_nodes(spice_include_files, top_subckt, port_names)
         -> list of internal node names referenced inside the cell subckt
    2. precompute_settle_state(cell_config, arc_config, ...)
         -> runs a long-settle ngspice sim with the static_pins driven to
            their "BEFORE" values, captures every internal node's voltage at
            settle_end, caches to JSON
    3. build_ic_block(cell_name, state_key)
         -> returns a string of .ic lines, ready to splice into the netlist
"""
import gc
import json
import os
import re
from pathlib import Path

import numpy as np
from PySpice.Spice.NgSpice.Shared import NgSpiceShared

# from .netlist import apply_robust_options


IC_CACHE_DIR = Path("ic_cache_v2")
_SUPPLY_NAMES = {"VDD", "VSS", "VNW", "VPW", "GND", "0", "VSUBS"}


# ---------------------------------------------------------------------------
# Node discovery
# ---------------------------------------------------------------------------

def discover_internal_nodes(spice_files, top_subckt, port_names):
    """
    Parse spice_include files, find all nodes referenced inside the
    top-level cell subckt, return those that aren't ports or supplies.

    Args:
        spice_files: list of paths to PEX/CDL .spice files
        top_subckt: top subckt name (e.g. 'sg13g2_dfrbpq_1')
        port_names: set of port names from pin_mapping

    Returns:
        sorted list of unique internal node names
    """
    if isinstance(spice_files, str):
        spice_files = [spice_files]

    body_lines = _extract_subckt_body(spice_files, top_subckt)
    if not body_lines:
        return []

    nodes = set()
    for line in body_lines:
        nodes.update(_extract_nodes_from_line(line))

    exclude = (set(port_names) | _SUPPLY_NAMES
               | {p.upper() for p in port_names})
    internal = sorted(n for n in nodes if n.upper() not in exclude
                      and n not in exclude
                      and n)
    return internal


def _extract_subckt_body(spice_files, target_subckt):
    """Return list of body lines for `.subckt <target_subckt>...`. """
    body = []
    in_target = False
    subckt_re = re.compile(r'^\s*\.subckt\s+(\S+)', re.IGNORECASE)
    ends_re = re.compile(r'^\s*\.ends\b', re.IGNORECASE)

    for path in spice_files:
        try:
            with open(path, encoding='utf-8', errors='ignore') as fobj:
                for raw in fobj:
                    line = raw.strip()
                    if not line or line.startswith('*'):
                        continue
                    match = subckt_re.match(line)
                    if match:
                        in_target = (match.group(1).lower()
                                     == target_subckt.lower())
                        continue
                    if ends_re.match(line):
                        in_target = False
                        continue
                    if in_target:
                        body.append(line)
        except FileNotFoundError:
            continue
    return body


def _extract_nodes_from_line(line):
    """
    Pull node names from a spice device line.

    Supported devices: M, X, R, C, L, V, I, D, Q. For each, the first N
    tokens after the device name are nodes (N depends on device).
    Anything containing '=' is a parameter, not a node.
    """
    tokens = line.split()
    if not tokens or tokens[0].startswith(('.', '*')):
        return []

    devtype = tokens[0][0].upper()
    # Node count per device type (typical primitives)
    counts = {'M': 4, 'Q': 3, 'D': 2, 'R': 2, 'C': 2, 'L': 2,
              'V': 2, 'I': 2, 'E': 4, 'F': 2, 'G': 4, 'H': 2,
              'J': 3, 'K': 0, 'S': 4, 'T': 4, 'U': 0, 'W': 3}

    # Subckt instance (X-element): consume all tokens until a '=' or the
    # last token, which is the subckt name. Treat everything before the
    # last non-param token as nodes.
    if devtype == 'X':
        nodes = []
        for tok in tokens[1:]:
            if '=' in tok:
                break
            nodes.append(tok)
        # Last node-like token is actually the subckt name; pop it.
        if nodes:
            nodes.pop()
        return nodes

    count = counts.get(devtype, 0)
    if count == 0:
        return []
    return tokens[1: 1 + count]


# ---------------------------------------------------------------------------
# State key
# ---------------------------------------------------------------------------

def state_key(arc_config, vdd_str):
    """
    Stable key for caching ICs. Encodes the BEFORE-state of all relevant
    static pins for this arc.

    For an internal_power arc, BEFORE state is what the cell sits in
    prior to the toggle on data_signal.
    """
    static = arc_config.get("static_pins", {}) or {}
    initialization = arc_config.get("initialization", "")
    data_signal = arc_config.get("data_signal", "")
    data_trans = arc_config.get("data_transition", "")
    # BEFORE value of data_signal is opposite of its transition
    if data_trans == "rise":
        data_before = "0"
    elif data_trans == "fall":
        data_before = vdd_str
    else:
        data_before = "?"

    parts = [f"{k}={v}" for k, v in sorted(static.items())]
    parts.append(f"{data_signal}={data_before}")
    if initialization:
        parts.append(f"init={initialization}")
    # the held Q level and the capture pin are part of the state
    parts.append(f"when={arc_config.get('when', '')}")
    parts.append(f"pcp={arc_config.get('preload_clock_pin', '')}")
    return ";".join(parts)


# ---------------------------------------------------------------------------
# Settle netlist construction
# ---------------------------------------------------------------------------

def _fill_config_placeholders(netlist, cell_config, arc_config):
    """Placeholders the template takes from the config (model library and
    corner, cell netlist, clock times) - same rules as simulation.build."""
    sim_cfg = arc_config.get("_sim_cfg", {})
    model = cell_config["spice_model"]
    pex_file = cell_config.get(
        "pex_file",
        (model[:-5] if model.endswith("_flat") else model) + ".pex.spice")
    pex_dir = cell_config.get("pex_dir", sim_cfg.get("pex_dir", "pex"))
    return (netlist
            .replace("%%MODEL_LIB%%", cell_config.get(
                "model_lib", sim_cfg.get("model_lib", "cornerMOSlv.lib")))
            .replace("%%MODEL_CORNER%%", cell_config.get(
                "model_corner", sim_cfg.get("model_corner", "mos_tt")))
            .replace("%%PEX_INCLUDES%%", f".include '{pex_dir}/{pex_file}'")
            .replace("%%PRELOAD_CLK_TIME%%",
                     f"{sim_cfg.get('preload_clk_time_ns', 5.0)}e-9")
            .replace("%%MEASURE_CLK_TIME%%",
                     f"{sim_cfg.get('measure_clk_time_ns', 15.0)}e-9"))


def _build_settle_netlist(spice_template, cell_config, arc_config,
                          temp_str, vdd_str, internal_nodes,
                          settle_ns=500.0):
    """
    Build a long-settle netlist where:
      - all static pins driven to BEFORE state
      - data_signal driven to its BEFORE value
      - clock held at the right "rest" value for initialization
      - .meas tran ic_NODE FIND v(xdut.NODE) AT=settle_ns-0.1ns
    Returns: netlist string, settle_end_s
    """
    # Pull internal-node values via .meas, prefixed with xdut.
    settle_end_ns = settle_ns
    measure_at_ns = settle_ns - 0.1
    measure_at_s = measure_at_ns * 1e-9

    # Build static-pin V sources for all BEFORE-state pins
    static = arc_config.get("static_pins", {}) or {}
    data_signal = arc_config.get("data_signal", "")
    data_trans = arc_config.get("data_transition", "")
    init = arc_config.get("initialization", "")
    func_map = arc_config.get("function_mapping", {}) or {}

    # Resolve "VDD"/"0" string values to numeric
    def _level_of(token):
        if token in ("VDD", "vdd"):
            return vdd_str
        try:
            float(token)
            return token
        except (ValueError, TypeError):
            return "0"

    choreo = _sequenced_settle_sources(arc_config, vdd_str)
    if choreo is not None:
        sources, settle_end_ns = choreo
        return _finish_settle_netlist(
            spice_template, cell_config, arc_config, temp_str, vdd_str,
            sources, settle_end_ns)

    sources = []
    handled = set()
    for pin, val in static.items():
        sources.append(f"V_{pin} {pin} VSS DC {_level_of(val)}")
        handled.add(pin)

    # Drive data_signal to BEFORE value
    if data_signal and data_signal not in handled:
        if data_trans == "rise":
            sources.append(f"V_{data_signal} {data_signal} VSS DC 0")
        elif data_trans == "fall":
            sources.append(f"V_{data_signal} {data_signal} VSS DC {vdd_str}")
        handled.add(data_signal)

    # Pin async resets/sets to inactive level if not already handled
    if init == "reset":
        rst_pin = func_map.get("reset_b")
        if rst_pin and rst_pin not in handled:
            sources.append(f"V_{rst_pin} {rst_pin} VSS DC 0")  # asserted
            handled.add(rst_pin)
    elif init == "set":
        set_pin = func_map.get("set_b")
        if set_pin and set_pin not in handled:
            sources.append(f"V_{set_pin} {set_pin} VSS DC 0")
            handled.add(set_pin)

    # Any leftover scan pins
    scan_pins = arc_config.get("_scan_pins", ["SCE", "SCD"])
    for scan_pin in scan_pins:
        if scan_pin not in handled:
            sources.append(f"V_{scan_pin} {scan_pin} VSS DC 0")

    init_block = "\n".join(sources)

    # Use the template's replace points. No %%STIMULI_BLOCK%% — the settle
    # holds all pins static at their BEFORE values and lets the circuit reach
    # steady state. We read ALL node voltages from the plot afterward, so we
    # do not emit any .meas lines (Liberate-style: dump every node, freeze).
    netlist = (spice_template
               .replace("%%TEMP_VAL%%", temp_str)
               .replace("%%VDD_VAL%%", vdd_str)
               .replace("%%C_LOAD%%", "0.001p")
               .replace("%%CLOCK_SLEW%%", "0.01n")
               .replace("%%DATA_SLEW%%", "0.01n")
               .replace("%%PIN_MAPPING%%", cell_config["pin_mapping"])
               .replace("%%SPICE_MODEL%%", cell_config["spice_model"])
               .replace("%%OUTPUT_NODE%%", arc_config["output_signal"])
               .replace("%%INIT_BLOCK%%", init_block)
               .replace("%%STIMULI_BLOCK%%", "* settle pass: no stimulus")
               .replace("%%QN_LOAD_BLOCK%%", "* no QN load for settle"))

    netlist = _fill_config_placeholders(netlist, cell_config, arc_config)
    # lazy import: tulip.core.netlist imports this module
    from .netlist.common import apply_robust_options
    netlist = apply_robust_options(netlist, temp_str,
                                   arc_config.get("_sim_cfg", {}))

    # Replace the .tran line with a long settle, no uic. Coarse step is fine;
    # we only care about the final steady-state value of each node.
    netlist = re.sub(
        r"^\.tran\s+.*$",
        f".tran 50p {settle_ns}n",
        netlist,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    return netlist, measure_at_s


# Sequenced settle timing for a rising-edge flop: async pin released at
# 5us, CLK rises at 10us (clocks in the Q target), CLK falls at 15us if its
# BEFORE level is 0, D moves to its BEFORE level at 20us, capture at 25us.
_T_RST_NS, _T_CLK_NS, _T_CLKF_NS, _T_D_NS, _T_END_NS = (
    5000.0, 10000.0, 15000.0, 20000.0, 25000.0)
_T_EDGE_NS = 0.2


def _pwl(points, vdd_str):
    """points: list of (t_ns, level 0/1) -> ngspice PWL with 0.2ns edges."""
    out = []
    prev = None
    for t_ns, lvl in points:
        level = vdd_str if lvl else "0"
        if prev is not None and lvl != prev:
            out.append(f"{t_ns}n {vdd_str if prev else '0'}")
            out.append(f"{t_ns + _T_EDGE_NS}n {level}")
        elif prev is None:
            out.append(f"0 {level}")
        prev = lvl
    return "PWL(" + " ".join(out) + ")"


def _sequenced_settle_sources(arc_config, vdd_str):
    """Settle sources that drive a rising-edge flop with active-low async
    pins into its BEFORE state (reset, clock in Q target, then set D/CLK).
    Returns (source_lines, end_ns) or None when the cell does not fit (no
    CLK/D pin or unknown Q target); the caller then uses the DC settle."""
    # lazy import: tulip.core.netlist imports this module
    from .netlist.power import _power_before_state
    levels, q_target = _power_before_state(arc_config)
    clk = arc_config.get("preload_clock_pin", "CLK")
    d_pin = arc_config.get("preload_data_pin", "D")
    if clk not in levels or d_pin not in levels or q_target is None:
        return None
    fmap = arc_config.get("function_mapping") or {}
    rst_pin, set_pin = fmap.get("reset_b"), fmap.get("set_b")
    lines = []
    for pin, bit in levels.items():
        if pin == rst_pin:
            pts = [(0, 0), (_T_RST_NS, 1), (_T_D_NS, bit)]
        elif pin == set_pin:
            pts = [(0, 1), (_T_D_NS, bit)]
        elif pin == clk:
            pts = [(0, 0), (_T_CLK_NS, 1), (_T_CLKF_NS, bit)]
        elif pin == d_pin:
            pts = [(0, q_target), (_T_D_NS, bit)]
        else:
            pts = [(0, bit)]
        lines.append(f"V_{pin} {pin} VSS {_pwl(pts, vdd_str)}")
    return lines, _T_END_NS


def _finish_settle_netlist(spice_template, cell_config, arc_config,
                           temp_str, vdd_str, sources, settle_end_ns):
    """Same template substitution as the DC settle, with a 25us .tran."""
    scan_pins = arc_config.get("_scan_pins", ["SCE", "SCD"])
    have = {ln.split()[1] for ln in sources}
    for scan_pin in scan_pins:
        if scan_pin not in have:
            sources.append(f"V_{scan_pin} {scan_pin} VSS DC 0")
    netlist = (spice_template
               .replace("%%TEMP_VAL%%", temp_str)
               .replace("%%VDD_VAL%%", vdd_str)
               .replace("%%C_LOAD%%", "0.001p")
               .replace("%%CLOCK_SLEW%%", "0.01n")
               .replace("%%DATA_SLEW%%", "0.01n")
               .replace("%%PIN_MAPPING%%", cell_config["pin_mapping"])
               .replace("%%SPICE_MODEL%%", cell_config["spice_model"])
               .replace("%%OUTPUT_NODE%%", arc_config["output_signal"])
               .replace("%%INIT_BLOCK%%", "\n".join(sources))
               .replace("%%STIMULI_BLOCK%%", "* sequenced settle")
               .replace("%%QN_LOAD_BLOCK%%", "* no QN load for settle"))
    netlist = _fill_config_placeholders(netlist, cell_config, arc_config)
    # lazy import: tulip.core.netlist imports this module
    from .netlist.common import apply_robust_options
    netlist = apply_robust_options(netlist, temp_str,
                                   arc_config.get("_sim_cfg", {}))
    # tstep 1ns, tmax 5ns; PWL corners are breakpoints so the 0.2ns edges
    # are still resolved.
    netlist = re.sub(r"^\.tran\s+.*$",
                     f".tran 1n {settle_end_ns}n 0 5n",
                     netlist, flags=re.MULTILINE | re.IGNORECASE)
    return netlist, (settle_end_ns - 0.001) * 1e-9


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def _cache_path(cell_name, key):
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', key)
    return IC_CACHE_DIR / cell_name / f"{safe}.json"


def load_ic_cache(cell_name, key):
    path = _cache_path(cell_name, key)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fobj:
            return json.load(fobj)
    except (json.JSONDecodeError, OSError):
        return None


def save_ic_cache(cell_name, key, ic_dict):
    path = _cache_path(cell_name, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fobj:
        json.dump(ic_dict, fobj, indent=2)


# ---------------------------------------------------------------------------
# Driver: run settle, return IC dict
# ---------------------------------------------------------------------------

def precompute_settle_state(spice_template, cell_config, arc_config,
                            temp_str, vdd_str, settle_ns=500.0,
                            force_recompute=False):
    """
    Get IC values for cell's current BEFORE-state. Uses cache if available.

    Returns:
        dict {node_name: voltage_float} OR None if unable to compute
    """
    cell_name = cell_config["name"]
    key = state_key(arc_config, vdd_str)
    print(
        f"  [IC] ENTRY: cell={cell_name} arc={arc_config.get('name')} "
        f"key={key}", flush=True)

    if not force_recompute:
        cached = load_ic_cache(cell_name, key)
        if cached is not None:
            print(f"  [IC] cache HIT len={len(cached)}", flush=True)
            return cached
        print("  [IC] cache MISS", flush=True)

    # Build the settle netlist (all pins static at BEFORE values, long .tran,
    # no stimulus). We do NOT parse the netlist for node names — instead we
    # run the settle, then read EVERY node voltage the simulator reports and
    # freeze each one. This matches Liberate's "dump all, .ic all" approach
    # and avoids any PEX-naming assumptions.
    print(f"  [IC] running settle ({settle_ns}ns) ...", flush=True)
    netlist, _ = _build_settle_netlist(
        spice_template, cell_config, arc_config,
        temp_str, vdd_str, internal_nodes=[], settle_ns=settle_ns,
    )

    ngspice = None
    try:
        ngspice = NgSpiceShared.new_instance()
        try:  # ngspice's OpenMP team ignores OMP_NUM_THREADS; one thread per
            # pool worker, else N workers x N threads spin on N cores.
            ngspice.exec_command(
                "set num_threads="
                + os.environ.get("TULIP_NGSPICE_THREADS", "1"))
        except Exception:  # pylint: disable=broad-except
            pass
        ngspice.load_circuit(netlist)
        try:
            ngspice.run()
        except Exception:  # pylint: disable=broad-except
            # PySpice flags any stderr line not starting with "Warning:"
            # (e.g. ngspice's "Note: Starting dynamic gmin stepping") as a
            # failed command even when the transient completed. Report only
            # stderr lines that are not ngspice Notes; the tran-plot check
            # below decides whether the run actually succeeded.
            _err = [ln for ln in getattr(ngspice, "_stderr", [])
                    if not str(ln).lstrip().startswith("Note:")]
            if _err:
                print("  [IC] ngspice stderr: " + " | ".join(map(str, _err)),
                      flush=True)
        plot = ngspice.plot(simulation=None, plot_name='tran1')
        _tk = [k for k in plot.keys() if str(k).lower() == "time"]
        if _tk:
            _tend = float(np.asarray(plot[_tk[0]].to_waveform())[-1])
            print(f"  [IC] settle reached t={_tend * 1e9:.1f} ns", flush=True)

        ic_dict = {}
        for key_obj in plot.keys():
            name = str(key_obj)
            low = name.lower()
            if low in ("time",) or low.startswith("i("):
                continue  # skip time axis and branch currents

            # Normalize to a bare node path. ngspice reports node voltages as
            # either "v(xdut.net9)" or just "xdut.net9" depending on version.
            node = name
            if low.startswith("v(") and name.endswith(")"):
                node = name[2:-1]

            # Only freeze nodes inside the DUT hierarchy (xdut.*). Top-level
            # supply/stimulus nodes are driven and don't need ICs.
            _out = str(arc_config.get("output_signal", "Q")).lower()
            if not (node.lower().startswith("xdut.")
                    or node.lower() == _out):
                continue

            try:
                arr = np.asarray(plot[key_obj].to_waveform())
            except Exception:
                continue
            if arr.size == 0:
                continue
            ic_dict[node] = float(arr[-1])

        print(f"  [IC] froze {len(ic_dict)} DUT nodes from settle", flush=True)
        save_ic_cache(cell_name, key, ic_dict)
        return ic_dict
    except Exception as exc:
        print(f"  [IC PRECOMPUTE] settle failed for "
              f"{cell_name}/{key}: {exc}", flush=True)
        return None
    finally:
        if ngspice:
            try:
                ngspice.destroy()
            except Exception:
                pass
        gc.collect()


# ---------------------------------------------------------------------------
# Inject IC block into a netlist
# ---------------------------------------------------------------------------

def build_ic_block(ic_dict):
    """
    Turn {node: voltage} into a SPICE .ic block.
    """
    if not ic_dict:
        return ""
    lines = ["* IC block from precomputed settle pass"]
    for node, val in ic_dict.items():
        lines.append(f".ic v({node}) = {val:.6g}")
    return "\n".join(lines)
