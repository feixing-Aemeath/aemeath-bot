"""Streaming reader for Apple Health exports (export.xml, or export.zip without unpacking).

The file can exceed 1 GB, so it is read with iterparse and every top-level element is cleared
after use; memory stays flat however long the history is. <Me> (date of birth, sex, blood type)
is never read, and nothing but the observation rows below leaves this module.
"""

import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from plugins.health.observations import (
    MAX_SPAN,
    QUANTITY_TYPES,
    SLEEP_TYPE,
    SLEEP_VALUES,
    STAGE_CODES,
    WORKOUT_PREFIX,
    convert,
    device_class,
    parse_time,
)

EXPORT_NAMES = ("export.xml", "导出.xml")  # Chinese-language iPhones localize the file name


def pick_member(names):
    """The main XML inside an export zip (not export_cda.xml, routes or ECG files)."""
    candidates = [
        name
        for name in names
        if name.lower().endswith(".xml") and not name.lower().endswith("_cda.xml")
    ]
    for name in candidates:
        if PurePosixPath(name).name in EXPORT_NAMES:
            return name
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("export.xml not found in archive")


class ExportReader:
    def __init__(self, source):
        self.source = source
        self.export_date = None
        self.counts = Counter()
        self._classes = {}
        self._models = {}  # source name -> class shown by an HKDevice model

    @contextmanager
    def _open(self):
        if hasattr(self.source, "read"):
            yield self.source
            return
        path = Path(self.source)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                with archive.open(pick_member(archive.namelist())) as stream:
                    yield stream
        else:
            with path.open("rb") as stream:
                yield stream

    def _device(self, source_name, device):
        # The HKDevice text starts with a per-object address; cache on the stable remainder.
        key = (source_name, device[device.find(">,") :] if device else "")
        if key not in self._classes:
            self._classes[key] = device_class(source_name, device)
        found = self._classes[key]
        if device and "model:" in device:
            self._models.setdefault(source_name, found)
            return found
        # Sleep and resting heart rate carry no HKDevice, so a watch whose name lacks "Watch"
        # (the user can name it anything) would count as "other". Its heart-rate readings come
        # first in an export and name the model; records without one take that class.
        return self._models.get(source_name, found)

    def rows(self):
        """Yield (kind, start, end, value, label, source, device_class) rows."""
        with self._open() as stream:
            depth, root = 0, None
            for event, element in ET.iterparse(stream, events=("start", "end")):
                if event == "start":
                    depth += 1
                    if depth == 1:
                        root = element
                    continue
                depth -= 1
                if depth != 1:
                    continue  # children are read through their top-level parent
                tag = element.tag
                if tag == "Record":
                    yield from self._record(element.attrib)
                elif tag == "Workout":
                    yield from self._workout(element)
                elif tag == "ExportDate":
                    self.export_date = element.get("value")
                else:
                    self.counts["skipped_elements"] += 1  # Me, ActivitySummary, ClinicalRecord…
                root.clear()

    def _record(self, attributes):
        self.counts["records"] += 1
        record_type = attributes.get("type")
        if record_type == SLEEP_TYPE:
            kind, label = "sleep", SLEEP_VALUES.get(attributes.get("value"))
        else:
            kind, label = QUANTITY_TYPES.get(record_type), ""
        if kind is None or label is None:
            self.counts["unused_records"] += 1
            return
        try:
            start = parse_time(attributes["startDate"])
            end = parse_time(attributes["endDate"])
            if kind == "sleep":
                value = float(STAGE_CODES[label])
            else:
                value = convert(kind, float(attributes["value"]), attributes.get("unit"))
        except (KeyError, ValueError):
            value = None
        if value is None or not 0 <= end - start <= MAX_SPAN:
            self.counts["invalid_records"] += 1
            return
        source = attributes.get("sourceName") or "unknown"
        self.counts["used_records"] += 1
        yield kind, start, end, value, label, source, self._device(source, attributes.get("device"))

    def _workout(self, element):
        self.counts["workouts"] += 1
        attributes = element.attrib
        try:
            start = parse_time(attributes["startDate"])
            end = parse_time(attributes["endDate"])
            duration = attributes.get("duration")
            minutes = convert(
                "workout",
                float(duration) if duration else (end - start) / 60,
                attributes.get("durationUnit", "min") if duration else "min",
            )
        except (KeyError, ValueError):
            minutes = None
        if minutes is None or end < start:
            self.counts["invalid_workouts"] += 1
            return
        source = attributes.get("sourceName") or "unknown"
        device = self._device(source, attributes.get("device"))
        activity = attributes.get("workoutActivityType", "")
        activity = activity.removeprefix(WORKOUT_PREFIX) or "Other"
        yield "workout", start, end, minutes, activity, source, device
        # iOS 16+ exports summarize workout heart rate; used only when no readings exist.
        for statistic in element.iterfind("WorkoutStatistics"):
            if statistic.get("type") != "HKQuantityTypeIdentifierHeartRate":
                continue
            for field, kind in (("average", "workout_hr_avg"), ("maximum", "workout_hr_max")):
                try:
                    value = convert(kind, float(statistic.get(field)), statistic.get("unit"))
                except (TypeError, ValueError):
                    value = None
                if value is not None:
                    yield kind, start, end, value, "", source, device
