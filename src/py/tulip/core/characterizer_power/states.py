"""Logic-state resolution for power arcs (when-conditions and
hidden-arc pairing)."""


def _resolve_when_to_state(when_str):
    """Same as netlist._resolve_when_to_state; duplicated here to avoid
    a hard import dependency."""
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


def _resolve_hidden_pair(internal_arc_name):
    """Map a switching internal_power arc to its corresponding hidden arc.

    Pairing convention (Liberate-style):
      Q_rise  (Q: 0 -> 1):  hidden = starting state Q=0
                            with D matching after-state
      Q_fall  (Q: 1 -> 0):  hidden = starting state Q=1
                            with D matching after-state
      Q_RESET_B_fall (Q: 1 -> 0): hidden with RESET_B asserted, Q=0 baseline
    """
    table = {
        "internal_power_Q_rise": "internal_power_CLK_D0_RB1_Q0",
        "internal_power_Q_fall": "internal_power_CLK_D1_RB1_Q1",
        "internal_power_Q_RESET_B_fall": "internal_power_CLK_D0_RB0_Q0",
    }
    return table.get(internal_arc_name)
