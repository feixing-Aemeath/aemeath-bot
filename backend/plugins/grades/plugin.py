"""Course grades, earned credits and GPA.

Grades arrive two ways: the Agent records a released grade it reads in a message
(grades_record, citing the message), or the plugin reads a CSV exported from the school
system (`import_csv`, every hour). Both upsert one row per term and course. The 综测
(comprehensive evaluation) and 体测 (fitness test) scores, and the 综测 needed to graduate,
are recorded by the Agent too (grades_score_record). The numbers on the cards are computed
here, never by the model.
"""

import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from core.db import dump, uid
from core.plugins import Plugin as BasePlugin
from plugins.school.common import arguments_of, choice, key, local, source_refs, text, zone_of

CATEGORIES = ("必修", "选修", "通识", "实践", "其他")
BOT = "成绩Bot"  # the character this plugin shows as in the App
SCALES = {
    "standard4": "标准 4.0",
    "pku4": "北大 4.0 公式",
    "linear5": "5 分制（成绩 − 50）÷ 10",
}
# Grades given as words or letters, as the score they stand for. None: pass/fail, which
# counts towards credits but not towards the GPA.
WORDS = {
    "优秀": 95, "优": 95, "良好": 85, "良": 85, "中等": 75, "中": 75, "及格": 65,
    "不及格": 40, "差": 40, "通过": None, "合格": None, "不通过": 0, "不合格": 0,
    "A+": 98, "A": 95, "A-": 91, "B+": 88, "B": 85, "B-": 81, "C+": 78, "C": 75, "C-": 71,
    "D+": 68, "D": 65, "D-": 61, "F": 40,
}  # fmt: skip
FAILED_WORDS = {"不通过", "不合格"}
COLUMNS = {
    "course": ("课程名称", "课程名", "课程"),
    "credits": ("学分",),
    "score": ("总评成绩", "总成绩", "最终成绩", "总评", "成绩"),
    "term": ("学年学期", "开课学期", "学期"),
    "category": ("课程性质", "课程类别", "类别"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS grades (
  id TEXT PRIMARY KEY, term TEXT NOT NULL, course TEXT NOT NULL, course_key TEXT NOT NULL,
  score REAL, grade TEXT NOT NULL, passed INTEGER NOT NULL, credits REAL, category TEXT,
  origin TEXT NOT NULL, source_refs TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, UNIQUE(term, course_key));
CREATE TABLE IF NOT EXISTS grade_scores (
  kind TEXT PRIMARY KEY, score REAL, full REAL, required REAL, source_refs TEXT NOT NULL,
  updated_at TEXT NOT NULL);
"""
# grade_scores.kind: the 综合测评 score, the 体质测试 score, and the credits graduating needs.
KINDS = ("综测", "体测", "学分")


def parse_score(value):
    """(score for the GPA or None, grade as shown, passed)."""
    if isinstance(value, bool):
        raise ValueError("score must be a number 0-100 or a grade such as 良好, 通过 or B+")
    if isinstance(value, int | float):
        number = float(value)
        if not 0 <= number <= 100:
            raise ValueError("score must be 0-100")
        return number, f"{number:g}", number >= 60
    if isinstance(value, str):
        word = value.strip().upper() if value.strip().isascii() else value.strip()
        try:
            number = float(word)
        except ValueError:
            number = None
        if number is not None:
            return parse_score(number)
        if word in WORDS:
            score = WORDS[word]
            passed = word not in FAILED_WORDS and (score is None or score >= 60)
            return score, value.strip(), passed
    raise ValueError("score must be a number 0-100 or a grade such as 良好, 通过 or B+")


def points(score, scale):
    """Grade points of one course, by the configured scale; below 60 is 0."""
    if score is None or score < 60:
        return 0.0
    if scale == "pku4":
        return 4 - 3 * (100 - score) ** 2 / 1600
    if scale == "linear5":
        return (score - 50) / 10
    return 4.0 if score >= 90 else 3.0 if score >= 80 else 2.0 if score >= 70 else 1.0


def current_term(moment):
    """The academic term a date falls in: 2026-2027-1 from September, 2026-2027-2 from
    February."""
    year, month = moment.year, moment.month
    if month >= 9:
        return f"{year}-{year + 1}-1"
    if month == 1:
        return f"{year - 1}-{year}-1"
    return f"{year - 1}-{year}-2"


def term_label(term):
    """2026-2027-1 -> 2026秋, 2026-2027-2 -> 2027春; other spellings stay as they are."""
    parts = term.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts) and parts[2] in {"1", "2"}:
        return f"{parts[0]}秋" if parts[2] == "1" else f"{parts[1]}春"
    return term


def gpa(rows, scale):
    graded = [r for r in rows if r["score"] is not None]
    if not graded:
        return None
    weights = [r["credits"] or 1 for r in graded]
    total = sum(w * points(r["score"], scale) for w, r in zip(weights, graded, strict=True))
    return total / sum(weights)


def amount(arguments, key):
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1000:
        raise ValueError(f"{key} must be a number 0-1000")
    return float(value)


def bar(title, shown, value, full, subtitle):
    """A row of 学业进度: the gray track is `full`, the colored part `value` (docs/api.md)."""
    return {
        "title": title,
        "subtitle": subtitle,
        "detail": f"{shown} / {full:g}",
        "progress": {"value": round(min(value or 0, full), 2), "max": full},
    }


class Plugin(BasePlugin):
    async def setup(self, context, config):
        await super().setup(context, config)
        self.db = context.store.db
        with self.db.connect() as conn:
            conn.executescript(SCHEMA)
        self.scale = config.get("gpa_scale", "standard4")
        self.required = config.get("required_credits")
        # Defaults until the Agent records the school's own numbers.
        self.defaults = {
            "综测": {"full": config.get("comprehensive_full", 100),
                     "required": config.get("comprehensive_required")},
            "体测": {"full": config.get("fitness_full", 100), "required": None},
            "学分": {"full": None, "required": self.required},
        }  # fmt: skip
        self.csv = Path(config["import_csv"]) if config.get("import_csv") else None

    def now(self):
        return datetime.now(zone_of(self.context))

    def jobs(self):
        if not self.csv:
            return {}
        return {"import": {"cron": "7 * * * *", "callback": self.import_csv}}

    async def import_csv(self):
        await asyncio.to_thread(self._import, self.csv)

    def _import(self, path):
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                content = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("grades_csv_encoding")
        reader = csv.DictReader(content.splitlines())
        headers = {name.strip(): name for name in reader.fieldnames or []}
        column = {}
        for field, names in COLUMNS.items():
            column[field] = next((headers[n] for n in names if n in headers), None)
        if not column["course"] or not column["score"]:
            raise ValueError("grades_csv_columns")
        count = 0
        for line in reader:
            course = (line.get(column["course"]) or "").strip()
            score = (line.get(column["score"]) or "").strip()
            if not course or not score:
                continue
            credits = (line.get(column["credits"]) or "").strip() if column["credits"] else ""
            category = (line.get(column["category"]) or "").strip() if column["category"] else ""
            self._upsert(
                course=course[:80],
                score=score,
                credits=float(credits) if credits else None,
                term=((line.get(column["term"]) or "").strip() if column["term"] else "")
                or current_term(self.now()),
                category=next((c for c in CATEGORIES if c in category), None) if category else None,
                origin="import",
                refs=[],
            )
            count += 1
        return count

    def _upsert(self, course, score, credits, term, category, origin, refs):
        number, grade, passed = parse_score(score)
        timestamp = datetime.now(UTC).isoformat()
        course_key = key(course)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM grades WHERE term=? AND course_key=?", (term, course_key)
            ).fetchone()
            if existing:
                merged = list(dict.fromkeys(json.loads(existing["source_refs"]) + refs))
                conn.execute(
                    "UPDATE grades SET course=?, score=?, grade=?, passed=?, "
                    "credits=COALESCE(?, credits), category=COALESCE(?, category), origin=?, "
                    "source_refs=?, updated_at=? WHERE id=?",
                    (
                        course,
                        number,
                        grade,
                        int(passed),
                        credits,
                        category,
                        origin,
                        dump(merged),
                        timestamp,
                        existing["id"],
                    ),
                )
                identifier, disposition = existing["id"], "updated"
            else:
                identifier, disposition = uid(), "added"
                conn.execute(
                    "INSERT INTO grades VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        term,
                        course,
                        course_key,
                        number,
                        grade,
                        int(passed),
                        credits,
                        category,
                        origin,
                        dump(refs),
                        timestamp,
                        timestamp,
                    ),
                )
            row = conn.execute("SELECT * FROM grades WHERE id=?", (identifier,)).fetchone()
        return {"disposition": disposition, "grade": view(row)}

    def mcp_tools(self):
        return {
            "grades_record": self.grades_record,
            "grades_list": self.grades_list,
            "grades_score_record": self.grades_score_record,
        }

    async def grades_record(self, arguments):
        """Record a released course grade the user received, e.g. from a teacher's or the
        school system's message. One row per term and course; recording again updates it.
        arguments: {"course": "离散数学", "score": 88 or "良好" / "通过" / "B+", "credits": 3
        (optional), "term": "2026-2027-1" (optional, default the current term), "category":
        "必修|选修|通识|实践|其他" (optional), "source_refs": [message IDs]}."""
        allowed = {"course", "score", "credits", "term", "category", "source_refs"}
        arguments_of(arguments, allowed, required=("course", "score", "source_refs"))
        credits = arguments.get("credits")
        if credits is not None and (
            isinstance(credits, bool)
            or not isinstance(credits, int | float)
            or not 0 < credits <= 30
        ):
            raise ValueError("credits must be a number 0-30")
        refs = source_refs(self.db, arguments)
        return await asyncio.to_thread(
            self._upsert,
            course=text(arguments, "course", 80),
            score=arguments["score"],
            credits=float(credits) if credits is not None else None,
            term=text(arguments, "term", 40) or current_term(self.now()),
            category=choice(arguments, "category", CATEGORIES),
            origin="agent",
            refs=refs,
        )

    async def grades_list(self, arguments):
        """Recorded grades with credits and grade points, and the totals shown on the cards
        (earned credits, credit-weighted GPA on the configured scale). arguments: {"term":
        "2026-2027-1" (optional, default all terms)}."""
        arguments_of(arguments, {"term"})
        term = text(arguments, "term", 40)
        rows = await asyncio.to_thread(self.rows)
        if term:
            rows = [r for r in rows if r["term"] == term]
        value = gpa(rows, self.scale)
        return {
            "items": [{**r, "points": round(points(r["score"], self.scale), 2)} for r in rows],
            "earned_credits": sum(r["credits"] or 0 for r in rows if r["passed"]),
            "required_credits": (await asyncio.to_thread(self.scores))["学分"]["required"],
            "gpa": round(value, 2) if value is not None else None,
            "gpa_scale": SCALES[self.scale],
            "scores": await asyncio.to_thread(self.scores),
        }

    async def grades_score_record(self, arguments):
        """Record the user's 综测 (综合测评, comprehensive evaluation) or 体测 (体质测试, fitness
        test) score, the 综测 score needed to graduate, or the credits needed to graduate (kind
        学分; the earned credits come from the grades), read in a message or told by the user.
        Fields left out keep their values. arguments: {"kind": "综测|体测|学分", "score": 78
        (optional, 综测 and 体测), "full": 100 (optional, the full mark, 综测 and 体测),
        "required": 60 (optional, 综测 and 学分: what graduating needs), "source_refs":
        [message IDs] (optional when the user said it in the chat)}."""
        allowed = {"kind", "score", "full", "required", "source_refs"}
        arguments_of(arguments, allowed, required=("kind",))
        kind = choice(arguments, "kind", KINDS)
        given = {name: amount(arguments, name) for name in ("score", "full", "required")}
        if kind == "体测" and given["required"] is not None:
            raise ValueError("required is only for 综测 and 学分")
        if kind == "学分" and (given["score"] is not None or given["full"] is not None):
            raise ValueError("学分 takes only required; earned credits come from the grades")
        if all(value is None for value in given.values()):
            raise ValueError("give score, full or required")
        refs = source_refs(self.db, arguments, required=False)
        return await asyncio.to_thread(self._record_score, kind, given, refs)

    def _record_score(self, kind, given, refs):
        [old] = self.db.rows("SELECT * FROM grade_scores WHERE kind = ?", (kind,)) or [None]
        merged = {name: given[name] if given[name] is not None else (old[name] if old else None)
                  for name in ("score", "full", "required")}  # fmt: skip
        known = json.loads(old["source_refs"]) if old else []
        with self.db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO grade_scores (kind, score, full, required, source_refs,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, merged["score"], merged["full"], merged["required"],
                 dump(list(dict.fromkeys(known + refs))), datetime.now(UTC).isoformat()),
            )  # fmt: skip
        return {"disposition": "updated" if old else "added", "score": self.scores()[kind]}

    def scores(self):
        """综测 and 体测 as {score, full, required}, the config's defaults where none recorded."""
        stored = {r["kind"]: r for r in self.db.rows("SELECT * FROM grade_scores")}
        result = {}
        for kind, default in self.defaults.items():
            row = stored.get(kind)
            result[kind] = {
                "score": row["score"] if row else None,
                "full": (row["full"] if row and row["full"] else None) or default["full"],
                "required": (row["required"] if row else None) or default["required"],
            }
        return result

    def rows(self):
        return [view(r) for r in self.db.rows("SELECT * FROM grades ORDER BY term, course")]

    async def cards(self):
        return await asyncio.to_thread(self._cards, self.now())

    async def daily(self, day):
        """成绩.md in the day's folder (core/daily.py): grades recorded that day, and totals."""
        return await asyncio.to_thread(self._daily, day, self.now().tzinfo)

    def _daily(self, day, zone):
        rows = self.rows()
        new = [r for r in rows if local(r["updated_at"], zone).date() == day]
        credits = sum(r["credits"] for r in rows if r["passed"] and r["credits"])
        lines = []
        if new:
            lines.append("## 这一天出的成绩")
            for row in new:
                extra = f"，{row['credits']:g} 学分" if row["credits"] else ""
                lines.append(
                    f"- {row['course']}：{row['grade']}（{term_label(row['term'])}{extra}）"
                )
        if rows:
            average = gpa(rows, self.scale)
            lines += [
                "",
                f"已修学分 {credits:g}" + (f"，平均绩点 {average:.2f}" if average else ""),
            ]
        if new:
            summary = f"这一天出了 {len(new)} 门成绩"
        else:
            summary = f"没有新成绩，已修 {credits:g} 学分" if rows else "还没有成绩"
        return {
            "file": "成绩.md",
            "bot": BOT,
            "summary": summary,
            "body": "\n".join(lines).strip() or "还没有成绩。",
        }

    def _cards(self, now):
        rows = self.rows()
        cards = [self.progress_card(rows)]
        if not rows:
            return cards
        terms = sorted({r["term"] for r in rows})
        latest = terms[-1]

        recent = sorted(
            (r for r in rows if r["term"] == latest), key=lambda r: r["updated_at"], reverse=True
        )
        cards.append(
            {
                "type": "list",
                "title": f"{term_label(latest)}已出成绩" if len(terms) > 1 else "已出成绩",
                "data": {
                    "items": [
                        {
                            "title": r["course"],
                            **({"subtitle": subtitle} if (subtitle := describe(r)) else {}),
                            "detail": r["grade"] if r["passed"] else f"{r['grade']}（未通过）",
                        }
                        for r in recent[:12]
                    ]
                },
            }
        )
        per_term = [
            (term, value)
            for term in terms
            if (value := gpa([r for r in rows if r["term"] == term], self.scale)) is not None
        ]
        if len(per_term) > 1:
            cards.append(
                {
                    "type": "chart",
                    "title": "各学期绩点",
                    "data": {
                        "kind": "bar",
                        "unit": "GPA",
                        "points": [{"x": term_label(t), "y": round(v, 2)} for t, v in per_term],
                    },
                }
            )
        return cards

    def progress_card(self, rows):
        """学业进度: the GPA, the 综测 against what graduating needs, the 体测 against full marks
        and the earned credits against what graduating needs. Each row is a ring (a bar on
        older Apps) whose gray track is the full mark and whose color is the score so far."""
        items = []
        top = 5.0 if self.scale == "linear5" else 4.0
        overall = gpa(rows, self.scale)
        if overall is None:
            note = "还没考过试，第一次期末出分后算"
        else:
            note = f"{SCALES[self.scale]} · 按学分加权"
            terms = [t for t in sorted({r["term"] for r in rows})
                     if gpa([r for r in rows if r["term"] == t], self.scale) is not None]  # fmt: skip
            if len(terms) > 1:
                before = gpa([r for r in rows if r["term"] == terms[-2]], self.scale)
                note += f" · 上学期 {before:.2f}"
        shown = "—" if overall is None else f"{overall:.2f}"
        items.append(bar("平均绩点", shown, overall, top, note))

        scores = self.scores()
        comprehensive = scores["综测"]
        score, required = comprehensive["score"], comprehensive["required"]
        if required is None:
            note = "毕业要多少分还没记下"
        elif score is None:
            note = f"毕业要 {required:g} 分，还没出分"
        elif score >= required:
            note = f"毕业要 {required:g} 分，已经够了"
        else:
            note = f"毕业要 {required:g} 分，还差 {required - score:g}"
        shown = "—" if score is None else f"{score:g}"
        items.append(bar("综测", shown, score, comprehensive["full"], note))

        fitness = scores["体测"]
        score, full = fitness["score"], fitness["full"]
        if score is None:
            note = "还没测"
        else:
            note = "满分" if score >= full else f"离满分差 {full - score:g}"
        items.append(bar("体测", "—" if score is None else f"{score:g}", score, full, note))

        credits = sum(r["credits"] for r in rows if r["passed"] and r["credits"])
        required = scores["学分"]["required"]
        if required:
            note = f"毕业要 {required:g}，还差 {max(required - credits, 0):g}"
            items.append(bar("已修学分", f"{credits:g}", credits, required, note))
        else:
            # No full mark to draw against yet: the track stays gray.
            items.append(
                {
                    "title": "已修学分",
                    "subtitle": "毕业要多少学分还没记下",
                    "detail": f"{credits:g} 学分",
                    "progress": {"value": 0, "max": 0},
                }
            )
        return {"type": "list", "title": "学业进度", "data": {"style": "rings", "items": items}}


def describe(row):
    parts = []
    if row["credits"]:
        parts.append(f"{row['credits']:g} 学分")
    if row["category"]:
        parts.append(row["category"])
    return " · ".join(parts)


def view(row):
    item = dict(row)
    item["source_refs"] = json.loads(item["source_refs"])
    item["passed"] = bool(item["passed"])
    item.pop("course_key")
    return item
