import numpy as np


def has_any_data(data_dict):
    """
    Recursively check if nested dictionary contains non empty data.

    Args:
        data_dict: Dictionary to check (can be nested)

    Returns:
        bool: True if any non-NaN, non-None data exists
    """
    if not isinstance(data_dict, dict):
        return (data_dict is not None
                and not (isinstance(data_dict, float) and np.isnan(data_dict)))
    return any(has_any_data(v) for v in data_dict.values())
