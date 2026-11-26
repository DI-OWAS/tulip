"""Shared context dataclasses used across arc, cell and power emission."""

from dataclasses import dataclass, field


@dataclass
class ArcContext:
    """Bundles per-arc handler arguments."""
    arc: dict
    arc_name: str
    in_pin: str
    out_pin: str
    clk_pin: str
    lib_cell: object
    res_path: object
    clock_pins: set = field(default_factory=set)


@dataclass
class PinSets:
    """Bundles pin collection results for post-processing."""
    input_pins: set
    output_pins: set
    clock_pins: set
    is_sequential: bool
