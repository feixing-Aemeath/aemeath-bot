"""Resting heart rate, HRV, wear time and a spike-resistant daily maximum heart rate."""

import math

from plugins.health.stats import median, quantile

SLEEP_HR_MIN_SAMPLES = 10
SLEEP_HR_MIN_SPAN = 2 * 3600


def resting_heart_rate(apple_samples, sleep_samples):
    """(bpm, method, device class) for one day.

    Apple Watch's own RestingHeartRate uses motion context we cannot reproduce, so it is used
    when present (median of that day's values). Otherwise the 10th percentile of heart rate
    during the main sleep approximates it. The two methods differ systematically, so baselines
    only compare days computed with the same method.
    """
    if apple_samples:
        return median([s.value for s in apple_samples]), "apple", apple_samples[0].device_class
    if len(sleep_samples) >= SLEEP_HR_MIN_SAMPLES:
        span = sleep_samples[-1].start - sleep_samples[0].start
        if span >= SLEEP_HR_MIN_SPAN:
            value = quantile([s.value for s in sleep_samples], 0.10)
            return value, "sleep_hr", sleep_samples[0].device_class
    return None, None, None


def log_mean(samples):
    """Mean of ln(value); HRV (SDNN or RMSSD, ms) is log-transformed before comparison."""
    return sum(math.log(s.value) for s in samples) / len(samples)


def wear_hours(samples, day_start, day_end):
    """Elapsed hours of the local day with at least one heart-rate reading."""
    hours = set()
    for sample in samples:
        if sample.end > sample.start:
            low, high = max(sample.start, day_start), min(sample.end, day_end)
            if high > low:
                hours.update(range((low - day_start) // 3600, (high - 1 - day_start) // 3600 + 1))
        elif day_start <= sample.start < day_end:
            hours.add((sample.start - day_start) // 3600)
    return len(hours)


def robust_max(values):
    """Largest 3-reading rolling median: a single optical spike cannot set the maximum."""
    if len(values) < 3:
        return None
    return max(sorted(values[i : i + 3])[1] for i in range(len(values) - 2))
