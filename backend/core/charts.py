"""Charts a plugin draws on request: a preset such as 最近 N 晚的睡眠 or 最近几个月的花销, with a
few parameters, which a Bot names in a chat reply (chat_reply cards: {plugin, chart, params}).
The plugin's own code works out every number; the model only picks the preset and the values.

A plugin's `charts()` returns {name: spec}. A spec has `title` and `description` (what the Bot
reads in chat_cards), `params` mapping each parameter to {"type": "integer", "minimum",
"maximum", "default"} or {"type": "string", "enum": [...], "default"}, each with an optional
"description", and `build`: an async function of the chosen values that returns one card, or
None when there is nothing to draw.
"""

import inspect
import re

NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
SHOWN = ("type", "minimum", "maximum", "enum", "default", "description")


def valid_param(param):
    if not isinstance(param, dict) or not isinstance(param.get("description", ""), str):
        return False
    default = param.get("default")
    if param.get("type") == "integer":
        low, high = param.get("minimum"), param.get("maximum")
        return (
            all(isinstance(v, int) and not isinstance(v, bool) for v in (low, high, default))
            and low <= default <= high
        )
    if param.get("type") == "string":
        choices = param.get("enum")
        return (
            isinstance(choices, list)
            and bool(choices)
            and all(isinstance(c, str) for c in choices)
            and default in choices
        )
    return False


def valid(name, spec):
    """A preset the core can offer: a plain name, words for the Bot, typed parameters with
    defaults inside their range, and an async build."""
    return (
        isinstance(name, str)
        and NAME.fullmatch(name) is not None
        and isinstance(spec, dict)
        and isinstance(spec.get("title"), str)
        and isinstance(spec.get("description"), str)
        and isinstance(spec.get("params", {}), dict)
        and all(NAME.fullmatch(k) and valid_param(p) for k, p in spec.get("params", {}).items())
        and inspect.iscoroutinefunction(spec.get("build"))
    )


def public(name, spec):
    """What chat_cards shows the Bot: the preset without its code."""
    return {
        "chart": name,
        "title": spec["title"],
        "description": spec["description"],
        "params": {
            key: {field: param[field] for field in SHOWN if field in param}
            for key, param in spec.get("params", {}).items()
        },
    }


def values(spec, given):
    """The parameters a reply asked for, each checked, with the defaults for the rest;
    ValueError for an unknown name or a value out of range."""
    params = spec.get("params", {})
    given = {} if given is None else given
    if not isinstance(given, dict) or set(given) - set(params):
        raise ValueError("invalid_chart_params")
    chosen = {}
    for key, param in params.items():
        value = given.get(key, param["default"])
        if param["type"] == "integer":
            ok = (
                isinstance(value, int)
                and not isinstance(value, bool)
                and param["minimum"] <= value <= param["maximum"]
            )
        else:
            ok = value in param["enum"]
        if not ok:
            raise ValueError("invalid_chart_params")
        chosen[key] = value
    return chosen
