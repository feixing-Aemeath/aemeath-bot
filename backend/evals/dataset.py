"""Load and validate an evaluation dataset (JSONL samples + meta.json)."""

import json
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT = ROOT / "extract_todos"


def parse_due(value):
    """Labels use "YYYY-MM-DD" for a date without a time and full ISO 8601 for a time."""
    if value is None:
        return None
    if len(value) == 10:
        return date.fromisoformat(value)
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"due_at needs a time zone: {value}")
    return moment


def load(directory=DEFAULT):
    directory = Path(directory)
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    samples = [
        json.loads(line)
        for line in (directory / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    validate(samples, meta)
    return samples, meta


def validate(samples, meta):
    """Structural checks; raises ValueError with the first problem found."""
    reference = datetime.fromisoformat(meta["reference_now"])
    by_id = {}
    for sample in samples:
        identifier = sample["id"]
        if identifier in by_id:
            raise ValueError(f"duplicate sample id {identifier}")
        by_id[identifier] = sample
        if sample["plugin"] not in {"qq_screen", "feishu"}:
            raise ValueError(f"{identifier}: unknown plugin")
        sent = datetime.fromisoformat(sample["sent_at"])
        if sent.tzinfo is None or sent > reference:
            raise ValueError(f"{identifier}: sent_at must be aware and before reference_now")
        if not sample["text"].strip():
            raise ValueError(f"{identifier}: empty text")
        label = sample["label"]
        if label["is_todo"]:
            if not label["title"]:
                raise ValueError(f"{identifier}: a todo needs a title")
            parse_due(label["due_at"])
        elif label["title"] or label["due_at"]:
            raise ValueError(f"{identifier}: a non-todo has no title or due_at")
    for sample in samples:
        label = sample["label"]
        for other_id in label["duplicates"]:
            other = by_id.get(other_id)
            if other is None or other_id == sample["id"]:
                raise ValueError(f"{sample['id']}: unknown duplicate {other_id}")
            if sample["id"] not in other["label"]["duplicates"]:
                raise ValueError(f"{sample['id']}/{other_id}: duplicates must be symmetric")
            for key in ("is_todo", "title", "due_at"):
                if other["label"][key] != label[key]:
                    raise ValueError(f"{sample['id']}/{other_id}: duplicates disagree on {key}")
    return True


def clusters(samples):
    """Gold todo clusters: sample id -> frozenset of the ids describing the same todo."""
    result = {}
    for sample in samples:
        if sample["label"]["is_todo"]:
            result[sample["id"]] = frozenset([sample["id"], *sample["label"]["duplicates"]])
    return result
