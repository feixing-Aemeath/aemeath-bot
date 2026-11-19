"""Capabilities used by the extraction prompt, shared with the synthetic flow evaluator."""

from core.tokens import task_grant


def extraction_grant(day):
    return task_grant(
        "extract_todos",
        tools={
            "messages_get_unprocessed",
            "messages_search",
            "messages_mark_processed",
            "screen_open_chat",
            "todos_add",
            "todos_list",
            "todos_update",
            "digest_write",
            "memory_read",
            "memory_search",
        },
        plugin_tools={
            ("homework", "homework_mark"),
            ("campus", "campus_event_add"),
            ("campus", "campus_leave_record"),
            ("campus", "campus_list"),
            ("grades", "grades_record"),
        },
        memory_read_paths={"关于我.md", f"每日/{day}/今天.md"},
    )


def goal_chat_grant(reply_id, reader):
    """Goal conversations can only inspect relevant context and their own goal."""
    from dataclasses import replace

    return replace(
        task_grant(
            f"chat:{reply_id}",
            kind="bot",
            tools={"chat_reply", "todos_list"},
            plugin_tools={
                ("goals", name)
                for name in (
                    "goals_get",
                    "goals_propose",
                    "goals_confirm",
                    "goals_record",
                    "goals_revoke",
                )
            }
            | {("timetable", "timetable_days"), ("homework", "homework_list")},
        ),
        reader=reader,
    )
