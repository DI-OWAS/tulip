"""
Core measurement functions for timing analysis.
"""
import numpy as np


def find_crossing_time(
    time_vec, voltage_vec, threshold, direction, search_after=0.0
):
    """
    Find the time when voltage crosses a threshold.

    Args:
        time_vec: Time vector (seconds)
        voltage_vec: Voltage vector (V)
        threshold: Crossing threshold (V)
        direction: 'rise' or 'fall'
        search_after: Only search after this time (seconds)

    Returns:
        Crossing time in seconds, or None if not found
    """
    valid_indices = np.where(time_vec >= search_after)[0]
    if len(valid_indices) < 2:
        return None
    time, voltage = time_vec[valid_indices], voltage_vec[valid_indices]
    if direction == 'rise':
        indices = np.where(
            (voltage[:-1] < threshold) & (voltage[1:] >= threshold)
        )[0]
    else:
        indices = np.where(
            (voltage[:-1] > threshold) & (voltage[1:] <= threshold)
        )[0]
    if not indices.size:
        return None
    idx = indices[0]
    time1, volt1 = time[idx], voltage[idx]
    time2, volt2 = time[idx + 1], voltage[idx + 1]
    if abs(volt2 - volt1) > 1e-9:
        return time1 + (time2 - time1) * (threshold - volt1) / (volt2 - volt1)
    return time1
