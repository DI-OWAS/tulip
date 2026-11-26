"""Leakage and internal-energy measurement.

Optional: timing-only builds may omit this module; the router in
``simulation`` degrades gracefully when it is absent."""

import os
import numpy as np
from .vectors import (
    branch_current,
    failed_result,
    plot_lookup,
    vec_lower,
)


def measure_leakage(plot, arc_config, vdd_numeric, delta_ns):
    """
    Liberate-aligned leakage measurement.

    Average i(VVDD) over the tail of the settling run, take absolute
    value. Default tail = last 5% of the window, ending at 99.99%.
    """
    sim_cfg = arc_config.get("_sim_cfg", {})
    settle_ns = sim_cfg.get("power_settle_window_ns", 20000)
    tail_start_pct = sim_cfg.get("power_leakage_tail_start_pct", 95.0)
    tail_end_pct = sim_cfg.get("power_leakage_tail_end_pct", 99.99)

    lookup = plot_lookup(plot)
    time_vec = vec_lower(plot, "time", lookup)
    if time_vec is None:
        return failed_result(delta_ns)

    i_vdd = branch_current(plot, "VVDD", lookup)
    if i_vdd is None:
        i_vdd = branch_current(plot, "VDD", lookup)
    if i_vdd is None:
        print("  [LEAKAGE] No VVDD branch current found in plot")
        return failed_result(delta_ns)

    i_vss = branch_current(plot, "VVSS", lookup)
    if i_vss is None:
        i_vss = branch_current(plot, "VSS", lookup)

    start_s = settle_ns * 1e-9 * tail_start_pct / 100.0
    end_s = settle_ns * 1e-9 * tail_end_pct / 100.0
    mask = (time_vec >= start_s) & (time_vec <= end_s)
    if not np.any(mask):
        print(f"  [LEAKAGE] Empty tail window [{start_s:.3e},{end_s:.3e}]")
        return failed_result(delta_ns)

    # Leakage = max(|i(VVDD)|, |i(VVSS)|) * VDD in ngspice's branch-current
    # sign convention. With inputs referenced to node 0, the input source
    # current inflates one rail and deflates the other; the rail carrying
    # the true device leakage is the LARGER magnitude here (opposite sign
    # convention to Liberate's Spectre deck, where it was the smaller).
    # Verified against the 1.5V reference lib: max() matches, min() does not.
    i_avg_vdd = float(np.mean(np.abs(i_vdd[mask])))
    i_avg_vss = (float(np.mean(np.abs(i_vss[mask])))
                 if i_vss is not None else float("nan"))
    # Reference definition: P = V * (|I_VDD| + currents of inputs held high)
    state = arc_config.get("_state") or {}
    out_sig = arc_config.get("output_signal", "Q")
    i_hi = 0.0
    for pin, val in state.items():
        if pin == out_sig or val != 1:
            continue
        i_pin = branch_current(plot, pin, lookup)
        if i_pin is not None:
            i_hi += float(np.mean(np.abs(i_pin[mask])))
    i_avg = i_avg_vdd + i_hi
    p_leak_w = i_avg * vdd_numeric

    output_signal = arc_config.get("output_signal", "Q")
    v_q_end = None
    v_q = vec_lower(plot, output_signal, lookup)
    if v_q is not None:
        v_q_end = float(v_q[-1])

    state_name = arc_config.get('_state_name', '?')
    _iv = (i_avg_vss if i_vss is not None else float('nan'))
    if v_q_end is not None:
        print(
            f"      [LEAKAGE] state={state_name}: "
            f"i_vdd={i_avg_vdd * 1e9:.3f}nA i_vss={_iv * 1e9:.3f}nA "
            f"i_hi={i_hi * 1e9:.3f}nA i_min={i_avg * 1e9:.3f}nA, "
            f"p_leak={p_leak_w * 1e6:.4f}uW, v(Q)={v_q_end:.3f}V"
        )
    else:
        print(
            f"      [LEAKAGE] state={state_name}: "
            f"i_vdd={i_avg_vdd * 1e9:.3f}nA i_vss={_iv * 1e9:.3f}nA "
            f"i_min={i_avg * 1e9:.3f}nA, p_leak={p_leak_w * 1e6:.4f}uW"
        )

    return {
        "delta_ns": delta_ns,
        "t_prop": np.nan,
        "t_tran": np.nan,
        "leakage_power": p_leak_w,
        "dynamic_energy": np.nan,
        "_v_q_settled": v_q_end,
    }


def measure_internal_energy(plot, arc_config, vdd_numeric, delta_ns,
                            output_toggles=True):
    """
    Liberate-aligned internal_power measurement.

    From the deck:
        AltosPower000    = INTEG i(VVDD)        from 0 to power_tend
        AltosPinPower000 = INTEG i(V_D)         from 0 to power_tend
        AltosPinPower001 = INTEG i(V_RESET_B)   from 0 to power_tend

    Liberty internal_power energy:
        E_int = (|Q_VDD| - Q_pin_charges - Q_leak*power_tend - Q_load) * VDD

    Two integration regimes:
      - Q toggles (output_toggles=True): short transient, window
        [power_toggle_start_ns, +power_toggle_window]
      - Q held    (output_toggles=False): long settling sim, window
        [power_settle_offset_ns, power_hidden_window_ns]
    """
    sim_cfg = arc_config.get("_sim_cfg", {})

    if output_toggles:
        # Q toggles within a short transient (sim_window_ns ~30ns).
        # Window starts a few ns before the trigger edge.
        # Window END is ADAPTIVE: extends to the LAST 50% crossing of v(Q)
        # plus a small pad, per (slew, load) point — mirrors Liberate's
        # AltosLeakCross power_tend convention.
        toggle_factor = sim_cfg.get("power_toggle_window_factor", 3.0)
        longest_delay_ns = arc_config.get("longest_expected_delay_ns", 5.0)

        # Direction-aware integration START.
        _arc_name_lower = arc_config.get("name", "").lower()
        _is_rising = "_rise" in _arc_name_lower

        if _is_rising:
            integ_start_ns = sim_cfg.get(
                "power_toggle_start_ns_rise",
                sim_cfg.get("power_toggle_start_ns", 4.0),
            )
        else:
            integ_start_ns = sim_cfg.get(
                "power_toggle_start_ns_fall",
                sim_cfg.get("power_toggle_start_ns", 4.0),
            )

        # Fixed-window fallback if v(Q) lookup fails or no crossing found
        fallback_window_ns = max(toggle_factor * longest_delay_ns, 5.0)
        integ_window_ns = fallback_window_ns

        # Fixed edge-anchored window (power_fixed_edge_window=true): integrate
        # power_window_ns from the start of the trigger ramp and skip the
        # adaptive output-crossing search below.
        _fixed_edge_win = sim_cfg.get("power_fixed_edge_window", False)
        if _fixed_edge_win:
            # Fixed per-arc window, same length for every slew/load point.
            integ_window_ns = arc_config.get(
                "power_window_ns", sim_cfg.get("power_window_ns", 8.2))
            if arc_config.get("_ic_dict"):
                # .ic stimulus: the trigger ramp starts at t=0
                integ_start_ns = 0.0
        else:
            # Adaptive END: find last v(Q) crossing of 50% VDD threshold + pad
            _lookup_pre = plot_lookup(plot)
            _time_pre = vec_lower(plot, "time", _lookup_pre)
            _vq_pre = vec_lower(
                plot, arc_config.get("output_signal", "Q"), _lookup_pre
            )
            if (_time_pre is not None
                    and _vq_pre is not None
                    and len(_vq_pre) > 1):
                v_thresh = 0.5 * vdd_numeric
                _sign_pre = np.sign(_vq_pre - v_thresh)
                _crossings = np.where(np.abs(np.diff(_sign_pre)) == 2)[0]
                if len(_crossings):
                    t_last_cross_ns = float(_time_pre[_crossings[-1]]) * 1e9
                    pad_ns = sim_cfg.get("power_toggle_pad_ns", 0.5)
                    adaptive_window_ns = (
                        (t_last_cross_ns + pad_ns) - integ_start_ns
                    )
                    # Use adaptive window, but never shorter than 1 ns and
                    # cap at a fixed absolute max past the crossing (default
                    # 30 ns) to catch the latch settling tail without reaching
                    # into the leakage-only region.
                    _max_after_cross_ns = sim_cfg.get(
                        "power_max_window_ns", 30.0
                    )
                    if adaptive_window_ns >= 1.0:
                        integ_window_ns = min(
                            adaptive_window_ns,
                            _max_after_cross_ns,
                        )
    else:
        # Hidden arc: long settling sim, integrate from settle_offset
        # through the hidden window end.
        integ_start_ns = sim_cfg.get("power_settle_offset_ns", 100)
        hidden_window_ns = sim_cfg.get("power_hidden_window_ns", 10000)
        integ_window_ns = hidden_window_ns - integ_start_ns

    integ_start_s = integ_start_ns * 1e-9
    integ_end_s = (integ_start_ns + integ_window_ns) * 1e-9

    lookup = plot_lookup(plot)
    time_vec = vec_lower(plot, "time", lookup)
    if time_vec is None:
        return failed_result(delta_ns)

    mask = (time_vec >= integ_start_s) & (time_vec <= integ_end_s)
    if not np.any(mask):
        print(
            f"  [INTERNAL] Empty integration window "
            f"[{integ_start_s:.3e},{integ_end_s:.3e}]"
        )
        return failed_result(delta_ns)

    # ---- Q_VDD (averaged with Q_VSS, Liberate convention) ----
    # Liberate computes E = (|∫i(VVDD)| + |∫i(VVSS)|) / 2 × VDD to handle
    # non-conservation of charge between source and sink branches during
    # transient (numerical KCL error at capacitor discontinuities).
    i_vdd = branch_current(plot, "VVDD", lookup)
    if i_vdd is None:
        i_vdd = branch_current(plot, "VDD", lookup)
    if i_vdd is None:
        print("  [INTERNAL] No VVDD branch current")
        return failed_result(delta_ns)
    q_vdd_raw = float(np.trapz(i_vdd[mask], time_vec[mask]))

    i_vss = branch_current(plot, "VVSS", lookup)
    if i_vss is None:
        i_vss = branch_current(plot, "VSS", lookup)

    if i_vss is not None:
        q_vss_raw = float(np.trapz(i_vss[mask], time_vec[mask]))
        # Signed raw integrals + pin charges, to verify KCL asymmetry.
        _q_clk = _q_d = _q_rb = 0.0
        _ic = branch_current(plot, "CLK", lookup)
        if _ic is not None:
            _q_clk = float(np.trapz(_ic[mask], time_vec[mask]))
        _id = branch_current(plot, "D", lookup)
        if _id is not None:
            _q_d = float(np.trapz(_id[mask], time_vec[mask]))
        _irb = branch_current(plot, "RESET_B", lookup)
        if _irb is not None:
            _q_rb = float(np.trapz(_irb[mask], time_vec[mask]))
        print(f"      [QBRANCH] q_vdd_signed={q_vdd_raw * 1e15:+.3f}fC "
              f"q_vss_signed={q_vss_raw * 1e15:+.3f}fC "
              f"q_clk={_q_clk * 1e15:+.3f} q_d={_q_d * 1e15:+.3f} "
              f"q_rb={_q_rb * 1e15:+.3f}fC", flush=True)
        # Signed: VDD delivers negative branch charge, VSS positive.
        q_vdd = (-q_vdd_raw + q_vss_raw) / 2.0
    else:
        # Fallback: VSS not in plot. Single-sided approximation.
        q_vdd = abs(q_vdd_raw)
    # ---- Optional waveform dump for simulator overlay plots ----
    # Enable with TULIP_DUMP_WAVES=1. Writes one CSV per (arc, slew,
    # load) point with the full (unmasked) traces used by this
    # measurement, for overlay against Spectre/Liberate deck reruns.
    if os.environ.get("TULIP_DUMP_WAVES"):
        _dump_dir = "wave_dumps"
        os.makedirs(_dump_dir, exist_ok=True)
        _v_clk = vec_lower(plot, "clk", lookup)
        _out_sig = arc_config.get("output_signal", "Q")
        _v_out = vec_lower(plot, _out_sig, lookup)
        _zero = np.zeros_like(time_vec)
        _cols = [
            time_vec,
            i_vdd if i_vdd is not None else _zero,
            i_vss if i_vss is not None else _zero,
            _v_clk if _v_clk is not None else _zero,
            _v_out if _v_out is not None else _zero,
        ]
        _fname = (
            f"{arc_config.get('name', 'arc')}"
            "_slew" + str(arc_config.get('data_slew_str')
                          or arc_config.get('clock_slew_str', 'NA'))
            + f"_load{arc_config.get('_load_str', 'NA')}.csv"
        )
        _hdr = (
            f"win_ns=[{integ_start_ns:.3f},"
            f"{integ_start_ns + integ_window_ns:.3f}]\n"
            f"time,i_vvdd,i_vvss,v_clk,v_{_out_sig.lower()}"
        )
        np.savetxt(
            os.path.join(_dump_dir, _fname),
            np.column_stack(_cols),
            delimiter=",",
            comments="# ",
            header=_hdr,
        )
        print(f"      [WAVEDUMP] {_dump_dir}/{_fname}", flush=True)
    # ---- Q_pin_charges ----
    # Liberate subtracts pin charge ONLY for STATIC (non-switching) input
    # pins. Verified in Liberate's hidden_*_RESET_B power deck: it emits
    # AltosPinPower only for i(VCLK)/i(VD), never for the switching pin.
    # The switching pin's drive current IS the stimulus, not a parasitic
    # load. Subtracting it (e.g. RESET_B, q~7fC) pulls E_int ~13% low.
    q_pin_total = 0.0
    pin_breakdown = {}
    pins_to_subtract = _collect_input_pins(arc_config)

    # The arc's switching pin (data_signal) must NOT be subtracted.
    _switch_pin = (arc_config.get("data_signal") or "").strip()
    for pin in pins_to_subtract:
        if _switch_pin and pin.strip().lower() == _switch_pin.lower():
            # switching pin: excluded (Liberate convention)
            pin_breakdown[pin] = 0.0
            continue
        i_pin = branch_current(plot, f"_{pin}", lookup)
        if i_pin is None:
            i_pin = branch_current(plot, pin, lookup)
        if i_pin is None:
            continue
        q_s = float(np.trapz(i_pin[mask], time_vec[mask]))
        v_pin = vec_lower(plot, pin, lookup)
        _high = (v_pin is not None
                 and float(v_pin[mask][0]) > 0.5 * vdd_numeric)
        # Static pin held high acts like VDD (+0.5*V*(-Q) added to energy),
        # held low acts like VSS (+0.5*V*(+Q)). q_internal subtracts
        # q_pin_used, so store the negated half-charge here.
        q_pin_total += (0.5 * q_s) if _high else (-0.5 * q_s)
        pin_breakdown[pin] = q_s

    # ---- Q_leak * window ----
    sub_leak = sim_cfg.get("power_internal_subtract_leakage", True)
    q_leak = 0.0
    leak_a = arc_config.get("_leak_current_a")
    if sub_leak and leak_a is not None and not np.isnan(leak_a):
        q_leak = abs(leak_a) * (integ_end_s - integ_start_s)

    # ---- Q_load ----
    sub_load = sim_cfg.get("power_internal_subtract_load", True)
    q_load = 0.0
    if sub_load and output_toggles:
        output_signal = arc_config.get("output_signal", "Q")
        v_q = vec_lower(plot, output_signal, lookup)
        load_str = arc_config.get("_load_str", "0.001")
        try:
            load_pf = float(load_str)
        except (ValueError, TypeError):
            load_pf = 0.001
        # DEBUG: pinpoint why Q_load is always 0
        _vq_keys = [k for k in plot.keys()
                    if str(k).lower() in ("q", "v(q)")]
        _vq_len = len(v_q) if v_q is not None else "NA"
        _mask_count = int(np.sum(mask))
        print(
            f"      [QLOAD_DBG] sub_load={sub_load} "
            f"output_toggles={output_toggles} "
            f"v_q_is_none={v_q is None} load_str={load_str!r} "
            f"load_pf={load_pf} v_q_len={_vq_len} mask_count={_mask_count} "
            f"v_q_keys_in_plot={_vq_keys}"
        )
        if v_q is not None:
            v_q_start = float(v_q[mask][0]) if len(v_q[mask]) else 0.0
            v_q_end = float(v_q[mask][-1]) if len(v_q[mask]) else 0.0
            dv_q = v_q_end - v_q_start
            print(
                f"      [QLOAD_DBG] v_q_start={v_q_start:.4f}V "
                f"v_q_end={v_q_end:.4f}V dv={dv_q:.4f}V"
            )
            # Only subtract Q_load on a RISING transition: cap charges
            # FROM VDD, charge appears in i(VVDD). On falling, the cap
            # discharges to VSS — i(VVDD) doesn't see this charge.
            if dv_q > 0.5 * vdd_numeric:
                # CLoad is tied to VSS, so on a rise the load charge C*dv
                # flows through BOTH rails and the rail average carries all
                # of it: subtract C*dv. On a fall it circulates inside the
                # VSS net and never reaches a rail: subtract nothing.
                q_load = load_pf * 1e-12 * dv_q
        # The complementary output (Q_N for a Q arc, Q for a Q_N arc) carries
        # the same CLoad; when it rises its charge also comes from VDD and
        # is load energy, not internal energy.
        qn_node = arc_config.get("_qn_load_node")
        if qn_node:
            other = (qn_node if output_signal.upper() != qn_node.upper()
                     else "Q")
            v_o = vec_lower(plot, other, lookup)
            if v_o is not None and len(v_o[mask]):
                dv_o = float(v_o[mask][-1]) - float(v_o[mask][0])
                if dv_o > 0.5 * vdd_numeric:
                    q_load += load_pf * 1e-12 * dv_o
                    print(f"      [QLOAD_DBG] {other} rises dv={dv_o:.4f}V "
                          f"-> +{load_pf * dv_o * 1e3:.3f}fC load")

    # ---- Final ----
    sub_pin = sim_cfg.get("power_internal_subtract_pin_charges", True)

    # Per-arc force-on for RESET_B/SET_B-driven Q arcs. These arcs
    # capture full pin charge from V_RESET_B/V_SET_B during the integration
    # window (the async pin transitions but does not toggle back), so
    # leaving pin-charge subtraction off inflates E_int by ~Q_pin*VDD.
    # CLK-driven arcs are unaffected (CLK toggles up and down within window).
    _arc_name_lower = arc_config.get("name", "").lower()
    # if "reset_b" in _arc_name_lower or "set_b" in _arc_name_lower:
    #    sub_pin = True

    # E = 0.5*V*(-Q_VDD + Q_VSS) + sum over static pins of the signed
    # 0.5*V*Q_pin term - load - leakage. q_pin_total already carries the
    # signed 0.5 weight per pin.
    q_pin_used = q_pin_total if sub_pin else 0.0

    q_internal = q_vdd - q_pin_used - q_leak - q_load
    e_internal = q_internal * vdd_numeric

    label = "TOGGLE" if output_toggles else "HIDDEN"
    print(
        f"      [{label}] win=[{integ_start_ns:.0f},"
        f"{integ_start_ns + integ_window_ns:.0f}]ns  "
        f"|Q_VDD|={abs(q_vdd) * 1e15:.3f}fC  "
        f"Q_pin={q_pin_used * 1e15:.3f}fC  "
        f"Q_leak={q_leak * 1e15:.3f}fC  "
        f"Q_load={q_load * 1e15:.3f}fC  "
        f"-> E_int={e_internal * 1e15:.3f}fJ"
    )

    return {
        "delta_ns": delta_ns,
        "t_prop": np.nan,
        "t_tran": np.nan,
        "leakage_power": np.nan,
        "dynamic_energy": e_internal,
        "_q_vdd": q_vdd,
        "_q_pin_total": q_pin_total,
        "_q_pin_breakdown": pin_breakdown,
        "_q_leak": q_leak,
        "_q_load": q_load,
    }


def _collect_input_pins(arc_config):
    """
    Collect names of input pins whose voltage-source charge must be
    subtracted from Q_VDD.
    """
    pins = set()

    trig = arc_config.get("data_signal")
    if trig:
        pins.add(trig)

    static = arc_config.get("static_pins") or {}
    for static_pin in static.keys():
        pins.add(static_pin)

    fmap = arc_config.get("function_mapping") or {}
    for static_pin in (fmap.get("reset_b"), fmap.get("set_b")):
        if static_pin:
            pins.add(static_pin)

    when = arc_config.get("when", "")
    for tok in when.replace(" ", "").replace("*", "&").split("&"):
        if not tok:
            continue
        pin = tok[1:] if tok.startswith("!") else tok
        if pin == arc_config.get("output_signal", "Q"):
            continue
        pins.add(pin)

    return list(pins)
