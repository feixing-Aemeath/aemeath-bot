"""Daily metrics that get personal baselines, deviations and alert rules."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    decimals: int
    floor: float  # smallest robust SD, so near-constant histories do not inflate z
    higher: str = "高于"
    lower: str = "低于"
    clock: bool = False  # minutes after the sleep-day cutoff, shown as local HH:MM
    # Values measured in different contexts (Apple RHR vs. sleeping HR) are only compared
    # with baseline days measured the same way.
    method_aware: bool = False
    # Daily TRIMP is zero on every rest day, so a z-score against its median says nothing;
    # it is judged through the 7/28-day training load instead.
    scored: bool = True


METRICS = {
    metric.key: metric
    for metric in (
        Metric("resting_hr", "静息心率", "bpm", 1, 1.0, method_aware=True),
        Metric("hrv_ln_sdnn", "HRV（ln SDNN）", "ln(ms)", 3, 0.05, method_aware=True),
        Metric("hrv_ln_rmssd", "HRV（ln RMSSD）", "ln(ms)", 3, 0.05, method_aware=True),
        Metric("respiratory_rate", "呼吸频率", "次/分", 1, 0.3, method_aware=True),
        Metric("oxygen_saturation", "血氧饱和度", "%", 1, 0.5, method_aware=True),
        Metric("wrist_temperature", "睡眠手腕温度", "°C", 2, 0.1),
        Metric("vo2max", "最大摄氧量", "mL/kg/min", 1, 0.5),
        Metric("steps", "步数", "步", 0, 500.0),
        Metric("active_energy", "活动能量", "kcal", 0, 50.0),
        Metric("trimp", "训练负荷 TRIMP", "AU", 1, 10.0, scored=False),
        Metric("sleep_minutes", "总睡眠时长", "分钟", 0, 15.0, "长于", "短于"),
        Metric("deep_minutes", "深睡时长", "分钟", 0, 5.0, "长于", "短于"),
        Metric("core_minutes", "核心睡眠时长", "分钟", 0, 10.0, "长于", "短于"),
        Metric("rem_minutes", "REM 睡眠时长", "分钟", 0, 5.0, "长于", "短于"),
        Metric("awake_minutes", "夜间清醒时长", "分钟", 0, 5.0, "长于", "短于"),
        Metric("sleep_onset", "入睡时间", "时刻", 0, 15.0, "晚于", "早于", clock=True),
        Metric("wake_time", "醒来时间", "时刻", 0, 15.0, "晚于", "早于", clock=True),
    )
}

# Apple Watch records SDNN, and from watchOS 27 also RMSSD. A day is judged by RMSSD when it
# can be (see Analyzer.preferred_hrv); each measure is only compared with its own history.
HRV_MEASURES = ("hrv_ln_rmssd", "hrv_ln_sdnn")
HRV_MS = {"hrv_ln_rmssd": "rmssd_ms", "hrv_ln_sdnn": "sdnn_ms"}
HRV_NAMES = {"hrv_ln_rmssd": "RMSSD", "hrv_ln_sdnn": "SDNN"}
# Alert rules may name "hrv": each day uses that day's preferred HRV measure.
VIRTUAL_METRICS = {"hrv": HRV_MEASURES}

SLEEP_METRICS = (
    "sleep_minutes",
    "deep_minutes",
    "core_minutes",
    "rem_minutes",
    "awake_minutes",
    "sleep_onset",
    "wake_time",
)
