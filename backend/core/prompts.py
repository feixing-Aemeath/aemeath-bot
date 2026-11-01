"""Prompt templates live in backend/prompts/; the core only fills in named placeholders.

Only `{name}` for the given names is replaced, so templates may contain other braces
(JSON examples, tool arguments) without breaking.
"""

import re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def render(name, **values):
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    # Substitute once so braces in user-written souls, notes or messages stay literal.
    return re.sub(r"\{(\w+)\}", lambda match: str(values.get(match[1], match[0])), text)
