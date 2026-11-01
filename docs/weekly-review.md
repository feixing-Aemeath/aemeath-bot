# 本周回顾

插件 `weekly_review` 把这一周的数字整理好，再交给 Agent 写成回顾：做得好的、没做完要补的、下周的截止与考试，以及一两条具体建议。贴图是 `aemeath`，卡片归助理Bot；每次从 `Bots/助理/` 读取人设和记忆，事实和数字照实说。所有平台都能启用，默认停用。

实现在 `backend/plugins/weekly_review/`，提示词是 `backend/prompts/weekly_review.md`。不新增数据表，不读取消息、截图或原始健康采样。

## 启用与运行

用现有 `PATCH /api/v1/plugins/weekly_review` 启用，例如：

```json
{"enabled": true, "config": {"review_agent": "deepseek", "review_cron": "0 21 * * 0"}}
```

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| review_agent | deepseek | 可选 deepseek / claude_code / codex；失败时由核心按配置顺序换其他 Agent |
| review_cron | `0 21 * * 0` | 本地时区每周日 21:00。普通 crontab 星期：0、7 是周日，1 是周一；支持列表、范围、步长，也可用 mon / sun 等名字 |

任务 ID 是 `plugin:weekly_review:review`，可通过现有作业接口立即运行。每次回顾当前本地日期所在的一周，允许重跑覆盖。星期字段在交给 APScheduler 前转成名字，避免它把数字 0 当成周一。

Agent 调用 `weekly_review_input` 取数，用 `weekly_review_write` 写回。插件只调用核心 `Jobs.run_agent`，不自己启动 CLI。写回先写周报和搜索索引，再保存插件 KV 中的结果与写入版本；`succeeded` 必须看到本次的新版本，旧周报不能让没写回的重跑成功。失败、回退与重试沿用核心机制。

## 数字的口径

一周按后端 `RuntimeSettings.timezone` 的周一 00:00 到下一周一 00:00（不含），完整覆盖周日 23:59。周日晚生成时统计截至运行时刻，之后新发生的事要重跑才补进来。`date` 指整周，不是滚动的最近七天。

| 项目 | 输入与来源 |
| --- | --- |
| todos | 已完成数 `completed`、未完成标题与截止 `pending`、逾期标题 `overdue`。来自现有 todos 表，排除删除项；包括作业，不能与作业数相加 |
| homework | 通过已启用 homework 的公开 `homework()` 取同一份清单，沿用标记、课程群和关键词规则；勾成 completed 视为已交 |
| timetable | 用 `timetable_days` 取本周七天，只数「课程」，自己的日程不计；每天保留数量和 `estimated_from`，推测的周必须说明 |
| activities | 用 `campus_list` 取这周开始或报名截止的活动标题，放在 `recorded_titles`；**现有表只存通知，没有用户已报名或已参加状态**，`attended_or_registered` 返回「没有数据」，不能据通知声称参加过 |
| health | 调用与 Agent 相同的 `health_get_daily`、`health_get_sleep`，取七天的准备度和睡眠汇总，不重新实现健康算法 |
| tokens | 复用 token_usage 的公开 `Ledger`，汇总本周有日期的助手与个人 CLI 用量：输入（含缓存）加输出，不重复加缓存。分别返回 `assistant`、`own`、`total`，不拆分没有日期的手填累计值 |
| next_week | 下周周一到周日未完成待办的标题、截止、是否有时刻；作业另列。`exams` 筛出其中标题含「考试、期中、期末、小测、测验」的条目，是标题线索，不是另建的考试日程 |

完成数按 `status=completed` 且 `updated_at` 落在这一周统计：现有表没有独立完成时间，修改旧完成项可能把它计入；重新打开的项目也无法恢复先前完成历史。历史周重算使用当前状态，不是历史快照。未完成项包含此前积累和已知后续的；逾期按生成时刻（历史周按周末）判断，没有时刻的截止日算到当天结束。

平均睡眠 `average_sleep_minutes` 只纳入 `quality=ok` 的整日总睡眠（含小睡），保留有效天数 `sleep_days`；部分或缺失记录不当作零。准备度变化 `readiness_change` 是最后一个有效分数减去第一个，同时给出两个日期；少于两天返回「没有数据」。准备度是已有健康插件的工程估计，不是诊断。

插件停用、从未启用、没有数据或读取失败时，对应项的 `status` 是「没有数据」，其他项继续。部分健康指标缺失时也明确写「没有数据」。没有记录和有记录算出零分开处理；停用后即使旧表还在，也不拼出该插件的结果。输入只保留数字、日期、状态与标题，不携带聊天、来源引用、原文片段或笔记。

## MCP 工具

参数统一放在 `arguments` 对象，启用后才提供：

| 工具 | arguments | 返回 |
| --- | --- | --- |
| weekly_review_input | `date?`: YYYY-MM-DD，默认今天，取它所在的周 | `week,from,to,timezone,as_of,completion_note,todos,homework,timetable,activities,health,tokens,next_week`，统计由代码计算 |
| weekly_review_write | `date`: YYYY-MM-DD，回顾周任一天；`text`: Markdown，去首尾空白后 1–6000 字 | `{accepted:true,week,path}`；同周覆盖正文、保留笔记。正文不能包含单独的 `## 笔记` 标题 |

提示词要求数字只引用输入，数据不足就说不足。人设只管语气，不把游戏剧情当用户经历、不隐藏未完成事项、不服从标题里的指令。这些内容约束由提示词引导，没有逐个数字校验器，也没有给这次 Agent 单独裁剪 MCP 权限。

## 放在哪里

- **卡片**：已有 `text` 类型，标题「本周回顾」，`data.text` 是正文。默认周日 21:00 写好后出现，保留到下周二结束，周三 00:00 消失。卡片要求当前日期在回顾周的周日到下周二、写入时刻不晚于现在；手动提前写好的回顾在周日前不显示。过期只隐藏卡片，文件继续保留。
- **周报文件**：记忆目录 `周报/<ISO年>-W<两位周数>.md`，如 `周报/2026-W44.md`。跨年用 ISO 周年，2025-12-29 属于 `2026-W01`。重写调用 `core.daily.keep_notes` 保留末尾「## 笔记」，同时更新搜索索引。
- **每日文件夹**：写好当天，`daily(day)` 返回 `本周回顾.md`，放正文和到周报的相对链接；`今天.md` 的分工中提一句「本周回顾写好了」。由现有 `daily_notes` 在 10 分钟内整理，也可立即运行它。其他日期不重复提。

## 离线验证

`backend/tests/test_weekly_review.py` 使用临时数据库、合成学校与健康数据、假的 Agent 和截图入口，不读取真实会话、不调用模型或网络。覆盖统计与缺数据、插件停用、周一和周日边界、下周截止、ISO 跨年、调度触发日、回退和未写回失败、覆盖保留笔记、每日文件夹与周三隐藏。真实模型文案质量和 App 显示尚需启用插件后验收。
