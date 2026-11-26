"""Debug and curve plotting.
All matplotlib usage lives here, so a distribution that does not
need debug plots can omit this  package (callers guard the import).
"""

from .plotting_comb import (
    generate_overlapped_plot,
    save_single_plot,
)
from .plotting_mpw import (
    save_mpw_curve_plot,
    save_mpw_waveform,
)
from .plotting_seq import (
    generate_sequential_curve_plot,
    save_debug_waveform,
)

try:  # optional: timing-only builds omit this module
    from .plotting_power import save_power_plot
    HAS_POWER_PLOT = True
except ImportError:  # pragma: no cover
    HAS_POWER_PLOT = False

__all__ = [
    "save_single_plot",
    "generate_overlapped_plot",
    "save_mpw_waveform",
    "save_mpw_curve_plot",
    "save_debug_waveform",
    "generate_sequential_curve_plot",
    "save_power_plot",
]
