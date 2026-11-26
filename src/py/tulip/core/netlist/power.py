"""Leakage and hidden-internal-power stimulus generation.

Optional: timing-only builds may omit this module; the arc
dispatcher in ``__init__`` degrades gracefully when absent."""

from .common import (
    resolve_voltage,
)


def _resolve_when_to_state(when_str):
    """
    Convert "!CLK&!D&RESET_B&!Q" -> {"CLK": 0, "D": 0, "RESET_B": 1, "Q": 0}.
    Whitespace and '*' tolerated (Liberate uses '*' as AND in some places).
    """
    if not when_str:
        return {}
    expr = (when_str.replace(" ", "").replace("*", "&")
            .replace("(", "").replace(")", ""))
    state = {}
    for tok in expr.split("&"):
        if not tok:
            continue
        neg = tok.startswith("!")
        pin = tok[1:] if neg else tok
        state[pin] = 0 if neg else 1
    return state


def _resolve_static_from_when(arc_config):
    """
    Derive static_pins dict from a 'when' clause (for non-trigger,
    non-output pins) when arc_config has no explicit static_pins.
    """
    when = arc_config.get("when", "")
    state = _resolve_when_to_state(when)
    trigger_pin = arc_config.get("data_signal")
    output_signal = arc_config.get("output_signal", "Q")
    return {
        pin: ("VDD" if val == 1 else "0")
        for pin, val in state.items()
        if pin not in (trigger_pin, output_signal)
    }


def _infer_hidden_q(arc_config, _static_pins):
    """
    Infer the value Q must hold during a hidden_power_internal run.
    Priority order:
      1. arc_config['hidden_q'] explicit
      2. arc_config['when'] clause: parse out Q
      3. arc_config['initialization']: 'reset' -> Q=0, 'set' -> Q=1
      4. Default: 0
    """
    if "hidden_q" in arc_config:
        return int(arc_config["hidden_q"])
    when_state = _resolve_when_to_state(arc_config.get("when", ""))
    output_signal = arc_config.get("output_signal", "Q")
    if output_signal in when_state:
        return when_state[output_signal]
    init = arc_config.get("initialization")
    if init == "set":
        return 1
    if init == "reset":
        return 0
    return 0


def generate_leakage_stimuli(_sim_conditions, arc_config):
    """
    Stimuli for one leakage state.

    The dispatcher (characterizer_power._characterize_leakage) is expected
    to peel arc_config['states'] one state at a time before calling this -
    arc_config['_state'] (single dict) holds the active state for THIS run.

    Methodology (no .ic, monolith-style):
      - Output Q is forced via a brief RESET_B (or SET_B) pulse early in
        the run, then the pin is held DC for the rest of the window.
      - All other inputs are DC at the desired levels.
      - The transient is long (`power_settle_window_ns`, e.g. 20us) so
        i(VVDD) reaches steady-state by the tail.
    """
    state = arc_config.get("_state") or _resolve_when_to_state(
        arc_config.get("when", "")
    )
    if not state:
        return "", ""

    func_map = arc_config.get("function_mapping", {})
    reset_pin = func_map.get("reset_b")
    set_pin = func_map.get("set_b")
    output_signal = arc_config.get("output_signal", "Q")
    target_q = state.get(output_signal)

    sim_cfg = arc_config.get("_sim_cfg", {})
    preload_release_ns = sim_cfg.get("power_leakage_preload_release_ns", 5000)
    preload_edge_ns = sim_cfg.get("power_leakage_preload_edge_ns", 0.2)

    handled = set()
    stimuli = ""
    init = ""

    use_reset_pulse = (
        target_q == 0
        and reset_pin is not None
        and state.get(reset_pin) == 1
    )
    use_set_pulse = (
        target_q == 1
        and set_pin is not None
        and state.get(set_pin) == 1
    )

    if use_reset_pulse:
        # Active-low pulse: VDD -> 0 -> VDD, ending inactive
        # (matches state[reset_pin]==1)
        t_fall = round(preload_edge_ns + 0.1, 6)
        t_rise = round(preload_release_ns + preload_edge_ns, 6)
        stimuli += (
            f"V_{reset_pin} {reset_pin} VSS PWL("
            f"0 {{VDD_VAL}} "
            f"{preload_edge_ns}n {{VDD_VAL}} "
            f"{t_fall}n 0 "
            f"{preload_release_ns}n 0 "
            f"{t_rise}n {{VDD_VAL}}"
            f")\n"
        )
        handled.add(reset_pin)
    elif use_set_pulse:
        t_fall = round(preload_edge_ns + 0.1, 6)
        t_rise = round(preload_release_ns + preload_edge_ns, 6)
        stimuli += (
            f"V_{set_pin} {set_pin} VSS PWL("
            f"0 {{VDD_VAL}} "
            f"{preload_edge_ns}n {{VDD_VAL}} "
            f"{t_fall}n 0 "
            f"{preload_release_ns}n 0 "
            f"{t_rise}n {{VDD_VAL}}"
            f")\n"
        )
        handled.add(set_pin)

    # Edge case: target Q=1 with no SET_B pin available.
    # Capture Q=1 via a brief CLK rising edge while D=1.
    # Two sub-cases for the final CLK state:
    #   - target CLK=0: pulse CLK 0->VDD->0 (capture, then release low)
    #   - target CLK=1: ramp CLK 0->VDD and STAY HIGH (capture, leave high)
    if target_q == 1 and not use_set_pulse and not use_reset_pulse:
        clk_pin = next(
            (c for c in ("CLK", "GATE", "GATE_N") if c in state), "CLK")
        d_pin = "D"
        if (clk_pin == "GATE_N" and d_pin in state
                and clk_pin not in handled):
            # active-low latch: open (GATE_N=0) with D=1, then return GATE_N
            # to its state value; D moves to its state value afterwards
            t_fall = round(preload_edge_ns + 0.1, 6)
            t_rise = round(preload_release_ns + preload_edge_ns, 6)
            if state[d_pin] == 1:
                stimuli += f"V_{d_pin} {d_pin} VSS DC {{VDD_VAL}}\n"
            else:
                t_d0 = round(preload_release_ns + preload_edge_ns + 5, 6)
                stimuli += (
                    f"V_{d_pin} {d_pin} VSS PWL("
                    f"0 {{VDD_VAL}} {t_d0}n {{VDD_VAL}} "
                    f"{round(t_d0 + preload_edge_ns, 6)}n 0)\n")
            handled.add(d_pin)
            end = "{VDD_VAL}" if state[clk_pin] == 1 else "0"
            stimuli += (
                f"V_{clk_pin} {clk_pin} VSS PWL("
                f"0 {{VDD_VAL}} {preload_edge_ns}n {{VDD_VAL}} "
                f"{t_fall}n 0 {preload_release_ns}n 0 "
                f"{t_rise}n {end})\n")
            handled.add(clk_pin)
            if reset_pin and reset_pin not in handled:
                stimuli += f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
                handled.add(reset_pin)
        if (clk_pin in state and d_pin in state
                and clk_pin not in handled):
            t_fall = round(preload_edge_ns + 0.1, 6)
            t_rise = round(preload_release_ns + preload_edge_ns, 6)
            if state[d_pin] == 1:
                stimuli += f"V_{d_pin} {d_pin} VSS DC {{VDD_VAL}}\n"
            else:
                # capture Q=1 with D=1, then move D to its state value
                # after the CLK edge(s) so Q is not disturbed
                t_d0 = round(preload_release_ns + preload_edge_ns + 5, 6)
                stimuli += (
                    f"V_{d_pin} {d_pin} VSS PWL("
                    f"0 {{VDD_VAL}} {t_d0}n {{VDD_VAL}} "
                    f"{round(t_d0 + preload_edge_ns, 6)}n 0)\n")
            handled.add(d_pin)
            if state[clk_pin] == 0:
                # Pulse and return low
                stimuli += (
                    f"V_{clk_pin} {clk_pin} VSS PWL("
                    f"0 0 "
                    f"{preload_edge_ns}n 0 "
                    f"{t_fall}n {{VDD_VAL}} "
                    f"{preload_release_ns}n {{VDD_VAL}} "
                    f"{t_rise}n 0"
                    f")\n"
                )
            else:
                # Ramp up and STAY high (target CLK=1)
                stimuli += (
                    f"V_{clk_pin} {clk_pin} VSS PWL("
                    f"0 0 "
                    f"{preload_edge_ns}n 0 "
                    f"{t_fall}n {{VDD_VAL}} "
                    f"{preload_release_ns}n {{VDD_VAL}}"
                    f")\n"
                )
            handled.add(clk_pin)
            if reset_pin and reset_pin not in handled:
                stimuli += (
                    f"V_{reset_pin} {reset_pin} VSS DC {{VDD_VAL}}\n"
                )
                handled.add(reset_pin)

    # Everything else: DC at the state value, EXCEPT async pins that target
    # their asserted (active-low) level — those need a soft VDD->0 ramp
    # so the simulator can converge at t=0.
    async_pins_set = {reset_pin, set_pin}
    # never drive output pins (Q_N etc.): a DC source on an output fights
    # the cell
    out_pins = {output_signal} | {
        p for p in state if p.upper() in ("Q", "QN", "Q_N")}
    for pin, val in state.items():
        if pin in handled or pin in out_pins:
            continue
        if pin in async_pins_set and val == 0:
            # Soft ramp: start inactive, ramp to active over 0.5ns -> 1ns
            stimuli += (
                f"V_{pin} {pin} VSS PWL("
                f"0 {{VDD_VAL}} "
                f"0.5n {{VDD_VAL}} "
                f"1n 0 "
                f"{preload_release_ns}n 0)\n"
            )
        else:
            voltage = "{VDD_VAL}" if val == 1 else "0"
            stimuli += f"V_{pin} {pin} VSS DC {voltage}\n"
        handled.add(pin)

    # Async pins not yet handled: hold inactive
    for async_pin in (reset_pin, set_pin):
        if async_pin and async_pin not in handled:
            stimuli += f"V_{async_pin} {async_pin} VSS DC {{VDD_VAL}}\n"
            handled.add(async_pin)

    return stimuli, init


def generate_hidden_internal_stimuli(_sim_conditions, arc_config):
    """
    Stimuli for an internal_power arc where the trigger pin edges but Q
    stays put.

    Methodology (no .ic, monolith-style, Liberate-aligned math):
      - Trigger pin (data_signal) gets ONE PWL edge centered at
        settle_offset_ns, ramping over DATA_SLEW.
      - Other pins held DC.
      - Q is preloaded to its hidden value via brief PWL pulse on the
        appropriate async pin (or via CLK+D capture if no SET_B exists).
      - Integration window in the measurement layer is
        [settle_offset, settle_offset + power_tend], so the math is
        identical to Liberate's t=0 -> power_tend window.
    """
    sim_cfg = arc_config.get("_sim_cfg", {})
    settle_offset_ns = sim_cfg.get("power_settle_offset_ns", 100)

    trigger_pin = arc_config.get("data_signal")
    trigger_edge = arc_config.get("data_transition")
    if trigger_edge is None:
        trigger_edge = "rise"

    static_pins = arc_config.get("static_pins") or _resolve_static_from_when(
        arc_config
    )

    func_map = arc_config.get("function_mapping", {})
    reset_pin = func_map.get("reset_b")
    set_pin = func_map.get("set_b")
    output_signal = arc_config.get("output_signal", "Q")

    # When precomputed ICs are attached, the held state is established by the
    # IC block (Liberate 2-pass style), so skip the in-sim preload pulse and
    # emit only the clean trigger ramp — matching Liberate's single-ramp deck.
    has_ic = bool(arc_config.get("_ic_dict"))
    target_q = _infer_hidden_q(arc_config, static_pins)
    needs_preload_low = (target_q == 0) and not has_ic
    needs_preload_high = (target_q == 1) and not has_ic

    handled = set()
    stimuli = ""
    init = ""

    # ---- async-pin preload: force Q to target ----
    # Use RESET_B pulse if we want Q=0 and RESET_B is in static as VDD.
    # Start at VDD (inactive) for clean DC convergence, briefly assert
    # to reset Q, then release back to VDD.
    if (needs_preload_low
            and reset_pin
            and str(static_pins.get(reset_pin, "VDD")).upper() == "VDD"
            and reset_pin != trigger_pin):
        stimuli += (
            f"V_{reset_pin} {reset_pin} VSS PWL("
            f"0 {{VDD_VAL}} "
            f"1n {{VDD_VAL}} "
            f"1.1n 0 "
            f"10n 0 "
            f"10.1n {{VDD_VAL}} "
            f"100ns {{VDD_VAL}}"
            f")\n"
        )
        handled.add(reset_pin)

    # SET_B path (rare for sg13g2). Soft start same as RESET_B.
    if (needs_preload_high
            and set_pin
            and str(static_pins.get(set_pin, "VDD")).upper() == "VDD"
            and set_pin != trigger_pin):
        stimuli += (
            f"V_{set_pin} {set_pin} VSS PWL("
            f"0 {{VDD_VAL}} "
            f"1n {{VDD_VAL}} "
            f"1.1n 0 "
            f"10n 0 "
            f"10.1n {{VDD_VAL}} "
            f"100ns {{VDD_VAL}}"
            f")\n"
        )
        handled.add(set_pin)

    # Q=1 needed but no SET_B pin: capture via CLK + D=1 preload pulse.
    is_clk_trigger = (
        trigger_pin
        and trigger_pin.upper() in ("CLK", "CK", "CP", "GATE", "C")
    )
    if needs_preload_high and set_pin is None:
        clk_static = static_pins.get("CLK", "0")
        clk_dc_after = "{VDD_VAL}" if str(clk_static).upper() == "VDD" else "0"

        if is_clk_trigger:
            v_trig_end = "{VDD_VAL}" if trigger_edge == "rise" else "0"
            if has_ic:
                # ICs establish the held state; emit ONLY the clean single
                # ramp from the pre-edge level (Liberate single-ramp form).
                # Rise: 0 -> VDD. Fall: VDD -> 0.
                v_pre = "0" if trigger_edge == "rise" else "{VDD_VAL}"
                stimuli += (
                    f"V_CLK CLK VSS PWL("
                    f"0 {v_pre} "
                    f"{{ {settle_offset_ns}e-9 }} {v_pre} "
                    f"{{ {settle_offset_ns}e-9 + {{DATA_SLEW}} }} "
                    f"{v_trig_end}"
                    f")\n"
                )
            elif trigger_edge == "fall":
                stimuli += (
                    f"V_CLK CLK VSS PWL("
                    f"0 0 "
                    f"1.9n 0 "
                    f"2n {{VDD_VAL}} "
                    f"3.9n {{VDD_VAL}} "
                    f"4n 0 "
                    f"{settle_offset_ns - 5}n 0 "
                    f"{settle_offset_ns - 4}n {{VDD_VAL}} "
                    f"{{ {settle_offset_ns}e-9 }} "
                    f"{{VDD_VAL}} "
                    f"{{ {settle_offset_ns}e-9 + {{DATA_SLEW}} }} "
                    f"{v_trig_end}"
                    f")\n"
                )
            else:
                stimuli += (
                    f"V_CLK CLK VSS PWL("
                    f"0 0 "
                    f"1.9n 0 "
                    f"2n {{VDD_VAL}} "
                    f"3.9n {{VDD_VAL}} "
                    f"4n 0 "
                    f"{{ {settle_offset_ns}e-9 }} 0 "
                    f"{{ {settle_offset_ns}e-9 + {{DATA_SLEW}} }} "
                    f"{v_trig_end}"
                    f")\n"
                )
            handled.add(trigger_pin)
        elif "CLK" not in static_pins:
            stimuli += (
                f"V_CLK CLK VSS PWL("
                f"0 0 "
                f"1.9n 0 "
                f"2n {{VDD_VAL}} "
                f"3.9n {{VDD_VAL}} "
                f"4n 0 "
                f"{settle_offset_ns}n {clk_dc_after}"
                f")\n"
            )
            handled.add("CLK")

        d_static_v = (
            "{VDD_VAL}"
            if str(static_pins.get("D", "0")).upper() == "VDD"
            else "0"
        )
        stimuli += (
            f"V_D D VSS PWL("
            f"0 {{VDD_VAL}} "
            f"3.9n {{VDD_VAL}} "
            f"4n {d_static_v} "
            f"{settle_offset_ns}n {d_static_v}"
            f")\n"
        )
        handled.add("D")

    # ---- TRIGGER EDGE (skipped if combined into CLK preload above) ----
    if trigger_pin and trigger_pin not in handled:
        if trigger_edge == "rise":
            v_from, v_to = "0", "{VDD_VAL}"
        else:
            v_from, v_to = "{VDD_VAL}", "0"
        stimuli += (
            f"V_{trigger_pin} {trigger_pin} VSS PWL("
            f"0 {v_from} "
            f"{{ {settle_offset_ns}e-9 }} {v_from} "
            f"{{ {settle_offset_ns}e-9 + {{DATA_SLEW}} }} {v_to}"
            f")\n"
        )
        handled.add(trigger_pin)

    # ---- STATIC PINS (DC) ----
    # Async pins that are statically asserted (active-low at 0, or active-
    # high at VDD with a SET_B-style polarity convention) get a soft ramp
    # from inactive at t=0 down to their asserted level, so the DC OP
    # solver doesn't fight an asserted async pin at t=0.
    async_pins_set = {p for p in (reset_pin, set_pin) if p}
    for pin, state in static_pins.items():
        if pin in handled or pin == output_signal:
            continue
        if pin in async_pins_set and str(state).upper() == "0":
            # Active-low async pin held asserted: soft ramp VDD -> 0
            stimuli += (
                f"V_{pin} {pin} VSS PWL("
                f"0 {{VDD_VAL}} "
                f"1n {{VDD_VAL}} "
                f"1.1n 0 "
                f"100ns 0"
                f")\n"
            )
        else:
            stimuli += f"V_{pin} {pin} VSS DC {resolve_voltage(state)}\n"
        handled.add(pin)

    # Async pins still unhandled: hold inactive
    for async_pin in (reset_pin, set_pin):
        if async_pin and async_pin not in handled:
            stimuli += f"V_{async_pin} {async_pin} VSS DC {{VDD_VAL}}\n"
            handled.add(async_pin)

    return stimuli, init


# ---------------------------------------------------------------------------
# Pre-edge state (used by the ic_precompute settle and by the internal_power
# measurement stimulus below)
# ---------------------------------------------------------------------------

def _power_before_state(arc_config):
    """Logic level (0/1) of every input pin just BEFORE the measured edge,
    plus the target Q level. Returns (levels, q_target)."""
    levels = {}
    for pin, val in (arc_config.get("static_pins") or {}).items():
        levels[pin] = 1 if str(val).upper() == "VDD" else 0
    levels.update({k: v for k, v in _resolve_when_to_state(
        arc_config.get("when", "")).items()
        if k != arc_config.get("output_signal", "Q")})
    trig = arc_config.get("data_signal")
    if trig:
        # missing data_transition defaults to rise (same as the hidden
        # stimulus generator)
        _tr = arc_config.get("data_transition") or "rise"
        levels[trig] = 0 if _tr == "rise" else 1
    fmap = arc_config.get("function_mapping") or {}
    for rs_pin in (fmap.get("reset_b"), fmap.get("set_b")):
        if rs_pin and rs_pin not in levels:
            levels[rs_pin] = 1  # active-low async pins default inactive
    out = arc_config.get("output_signal", "Q")
    when_q = _resolve_when_to_state(arc_config.get("when", "")).get(out)
    init = arc_config.get("initialization")
    if when_q is not None:
        q_target = when_q
    elif init == "reset":
        q_target = 0
    elif init == "set":
        q_target = 1
    else:
        q_target = None
    return levels, q_target


def _generate_internal_ic_stimuli(_sim_conditions, arc_config):
    """internal_power stimulus when a settle .ic exists: every pin DC at its
    BEFORE level, trigger ramp starts at t=0, no preload sequence. Falls
    back to the prop-delay preload stimulus when no .ic is available."""
    if not arc_config.get("_ic_dict"):
        from .prop_delay import generate_prop_delay_stimuli
        return generate_prop_delay_stimuli(_sim_conditions, arc_config)
    levels, _ = _power_before_state(arc_config)
    trig = arc_config.get("data_signal")
    lines = []
    for pin, bit in levels.items():
        if pin == trig:
            v_from, v_to = (("0", "{VDD_VAL}") if bit == 0
                            else ("{VDD_VAL}", "0"))
            lines.append(f"V_{pin} {pin} VSS "
                         f"PWL(0 {v_from} {{DATA_SLEW}} {v_to})")
        else:
            lines.append(f"V_{pin} {pin} VSS DC "
                         f"{'{VDD_VAL}' if bit else '0'}")
    return "\n".join(lines) + "\n", ""
