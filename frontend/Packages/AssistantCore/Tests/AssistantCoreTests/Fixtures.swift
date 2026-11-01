import Foundation

/// Responses recorded from the real backend (backend/core/api.py) with synthetic data.
enum Fixtures {
    static let todo = """
        {"title": "完成信息核对", "notes": "", "due_at": "2026-10-16T09:00:00Z", "has_time": true,
         "source_plugin": "qq_screen", "source_refs": ["a22a2833-b640-4ccb-8875-3ce01613f72d"],
         "source_excerpt": "请在10月16日17:00前完成信息核对", "confidence": 0.93,
         "created_by": "agent", "id": "ab0904cc-b9d3-4732-a6aa-534f255a6722",
         "status": "pending",
         "dedup_key": "4094bdb6dd912094feb880de550755e88343f5140aa75e4dc63de5538ca97699",
         "reminder_id": null, "calendar_event_id": null,
         "created_at": "2026-10-15T16:54:37.446276Z", "updated_at": "2026-10-15T16:54:37.446276Z",
         "sync_status": "pending", "sync_error": null}
        """

    static let todoWithoutDue = """
        {"title": "交实验报告", "notes": "", "due_at": null, "has_time": false,
         "source_plugin": "qq_screen,feishu", "source_refs": ["m1", "m2"],
         "source_excerpt": "信息核对", "confidence": 0.9, "created_by": "agent", "id": "t2",
         "status": "completed", "dedup_key": "k2", "reminder_id": "fake-reminders-t2",
         "calendar_event_id": null, "created_at": "2026-10-15T16:54:37.448001Z",
         "updated_at": "2026-10-15T16:54:38Z", "sync_status": "synced", "sync_error": null}
        """

    static let todoPage = "{\"items\": [\(todo), \(todoWithoutDue)], \"total\": 2}"

    static let status = """
        {"online": true, "platform": "linux", "apple_mode": "fake",
         "agents": {"claude_code": {"available": true, "authentication": "unchecked"},
                    "codex": {"available": false, "authentication": "unchecked"}},
         "last_run": {"id": "91a99011-1063-4f38-adef-13b5db6ca51f", "job": "extract_todos",
                      "harness": "claude_code", "started_at": "2026-10-15T16:54:37.453973Z",
                      "finished_at": "2026-10-15T16:54:37.455389Z", "status": "failed",
                      "error": "quota_exhausted", "attempt": 1},
         "pending_sync": 2}
        """

    static let today = """
        {"date": "2026-10-16", "summary": "今天暂无摘要。", "todos": [\(todo)],
         "cards": [{"type": "text", "title": "QQ 屏幕读取", "data": {"text": "只预览会话列表"}},
                   {"type": "sparkline", "title": "未来的卡片", "data": {}}]}
        """

    static let digests = """
        {"items": [{"id": "21db0e44-4e79-4efb-a576-7e396bf5916f", "date": "2026-10-15",
                    "plugin": "qq_screen", "summary": "今天班级群有一条信息核对通知。",
                    "created_at": "2026-10-15T16:54:37.451981Z"}], "total": 1}
        """

    static let runs = """
        {"items": [{"id": "r1", "job": "fetch:feishu", "harness": null,
                    "started_at": "2026-10-15T16:54:37.453973Z", "finished_at": null,
                    "status": "running", "error": null, "attempt": 2}], "total": 1}
        """

    static let settings = """
        {"timezone": "Asia/Shanghai", "source_cron": "*/30 * * * *",
         "extraction_cron": "0 7-23/3 * * *", "confidence_threshold": 0.8,
         "harness_order": ["claude_code", "codex"], "job_harness": {}, "agent_timeout": 300,
         "retry_attempts": 3, "retry_base_seconds": 2.0}
        """

    static let plugins = "[\(plugin)]"

    static let plugin = """
        {"id": "feishu", "name": "飞书", "version": "0.1.0", "icon": "message",
          "description": "官方 SDK，以用户身份增量读取白名单会话", "platforms": ["linux", "macos"],
          "permissions": ["network"], "schedule": null, "provides": ["source", "cards"],
          "config_schema": {"type": "object", "additionalProperties": false,
            "properties": {"whitelist": {"type": "array", "uniqueItems": true,
              "items": {"type": "string", "minLength": 1}, "default": []},
              "initial_lookback_hours": {"type": "integer", "minimum": 1, "maximum": 720,
                                         "default": 24}}},
          "mascot": {"sticker": null, "accent": "#22B8CF"},
          "enabled": false, "config": {}, "last_run_at": null, "state": "disabled",
          "error": null}
        """

    static let todoWithSources = """
        {"title": "提交实验报告", "notes": "", "due_at": "2026-10-16T10:00:00Z", "has_time": true,
         "source_plugin": "qq_screen", "source_refs": ["e87d5be1-6277-4a4a-9fb9-4a88642399a2"],
         "source_excerpt": "周五18点前提交实验报告", "confidence": 0.93, "created_by": "agent",
         "sources": [{"message_id": "e87d5be1-6277-4a4a-9fb9-4a88642399a2",
           "plugin": "qq_screen", "chat_name": "软件工程2班", "sender": "李老师",
           "sent_at": "2026-10-15T12:41:00Z"}],
         "id": "0259cf70-db24-4ef5-9693-21d5fbc2bd0c", "status": "pending",
         "dedup_key": "5eaf728f3b7cb2edb41f84c31cc6fb53e059222b21538e900091727e4f1c858b",
         "reminder_id": null, "calendar_event_id": null,
         "created_at": "2026-10-15T19:05:45.157751Z", "updated_at": "2026-10-15T19:05:45.157751Z",
         "sync_status": "pending", "sync_error": null}
        """

    static let healthReports = """
        {"items": [{"id": "r1", "date": "2026-10-15", "kind": "morning", "status": "ready",
           "title": "睡得不错，今天可以正常训练",
           "body": "昨晚睡了 **7小时12分**，比 7 天基线多 18 分钟。\\n\\n- 静息心率 58\\n- 建议：正常训练",
           "error": null, "harness": "claude_code",
           "created_at": "2026-10-15T19:05:45.160741Z", "updated_at": "2026-10-15T19:05:45.160752Z"},
          {"id": "r2", "date": "2026-10-14", "kind": "morning", "status": "failed", "title": null,
           "body": null, "error": "report_not_written", "harness": null,
           "created_at": "2026-10-15T19:05:45.162977Z", "updated_at": "2026-10-15T19:05:45.162991Z"}],
         "total": 2}
        """

    /// GET /api/v1/plugins/health/cards for the synthetic history in backend/tests
    /// (plugins/health/cards.py on scripts/health_synthetic.py data with --rmssd-days 30,
    /// before any briefing).
    static let healthCards = """
        [
         {"type": "list", "title": "健康预警", "data": {"items": [{"title": "静息心率高于基线", "subtitle": "静息心率已连续 3 天明显高于本人近 30 天基线（今日 64.7 bpm，基线中位数 56.5 bpm），建议关注。", "detail": "连续 3 天"}, {"title": "HRV 低于基线", "subtitle": "HRV（ln RMSSD）已连续 3 天明显低于本人近 30 天基线（今日 RMSSD 39 ms，基线中位数 RMSSD 60 ms），建议关注。", "detail": "连续 3 天"}], "count": 2}},
         {"type": "stat", "title": "今日准备度", "data": {"value": 4.5, "unit": "/ 10", "label": "夜间 HRV 0.0 · 静息心率 0.0 · 睡眠 10.0 · 训练负荷 10.0", "delta": "昨天 4.5"}},
         {"type": "stat", "title": "昨晚睡眠", "data": {"value": "7小时29分", "label": "23:11 入睡 · 06:50 醒来", "delta": "比近 30 天中位数少 18分", "trend": "flat"}},
         {"type": "chart", "title": "最近 7 晚睡眠分期", "data": {"kind": "bar", "unit": "小时", "series": [{"name": "深睡", "points": [{"x": "10/6", "y": 1.37}, {"x": "10/7", "y": 1.4}, {"x": "10/8", "y": 1.47}, {"x": "10/9", "y": 1.38}, {"x": "10/10", "y": 1.48}, {"x": "10/11", "y": 1.35}, {"x": "10/12", "y": 1.47}]}, {"name": "核心", "points": [{"x": "10/6", "y": 3.95}, {"x": "10/7", "y": 4.08}, {"x": "10/8", "y": 3.92}, {"x": "10/9", "y": 3.92}, {"x": "10/10", "y": 3.88}, {"x": "10/11", "y": 4.07}, {"x": "10/12", "y": 3.92}]}, {"name": "REM", "points": [{"x": "10/6", "y": 2.12}, {"x": "10/7", "y": 2.75}, {"x": "10/8", "y": 2.08}, {"x": "10/9", "y": 2.45}, {"x": "10/10", "y": 2.43}, {"x": "10/11", "y": 1.97}, {"x": "10/12", "y": 2.1}]}, {"name": "清醒", "points": [{"x": "10/6", "y": 0.33}, {"x": "10/7", "y": 0.43}, {"x": "10/8", "y": 0.33}, {"x": "10/9", "y": 0.4}, {"x": "10/10", "y": 0.33}, {"x": "10/11", "y": 0.37}, {"x": "10/12", "y": 0.33}]}]}},
         {"type": "stat", "title": "作息规律", "data": {"value": 90.4, "unit": "SRI", "label": "近 7 天睡眠规律性指数，越高越规律 · 入睡时间波动 ±11 分钟 · 醒来时间波动 ±14 分钟", "delta": "近 30 天 90.4"}},
         {"type": "stat", "title": "静息心率", "data": {"value": 64.7, "unit": "次/分", "label": "今天 · 近 30 天中位数 56.5 次/分", "delta": "+8.2", "trend": "up"}},
         {"type": "stat", "title": "HRV", "data": {"value": 39, "unit": "毫秒（RMSSD）", "label": "今天 · 近 30 天中位数 60 毫秒 · 夜间测量", "delta": "-35%", "trend": "down"}},
         {"type": "stat", "title": "训练负荷", "data": {"value": 315, "unit": "TRIMP（近 7 天）", "label": "近 28 天平均每周 357 · 急慢性负荷比 0.88（仅供参考）"}}
        ]
        """

    static let jobAccepted = #"{"job_id": "memory_index", "status": "accepted"}"#
    static let healthAccepted = #"{"accepted": 1, "duplicates": 0}"#
}
