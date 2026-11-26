"""Simulation package.

Public API is unchanged: ``from .simulation import run_single_simulation``
keeps working because this package re-exports it.
"""

from .simulation import (
    run_simulation_for_plot,
    run_single_simulation,
)

__all__ = ["run_single_simulation", "run_simulation_for_plot"]
