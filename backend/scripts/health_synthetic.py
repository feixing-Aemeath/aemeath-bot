"""Write a synthetic Apple Health export.xml: Apple's format, an invented person's data.

Used by the tests and to try the importer without real data:

    uv run python -m scripts.health_synthetic .cache/synthetic/export.xml --days 60

The data deliberately contains what real exports contain: watch and phone recording the same
steps and nights, exact duplicate records, days without the watch, nights split in two, naps,
a single optical heart-rate spike, a workout written twice (watch and a third-party app), and
the last days with a raised resting heart rate and lowered HRV. With --rmssd-days N the last N
nights also carry RMSSD ("Recovery HRV", iOS / watchOS 27) next to SDNN.
"""

import argparse
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from xml.sax.saxutils import quoteattr

OFFSET = "+0800"
WATCH = "示例用户的Apple Watch"
PHONE = "示例用户的iPhone"
WATCH_DEVICE = (
    "<<HKDevice: 0x{:x}>, name:Apple Watch, manufacturer:Apple Inc., model:Watch, "
    "hardware:Watch7,2, software:11.0>"
)
PHONE_DEVICE = (
    "<<HKDevice: 0x{:x}>, name:iPhone, manufacturer:Apple Inc., model:iPhone, "
    "hardware:iPhone16,1, software:18.0>"
)
SLEEP = "HKCategoryValueSleepAnalysis"
# The invented person's RMSSD relative to their SDNN; an arbitrary choice for test data.
RMSSD_RATIO = 1.3

HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!-- HealthKit Export Version: 14 -->
<!ELEMENT HealthData (ExportDate,Me,(Record|Correlation|Workout|ActivitySummary|ClinicalRecord|Audiogram|VisionPrescription)*)>
<!ATTLIST HealthData
  locale CDATA #REQUIRED
>
<!ELEMENT ExportDate EMPTY>
<!ATTLIST ExportDate
  value CDATA #REQUIRED
>
<!ELEMENT Me EMPTY>
<!ATTLIST Me
  HKCharacteristicTypeIdentifierDateOfBirth                 CDATA #REQUIRED
  HKCharacteristicTypeIdentifierBiologicalSex               CDATA #REQUIRED
  HKCharacteristicTypeIdentifierBloodType                   CDATA #REQUIRED
  HKCharacteristicTypeIdentifierFitzpatrickSkinType         CDATA #REQUIRED
  HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse CDATA #REQUIRED
>
<!ELEMENT Record ((MetadataEntry|HeartRateVariabilityMetadataList)*)>
<!ATTLIST Record
  type          CDATA #REQUIRED
  unit          CDATA #IMPLIED
  value         CDATA #IMPLIED
  sourceName    CDATA #REQUIRED
  sourceVersion CDATA #IMPLIED
  device        CDATA #IMPLIED
  creationDate  CDATA #IMPLIED
  startDate     CDATA #REQUIRED
  endDate       CDATA #REQUIRED
>
<!ELEMENT Correlation ((MetadataEntry|Record)*)>
<!ATTLIST Correlation
  type          CDATA #REQUIRED
  sourceName    CDATA #REQUIRED
  sourceVersion CDATA #IMPLIED
  device        CDATA #IMPLIED
  creationDate  CDATA #IMPLIED
  startDate     CDATA #REQUIRED
  endDate       CDATA #REQUIRED
>
<!ELEMENT Workout ((MetadataEntry|WorkoutEvent|WorkoutRoute|WorkoutStatistics)*)>
<!ATTLIST Workout
  workoutActivityType   CDATA #REQUIRED
  duration              CDATA #IMPLIED
  durationUnit          CDATA #IMPLIED
  totalDistance         CDATA #IMPLIED
  totalDistanceUnit     CDATA #IMPLIED
  totalEnergyBurned     CDATA #IMPLIED
  totalEnergyBurnedUnit CDATA #IMPLIED
  sourceName            CDATA #REQUIRED
  sourceVersion         CDATA #IMPLIED
  device                CDATA #IMPLIED
  creationDate          CDATA #IMPLIED
  startDate             CDATA #REQUIRED
  endDate               CDATA #REQUIRED
>
<!ELEMENT WorkoutEvent EMPTY>
<!ATTLIST WorkoutEvent
  type         CDATA #REQUIRED
  date         CDATA #REQUIRED
  duration     CDATA #IMPLIED
  durationUnit CDATA #IMPLIED
>
<!ELEMENT WorkoutStatistics EMPTY>
<!ATTLIST WorkoutStatistics
  type      CDATA #REQUIRED
  startDate CDATA #REQUIRED
  endDate   CDATA #REQUIRED
  average   CDATA #IMPLIED
  minimum   CDATA #IMPLIED
  maximum   CDATA #IMPLIED
  sum       CDATA #IMPLIED
  unit      CDATA #IMPLIED
>
<!ELEMENT WorkoutRoute ((MetadataEntry|FileReference)*)>
<!ATTLIST WorkoutRoute
  sourceName    CDATA #REQUIRED
  sourceVersion CDATA #IMPLIED
  device        CDATA #IMPLIED
  creationDate  CDATA #IMPLIED
  startDate     CDATA #REQUIRED
  endDate       CDATA #REQUIRED
>
<!ELEMENT FileReference EMPTY>
<!ATTLIST FileReference
  path CDATA #REQUIRED
>
<!ELEMENT ActivitySummary EMPTY>
<!ATTLIST ActivitySummary
  dateComponents           CDATA #IMPLIED
  activeEnergyBurned       CDATA #IMPLIED
  activeEnergyBurnedGoal   CDATA #IMPLIED
  activeEnergyBurnedUnit   CDATA #IMPLIED
  appleMoveTime            CDATA #IMPLIED
  appleMoveTimeGoal        CDATA #IMPLIED
  appleExerciseTime        CDATA #IMPLIED
  appleExerciseTimeGoal    CDATA #IMPLIED
  appleStandHours          CDATA #IMPLIED
  appleStandHoursGoal      CDATA #IMPLIED
>
<!ELEMENT MetadataEntry EMPTY>
<!ATTLIST MetadataEntry
  key   CDATA #REQUIRED
  value CDATA #REQUIRED
>
<!ELEMENT HeartRateVariabilityMetadataList (InstantaneousBeatsPerMinute*)>
<!ELEMENT InstantaneousBeatsPerMinute EMPTY>
<!ATTLIST InstantaneousBeatsPerMinute
  bpm  CDATA #REQUIRED
  time CDATA #REQUIRED
>
]>
<HealthData locale="zh_CN">
"""


@dataclass
class Plan:
    day: date
    worn: bool
    rest_hr: float
    hrv_ms: float
    stages: list = field(default_factory=list)  # (start, end, stage) of the night ending today
    nap: list = field(default_factory=list)
    workout: tuple | None = None  # (start, end)
    phone_asleep: bool = False
    duplicate_stage: bool = False
    duplicate_workout: bool = False
    spike: bool = False


def stamp(moment):
    return f"{moment:%Y-%m-%d %H:%M:%S} {OFFSET}"


def night_stages(rng, onset, wake, split):
    """Contiguous watch stages from onset to wake, framed by short awake segments."""
    stages = [(onset - timedelta(minutes=6), onset, "Awake")]
    cursor, cycle = onset, 0
    gap = (onset + timedelta(hours=2, minutes=30), onset + timedelta(hours=3, minutes=40))
    while cursor < wake:
        pattern = [("AsleepCore", 25), ("AsleepDeep", 28 - 6 * cycle), ("AsleepCore", 15)]
        pattern += [("AsleepREM", 12 + 6 * cycle)]
        if cycle % 2:
            pattern.append(("Awake", 4))
        for stage, minutes in pattern:
            minutes = max(5, minutes + rng.randint(-3, 3))
            end = min(wake, cursor + timedelta(minutes=minutes))
            if split and cursor < gap[1] and end > gap[0]:
                # The night is split by 70 minutes without any record (out of bed).
                if cursor < gap[0]:
                    stages.append((cursor, gap[0], stage))
                cursor = gap[1]
                break
            stages.append((cursor, end, stage))
            cursor = end
            if cursor >= wake:
                break
        cycle += 1
    stages.append((wake, wake + timedelta(minutes=4), "Awake"))
    return stages


def make_plans(days, end, seed, missing, stress_days):
    rng = random.Random(seed)
    first = end - timedelta(days=days - 1)
    plans = []
    for index in range(days):
        day = first + timedelta(days=index)
        stressed = index >= days - stress_days
        plan = Plan(
            day=day,
            worn=index not in missing,
            rest_hr=56 + rng.uniform(-1.5, 1.5) + (9 if stressed else 0),
            hrv_ms=45 * math.exp(rng.gauss(0, 0.08)) * (0.6 if stressed else 1),
        )
        onset = datetime.combine(day - timedelta(days=1), time(23, 20)) + timedelta(
            minutes=rng.randint(-25, 25)
        )
        wake = datetime.combine(day, time(7, 10)) + timedelta(minutes=rng.randint(-20, 20))
        if plan.worn:
            plan.stages = night_stages(rng, onset, wake, split=index % 11 == 5)
            if index % 6 == 3:
                nap = datetime.combine(day, time(13, 10))
                plan.nap = [(nap, nap + timedelta(minutes=35), "AsleepCore")]
            if day.weekday() in (0, 2, 5):
                start = datetime.combine(day, time(18, 30)) + timedelta(minutes=rng.randint(0, 20))
                plan.workout = (start, start + timedelta(minutes=rng.randint(35, 50)))
        plan.phone_asleep = index % 4 == 1
        plan.duplicate_stage = index % 9 == 2
        plan.duplicate_workout = plan.workout is not None and index % 7 == 0
        plan.spike = index % 13 == 4
        plans.append(plan)
    return plans


def attributes(**values):
    return " ".join(f"{key}={quoteattr(str(value))}" for key, value in values.items())


class Writer:
    def __init__(self, stream, seed):
        self.stream, self.rng = stream, random.Random(seed + 1)
        self.address = 0x2830000

    def device(self, template):
        self.address += 0x40
        return template.format(self.address)

    def record(self, record_type, source, template, start, end, value=None, unit=None, body=""):
        values = {"type": record_type, "sourceName": source, "sourceVersion": "11.0"}
        if template:
            values["device"] = self.device(template)
        if unit:
            values["unit"] = unit
        values.update(creationDate=stamp(end + timedelta(seconds=30)))
        values.update(startDate=stamp(start), endDate=stamp(end))
        if value is not None:
            values["value"] = value
        if body:
            self.stream.write(f" <Record {attributes(**values)}>\n{body} </Record>\n")
        else:
            self.stream.write(f" <Record {attributes(**values)}/>\n")


def heart_rate_series(plan, tonight, rng, interval_minutes, workout_seconds):
    """(moment, bpm) readings: background every few minutes, dense during the workout.

    `tonight` is the plan of the next day, whose night starts this evening.
    """
    if not plan.worn:
        return []
    day_start = datetime.combine(plan.day, time())
    readings = []
    sleeping = [(s, e) for s, e, stage in plan.stages if stage != "Awake"]
    if tonight is not None:
        sleeping += [(s, e) for s, e, stage in tonight.stages if stage != "Awake"]
    moment = day_start + timedelta(minutes=rng.randint(0, interval_minutes - 1))
    while moment < day_start + timedelta(days=1):
        charging = time(21, 0) <= moment.time() < time(22, 0)
        in_workout = plan.workout and plan.workout[0] <= moment < plan.workout[1]
        if not charging and not in_workout:
            if any(s <= moment < e for s, e in sleeping):
                rest = tonight.rest_hr if tonight and moment.hour >= 12 else plan.rest_hr
                bpm = rest + 3 + rng.gauss(0, 2.5)
            else:
                bpm = 78 + rng.gauss(0, 8)
            readings.append((moment, round(bpm)))
        moment += timedelta(minutes=interval_minutes, seconds=rng.randint(-40, 40))
    if plan.workout:
        start, end = plan.workout
        total = (end - start).total_seconds()
        moment = start + timedelta(seconds=3)
        while moment < end:
            share = (moment - start).total_seconds() / total
            if share < 0.12:
                bpm = 110 + 340 * share
            elif share > 0.9:
                bpm = 150 - 300 * (share - 0.9)
            else:
                bpm = 152 + 12 * math.sin(share * 9) + (14 if 0.55 < share < 0.62 else 0)
            readings.append((moment, round(bpm + rng.gauss(0, 2))))
            moment += timedelta(seconds=workout_seconds)
    if plan.spike:
        readings.append((day_start + timedelta(hours=10, minutes=3), 228))
    return sorted(readings)


def hrv_body(rng, moment, rest_hr):
    lines = ["  <HeartRateVariabilityMetadataList>"]
    for beat in range(40):
        when = moment + timedelta(seconds=beat * 1.05)
        hour = when.hour % 12 or 12
        clock = f"{hour}:{when.minute:02d}:{when.second + when.microsecond / 1e6:05.2f}"
        clock += " AM" if when.hour < 12 else " PM"
        bpm = round(rest_hr + rng.gauss(0, 3))
        lines.append(f"   <InstantaneousBeatsPerMinute {attributes(bpm=bpm, time=clock)}/>")
    lines.append("  </HeartRateVariabilityMetadataList>")
    return "\n".join(lines) + "\n"


def write_export(
    path,
    days=60,
    end=date(2026, 10, 12),
    seed=7,
    missing=(20, 21),
    stress_days=3,
    hr_interval=10,
    workout_seconds=10,
    rmssd_days=0,
):
    """Write the file and return the planted ground truth per day."""
    plans = make_plans(days, end, seed, set(missing), stress_days)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    truth = {}
    with path.open("w", encoding="utf-8") as stream:
        out = Writer(stream, seed)
        stream.write(HEADER)
        stream.write(f' <ExportDate value="{stamp(datetime.combine(end, time(21, 5)))}"/>\n')
        stream.write(
            ' <Me HKCharacteristicTypeIdentifierDateOfBirth="1999-09-09" '
            'HKCharacteristicTypeIdentifierBiologicalSex="HKBiologicalSexNotSet" '
            'HKCharacteristicTypeIdentifierBloodType="HKBloodTypeABPositive" '
            'HKCharacteristicTypeIdentifierFitzpatrickSkinType="HKFitzpatrickSkinTypeNotSet" '
            'HKCharacteristicTypeIdentifierCardioFitnessMedicationsUse="None"/>\n'
        )
        # Like real exports, records are grouped by type rather than sorted by time.
        following = {plan.day: plans[i + 1] for i, plan in enumerate(plans[:-1])}
        for plan in plans:
            rng = random.Random(f"{seed}-hr-{plan.day}")
            series = heart_rate_series(
                plan, following.get(plan.day), rng, hr_interval, workout_seconds
            )
            for moment, bpm in series:
                body = '  <MetadataEntry key="HKMetadataKeyHeartRateMotionContext" value="0"/>\n'
                out.record(
                    "HKQuantityTypeIdentifierHeartRate",
                    WATCH,
                    WATCH_DEVICE,
                    moment,
                    moment,
                    bpm,
                    "count/min",
                    body,
                )
        for plan in plans:
            if plan.worn:
                start = datetime.combine(plan.day, time(0, 4, 56))
                out.record(
                    "HKQuantityTypeIdentifierRestingHeartRate",
                    WATCH,
                    None,
                    start,
                    start + timedelta(hours=23, minutes=46),
                    round(plan.rest_hr, 1),
                    "count/min",
                )
        for plan in plans:
            rng = random.Random(f"{seed}-night-{plan.day}")
            asleep = [(s, e) for s, e, stage in plan.stages if stage != "Awake"]
            for index in range(3 if asleep else 0):
                moment = asleep[min(len(asleep) - 1, 2 + index * 4)][0] + timedelta(minutes=2)
                value = round(plan.hrv_ms * math.exp(rng.gauss(0, 0.05)), 3)
                out.record(
                    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
                    WATCH,
                    WATCH_DEVICE,
                    moment,
                    moment + timedelta(seconds=58),
                    value,
                    "ms",
                    hrv_body(rng, moment, plan.rest_hr),
                )
            if plan.day > end - timedelta(days=rmssd_days):
                # A separate random stream, so the rest of the file is the same with or
                # without RMSSD.
                extra = random.Random(f"{seed}-rmssd-{plan.day}")
                for index in range(3 if asleep else 0):
                    moment = asleep[min(len(asleep) - 1, 3 + index * 4)][0] + timedelta(minutes=5)
                    value = plan.hrv_ms * RMSSD_RATIO * math.exp(extra.gauss(0, 0.05))
                    out.record(
                        "HKQuantityTypeIdentifierHeartRateVariabilityRMSSD",
                        WATCH,
                        WATCH_DEVICE,
                        moment,
                        moment + timedelta(seconds=58),
                        round(value, 3),
                        "ms",
                    )
            for index in range(4 if asleep else 0):
                moment = asleep[min(len(asleep) - 1, 1 + index * 3)][0] + timedelta(minutes=1)
                out.record(
                    "HKQuantityTypeIdentifierRespiratoryRate",
                    WATCH,
                    WATCH_DEVICE,
                    moment,
                    moment + timedelta(minutes=2),
                    round(14.5 + rng.gauss(0, 0.6), 1),
                    "count/min",
                )
                out.record(
                    "HKQuantityTypeIdentifierOxygenSaturation",
                    WATCH,
                    WATCH_DEVICE,
                    moment,
                    moment,
                    round(0.975 + rng.uniform(-0.015, 0.015), 2),
                    "%",
                )
            if asleep:
                out.record(
                    "HKQuantityTypeIdentifierAppleSleepingWristTemperature",
                    WATCH,
                    WATCH_DEVICE,
                    asleep[0][0],
                    asleep[-1][1],
                    round(36.3 + rng.gauss(0, 0.1), 2),
                    "degC",
                )
        for plan in plans:
            rng = random.Random(f"{seed}-steps-{plan.day}")
            moment = datetime.combine(plan.day, time(8, 0))
            watch_steps = phone_steps = 0.0
            while moment < datetime.combine(plan.day, time(22, 30)):
                length = timedelta(minutes=rng.randint(3, 9))
                steps = rng.randint(80, 700)
                charging = time(21, 0) <= moment.time() < time(22, 0)
                if plan.worn and not charging:
                    out.record(
                        "HKQuantityTypeIdentifierStepCount",
                        WATCH,
                        WATCH_DEVICE,
                        moment,
                        moment + length,
                        steps,
                        "count",
                    )
                    watch_steps += steps
                # The phone counts the same walk a little differently, and alone while the
                # watch charges or stays in the drawer.
                phone = round(steps * rng.uniform(0.85, 1.05))
                out.record(
                    "HKQuantityTypeIdentifierStepCount",
                    PHONE,
                    PHONE_DEVICE,
                    moment + timedelta(seconds=20),
                    moment + length + timedelta(seconds=20),
                    phone,
                    "count",
                )
                if not plan.worn or charging:
                    phone_steps += phone
                moment += length + timedelta(minutes=rng.randint(8, 45))
            truth.setdefault(plan.day, {})["steps_watch"] = watch_steps
            truth[plan.day]["steps_phone_only"] = phone_steps
        for plan in plans:
            if not plan.worn:
                continue
            rng = random.Random(f"{seed}-energy-{plan.day}")
            unit = "Cal" if plan.day.day % 2 else "kcal"
            for half_hour in range(48):
                moment = datetime.combine(plan.day, time()) + timedelta(minutes=30 * half_hour)
                out.record(
                    "HKQuantityTypeIdentifierActiveEnergyBurned",
                    WATCH,
                    WATCH_DEVICE,
                    moment,
                    moment + timedelta(minutes=29),
                    round(rng.uniform(2, 25), 2),
                    unit,
                )
        for plan in plans:
            if plan.workout and plan.day.weekday() == 5:
                end_time = plan.workout[1] + timedelta(minutes=5)
                out.record(
                    "HKQuantityTypeIdentifierVO2Max",
                    WATCH,
                    None,
                    end_time,
                    end_time,
                    45.2,
                    "mL/min·kg",
                )
        for plan in plans:
            in_bed_start = plan.stages[0][0] - timedelta(minutes=20) if plan.stages else None
            if in_bed_start is None:
                # Watch left in the drawer: only the phone's bedtime estimate exists.
                start = datetime.combine(plan.day - timedelta(days=1), time(23, 30))
                out.record(
                    "HKCategoryTypeIdentifierSleepAnalysis",
                    PHONE,
                    PHONE_DEVICE,
                    start,
                    start + timedelta(hours=7, minutes=40),
                    f"{SLEEP}InBed",
                )
                continue
            out.record(
                "HKCategoryTypeIdentifierSleepAnalysis",
                PHONE,
                PHONE_DEVICE,
                in_bed_start,
                plan.stages[-1][1] + timedelta(minutes=10),
                f"{SLEEP}InBed",
            )
            if plan.phone_asleep:
                # The phone's own "asleep" guess overlaps the watch night and must lose.
                out.record(
                    "HKCategoryTypeIdentifierSleepAnalysis",
                    PHONE,
                    PHONE_DEVICE,
                    in_bed_start + timedelta(minutes=5),
                    plan.stages[-1][1],
                    f"{SLEEP}AsleepUnspecified",
                )
            for start, end_time, stage in plan.stages + plan.nap:
                copies = 2 if plan.duplicate_stage and stage == "AsleepDeep" else 1
                for _ in range(copies):
                    out.record(
                        "HKCategoryTypeIdentifierSleepAnalysis",
                        WATCH,
                        WATCH_DEVICE,
                        start,
                        end_time,
                        f"{SLEEP}{stage}",
                    )
            minutes = {}
            for start, end_time, stage in plan.stages:
                minutes[stage] = minutes.get(stage, 0) + (end_time - start).total_seconds() / 60
            asleep_parts = [(s, e) for s, e, stage in plan.stages if stage != "Awake"]
            truth[plan.day].update(
                asleep=sum(v for k, v in minutes.items() if k != "Awake"),
                deep=minutes.get("AsleepDeep", 0),
                rem=minutes.get("AsleepREM", 0),
                core=minutes.get("AsleepCore", 0),
                awake=minutes.get("Awake", 0),
                onset=asleep_parts[0][0],
                wake=asleep_parts[-1][1],
                nap=sum((e - s).total_seconds() / 60 for s, e, _ in plan.nap),
            )
        for plan in plans:
            truth[plan.day].update(worn=plan.worn, rest_hr=round(plan.rest_hr, 1))
            truth[plan.day]["hrv_ms"] = plan.hrv_ms
            if plan.worn and plan.day > end - timedelta(days=rmssd_days):
                truth[plan.day]["rmssd_ms"] = plan.hrv_ms * RMSSD_RATIO
            if not plan.workout:
                continue
            start, end_time = plan.workout
            readings = [
                bpm
                for moment, bpm in heart_rate_series(
                    plan,
                    following.get(plan.day),
                    random.Random(f"{seed}-hr-{plan.day}"),
                    hr_interval,
                    workout_seconds,
                )
                if start <= moment <= end_time
            ]
            minutes = (end_time - start).total_seconds() / 60
            body = (
                '  <MetadataEntry key="HKIndoorWorkout" value="0"/>\n'
                '  <MetadataEntry key="HKTimeZone" value="Asia/Shanghai"/>\n'
                f'  <WorkoutEvent type="HKWorkoutEventTypeSegment" date="{stamp(start)}" '
                f'duration="{minutes / 2:.4f}" durationUnit="min"/>\n'
                f'  <WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" '
                f'startDate="{stamp(start)}" endDate="{stamp(end_time)}" '
                f'average="{sum(readings) / len(readings):.2f}" minimum="{min(readings)}" '
                f'maximum="{max(readings)}" unit="count/min"/>\n'
                f'  <WorkoutStatistics type="HKQuantityTypeIdentifierActiveEnergyBurned" '
                f'startDate="{stamp(start)}" endDate="{stamp(end_time)}" sum="{minutes * 10:.1f}" '
                f'unit="kcal"/>\n'
                f"  <WorkoutRoute {attributes(sourceName=WATCH, sourceVersion='11.0')} "
                f'creationDate="{stamp(end_time)}" startDate="{stamp(start)}" '
                f'endDate="{stamp(end_time)}">\n'
                f'   <FileReference path="/workout-routes/route_{plan.day}.gpx"/>\n'
                "  </WorkoutRoute>\n"
            )
            workout = {
                "workoutActivityType": "HKWorkoutActivityTypeRunning",
                "duration": f"{minutes:.4f}",
                "durationUnit": "min",
                "sourceName": WATCH,
                "sourceVersion": "11.0",
                "device": out.device(WATCH_DEVICE),
                "creationDate": stamp(end_time),
                "startDate": stamp(start),
                "endDate": stamp(end_time),
            }
            stream.write(f" <Workout {attributes(**workout)}>\n{body} </Workout>\n")
            truth[plan.day]["workout_minutes"] = minutes
            if plan.duplicate_workout:
                copy = dict(workout, sourceName="Strava", device=out.device(PHONE_DEVICE))
                copy["startDate"] = stamp(start + timedelta(seconds=30))
                stream.write(f" <Workout {attributes(**copy)}/>\n")
        for plan in plans:
            stream.write(
                f' <ActivitySummary dateComponents="{plan.day}" activeEnergyBurned="420" '
                'activeEnergyBurnedGoal="500" activeEnergyBurnedUnit="Cal" appleMoveTime="0" '
                'appleMoveTimeGoal="0" appleExerciseTime="32" appleExerciseTimeGoal="30" '
                'appleStandHours="12" appleStandHoursGoal="12"/>\n'
            )
        stream.write("</HealthData>\n")
    return truth


def main():
    parser = argparse.ArgumentParser(description="Write a synthetic Apple Health export.xml")
    parser.add_argument("path", type=Path)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 12))
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--hr-interval", type=int, default=10, help="minutes between readings")
    parser.add_argument("--workout-seconds", type=int, default=10)
    parser.add_argument("--rmssd-days", type=int, default=0, help="last nights with RMSSD too")
    args = parser.parse_args()
    write_export(
        args.path,
        args.days,
        args.end,
        args.seed,
        hr_interval=args.hr_interval,
        workout_seconds=args.workout_seconds,
        rmssd_days=args.rmssd_days,
    )
    print(args.path)


if __name__ == "__main__":
    main()
