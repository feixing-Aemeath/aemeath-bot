"""Plugin config (already checked against manifest.yaml) merged with code defaults."""

import hashlib
import json
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from plugins.health.metrics import METRICS, VIRTUAL_METRICS

DEVICE_CLASSES = ("watch", "phone", "other")
# Raised whenever the code stores daily rows differently (2: sleep_minutes counts naps; 3: an
# uploaded watch whose name lacks "Watch" counts as the watch the export showed it to be), so
# history derived by an older version is rebuilt like after a settings change.
DERIVATION = 3

# Engineering defaults, not clinically validated cut-offs; see docs/health-methods.md.
DEFAULT_ALERT_RULES = (
    {
        "id": "resting_hr_high",
        "metric": "resting_hr",
        "direction": "above",
        "z": 2.0,
        "min_delta": 3.0,
    },
    # "hrv": RMSSD when the day can be judged by it, else SDNN (Analyzer.preferred_hrv).
    {"id": "hrv_low", "metric": "hrv", "direction": "below", "z": 1.0},
    {
        "id": "sleep_duration_shift",
        "metric": "sleep_minutes",
        "direction": "either",
        "z": 2.0,
        "min_delta": 60.0,
    },
    {
        "id": "sleep_onset_shift",
        "metric": "sleep_onset",
        "direction": "either",
        "z": 2.0,
        "min_delta": 60.0,
    },
    {
        "id": "wake_time_shift",
        "metric": "wake_time",
        "direction": "either",
        "z": 2.0,
        "min_delta": 60.0,
    },
)

RULE_DEFAULTS = {"baseline": "30d", "z": 2.0, "min_delta": 0.0, "days": 3, "enabled": True}

# Banister TRIMP weighting y = k1 * exp(k2 * x); <Me> is ignored, so this is a setting.
TRIMP_WEIGHTING = {"male": (0.64, 1.92), "female": (0.86, 1.67)}

# Readiness components and their default weights (engineering defaults, normalized over the
# components a day has); see docs/health-methods.md, section 7.
READINESS_WEIGHTS = {"hrv": 0.35, "resting_hr": 0.2, "sleep": 0.3, "training_load": 0.15}


@dataclass(frozen=True)
class ReadinessConfig:
    weights: dict = field(default_factory=lambda: dict(READINESS_WEIGHTS))
    min_components: int = 2
    # Robust z (sign turned so that larger is better) mapped linearly: z_zero -> 0, z_full -> 10.
    z_full: float = 1.0
    z_zero: float = -3.0
    sleep_target_hours: float = 7.0
    sleep_zero_hours: float = 3.0
    acwr_full: float = 1.0
    acwr_zero: float = 2.0

    @classmethod
    def from_dict(cls, values):
        values = dict(values or {})
        weights = {**READINESS_WEIGHTS, **values.pop("weights", {})}
        config = cls(weights=weights, **values)
        if set(config.weights) != set(READINESS_WEIGHTS):
            raise ValueError("readiness weights are hrv, resting_hr, sleep and training_load")
        if any(w < 0 for w in config.weights.values()) or not sum(config.weights.values()):
            raise ValueError("readiness weights must be non-negative and not all zero")
        if not 1 <= config.min_components <= len(READINESS_WEIGHTS):
            raise ValueError("readiness min_components must be 1-4")
        if config.z_full <= config.z_zero:
            raise ValueError("readiness z_full must be above z_zero")
        if config.sleep_target_hours <= config.sleep_zero_hours:
            raise ValueError("readiness sleep_target_hours must be above sleep_zero_hours")
        if config.acwr_zero <= config.acwr_full:
            raise ValueError("readiness acwr_zero must be above acwr_full")
        return config


@dataclass(frozen=True)
class HealthConfig:
    timezone: str = "Asia/Shanghai"
    source_priority: tuple[str, ...] = DEVICE_CLASSES
    min_wear_hours: int = 12
    sleep_day_cutoff_hour: int = 18
    sleep_segment_gap_minutes: int = 30
    sleep_night_gap_minutes: int = 120
    min_sleep_minutes: int = 120
    baseline_min_days: dict = field(default_factory=lambda: {7: 4, 30: 14})
    notable_z: float = 2.0
    strong_z: float = 3.0
    mad_floors: dict = field(default_factory=lambda: {k: m.floor for k, m in METRICS.items()})
    sri_min_pairs: dict = field(default_factory=lambda: {7: 4, 30: 14})
    max_hr: float | None = None
    resting_hr: float | None = None
    trimp_weighting: str = "male"
    alert_rules: tuple[dict, ...] = tuple({**RULE_DEFAULTS, **r} for r in DEFAULT_ALERT_RULES)
    readiness: ReadinessConfig = field(default_factory=ReadinessConfig)

    @classmethod
    def from_dict(cls, config: dict | None):
        config = config or {}
        sleep, wear = config.get("sleep", {}), config.get("wear", {})
        baseline, sri = config.get("baseline", {}), config.get("sri", {})
        training = config.get("training", {})
        defaults = cls()
        values = dict(
            timezone=config.get("timezone", defaults.timezone),
            source_priority=tuple(config.get("source_priority", defaults.source_priority)),
            min_wear_hours=wear.get("min_hours", defaults.min_wear_hours),
            sleep_day_cutoff_hour=sleep.get("day_cutoff_hour", defaults.sleep_day_cutoff_hour),
            sleep_segment_gap_minutes=sleep.get(
                "segment_gap_minutes", defaults.sleep_segment_gap_minutes
            ),
            sleep_night_gap_minutes=sleep.get(
                "night_gap_minutes", defaults.sleep_night_gap_minutes
            ),
            min_sleep_minutes=sleep.get("min_minutes", defaults.min_sleep_minutes),
            baseline_min_days={
                7: baseline.get("min_days_7d", defaults.baseline_min_days[7]),
                30: baseline.get("min_days_30d", defaults.baseline_min_days[30]),
            },
            notable_z=baseline.get("notable_z", defaults.notable_z),
            strong_z=baseline.get("strong_z", defaults.strong_z),
            mad_floors={**defaults.mad_floors, **baseline.get("mad_floors", {})},
            sri_min_pairs={
                7: sri.get("min_pairs_7d", defaults.sri_min_pairs[7]),
                30: sri.get("min_pairs_30d", defaults.sri_min_pairs[30]),
            },
            max_hr=training.get("max_hr"),
            resting_hr=training.get("resting_hr"),
            trimp_weighting=training.get("weighting", defaults.trimp_weighting),
            readiness=ReadinessConfig.from_dict(config.get("readiness")),
        )
        if "alerts" in config:
            values["alert_rules"] = tuple({**RULE_DEFAULTS, **rule} for rule in config["alerts"])
        result = cls(**values)
        result.validate()
        return result

    def validate(self):
        ZoneInfo(self.timezone)
        if sorted(self.source_priority) != sorted(DEVICE_CLASSES):
            raise ValueError("source_priority must order watch, phone and other")
        if self.strong_z < self.notable_z:
            raise ValueError("strong_z must not be below notable_z")
        if self.trimp_weighting not in TRIMP_WEIGHTING:
            raise ValueError("unknown TRIMP weighting")
        if self.max_hr is not None and self.resting_hr is not None:
            if self.max_hr <= self.resting_hr + 20:
                raise ValueError("max_hr must exceed resting_hr")
        ids = [rule["id"] for rule in self.alert_rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate alert rule id")
        for rule in self.alert_rules:
            metric = rule["metric"]
            if metric not in VIRTUAL_METRICS and (
                metric not in METRICS or not METRICS[metric].scored
            ):
                raise ValueError("alert rules need a scored metric")

    def derived_fingerprint(self):
        """Settings that change stored daily rows; thresholds and rules are applied on read."""
        relevant = [
            DERIVATION,
            self.timezone,
            list(self.source_priority),
            self.min_wear_hours,
            self.sleep_day_cutoff_hour,
            self.sleep_segment_gap_minutes,
            self.sleep_night_gap_minutes,
            self.min_sleep_minutes,
            self.max_hr,
            self.resting_hr,
            self.trimp_weighting,
        ]
        return hashlib.sha256(json.dumps(relevant).encode()).hexdigest()[:16]
