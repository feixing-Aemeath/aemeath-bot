"""Robust statistics for personal baselines."""

import math

# 1.4826 * MAD estimates the standard deviation for normally distributed data.
MAD_TO_SD = 1.4826


def median(values):
    ordered = sorted(values)
    if not ordered:
        raise ValueError("median of no values")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def mad(values, center=None):
    """Median absolute deviation (unscaled)."""
    center = median(values) if center is None else center
    return median([abs(value - center) for value in values])


def quantile(values, q):
    """Linear interpolation between order statistics (the common "type 7" definition)."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def stdev(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def robust_z(value, center, mad_value, floor):
    """(x - median) / max(1.4826 * MAD, floor); the floor keeps a flat history from turning
    tiny changes into large z values."""
    return (value - center) / max(MAD_TO_SD * mad_value, floor)


def level(z, notable_z, strong_z):
    if abs(z) >= strong_z:
        return "far_above" if z > 0 else "far_below"
    if abs(z) >= notable_z:
        return "above" if z > 0 else "below"
    return "normal"
