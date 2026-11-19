# 目标跟进 · 0 级与 1 级

`goals` 默认关闭，启用后归入助理的页面，不新增 Bot 或页签。
今天这一步只在 App 里；现成回答仍通过原聊天接口发送。
尚未完成连续 14 天真实使用验收；后台推送、响铃仍未完成，试用需要每天打开 App。

## 建目标和改计划

助理围绕为什么、做到什么算成和必要日期、每周节奏、复述确认来商量，一次一问，原则上最多四问。
必要的日期不能猜，也不能为了凑四问漏问；标准和日期一起问，不把同一问拆成多轮。
复述只有「目标 / 做到就算成 / 节奏 / 这周」四节，最后点「定下来」才应用。
数字问法、卡点、小一步、期限称谓、结果问法由 Agent 按用户的话填入档案，不作为确认表单展示。

工具只在紧接最新复述、用户回答「定下来」或「再改改」的这一轮收窄。
复述必须仍是助理最新一条 ready 消息，并带这两个选项。
用户说其他话时保留普通对话工具，同时清掉旧草稿；之后继续改计划可以重新暂存。
`goals_get` 不改变授权，`goals_propose({abandon:true})` 可以放弃商量。

同一时间最多一个开放目标：

| 状态 | 行为 |
| --- | --- |
| active · 进行中 | 计划日放今天这一步，按约定跟进 |
| paused · 先歇一歇 | 停掉每天的待办，到约定日只问一次 |
| waiting · 等结果 | 不放步骤、不做日常回顾，到约定日问结果 |
| achieved · 达成 | 保留过程、写结局，释放名额 |
| dropped · 先放下 | 保留过程和放下日期，释放名额 |

想放下或换目标时，通过 goals_propose 的 `changes.status=dropped` 复述一句，用户确认后生效。
等结果期间报了新的数字、标准未全部满足时，程序说明还差哪项、差多少，问「接着准备」或「先放下」。
这个问题中的「先放下」直接生效；「接着准备」请用户说下次日期，再由 Agent 暂存新期限并确认回到进行中。
进入等结果前的练习数字不会被误当成刚报出的结果。
放下时说出目标名和实际推进天数，不评判；达成时另说出当初为什么、开始与结束日期。
两种结局后都出现「下一个目标」空状态卡；新目标使用新档案和新记录，不继承旧目标的计数。

## 今天这一步与每日文件

计划日安静时段结束后建一条普通 Todo，`source_plugin=goals`。
按当天课表和带时间的作业截止找空档，冲突结束后至少留 15 分钟；只提议挪时间，不自动接受。
例如「今天 16:30 有临时实验，挪到 18:15？」；不回答就不采用新时间。
原定时间已经过去时不补放；当天没做的次日收起，状态改为 cancelled，不变成逾期。

「不进提醒事项」与「系统提醒」分开：

- `core/apple.py` 的 `REMINDERS_EXCLUDED` 包含 watchdog、goals，仅控制不进提醒事项和日历。
- 每日文件中的系统提醒只有 watchdog。
- `待办.md` 单列「今天的目标」，如「16:30 · 高数练习 · 第 1 次（约 40 分钟）」，不称为截止。
- `今天.md` 摘要分别计「目标 1 步」和系统提醒数量。今日提醒和普通聊天可把目标列进用户自己的待办。

来源使用普通的 TodoSource 字段：plugin=goals、chat_name=目标短标题、sender 为空。
App 名字表将 goals 显示为「目标」，来源因此读作「目标 · 高数85分」，没有目标专用的 App 拼接逻辑。
合并其他来源后，包含 goals 的待办仍不会同步到苹果。

## 进展、数字和卡点

App 或手表勾选算一次，Agent 勾选不算。手表运动、已交作业、用户明确说这次做了也能计入。
次数和实际推进天数由代码计算，每条带日期、可撤销，保存用户本轮 message_id 和完整原话。
程序不再用完成词或否定词词表判断；Agent 结合上下文判断是否明确完成，不清楚先问。
记录后告诉用户「算一次」，说「那次不算」就能撤销；模型自己说的话不能作为记录。

运动类型不限跑步，回顾使用实际活动类型和分钟数，未知类型叫「运动」，不把计划距离当成实测距离。
睡眠不足六小时的建议使用目标本身的 step_title，不拿最后一次运动类型替换目标，也不自动改计划。

需要数字的标准，在勾选后问一次。首次未达标只记下，不劝少做。
同一标准连续两条有效数字未达标，才把小一步作为选项提出；整个目标一周最多提一次。

卡点选项保存 text 和 kind，按三类处理：

| kind | 默认选项 | 选中后的动作 |
| --- | --- | --- |
| content | 看不懂 | 保存卡点，交给对话 Agent 查笔记、知识库并帮助；三天后只对这类追问 |
| time | 时间不够 | 当场提议 smaller_step，给「好 / 照原计划」 |
| materials | 等材料 | 问「三天后 / 下周 / 我自己说」；选日期后暂停，到那天问材料是否到了 |

专用卡点如「选择题」由 Agent 标为 content。旧版字符串选项兼容读入，时间不够、等材料归入对应类型，其余归 content。
「我自己说」交回 Agent 商量日期，再按暂停的暂存确认流程处理。
「这一小段」只用于内容卡点；时间和材料不套用这句话。

## 节奏与周回顾

计划日早上放待办，不必开口；需要回应时再问。
连续两个计划日没做也没回，就停止放新待办，只问一次是否改小或歇一周。
之后只在原有周回顾里提一句，卡片写「等你回来」；用户再次开口后接上。

「改小一点」只改变当周：每周次数减一，最少一次，周一按原计划恢复。
回复明确说「这周先做 N 次，下周回到 M 次」；永久调整仍通过 goals_propose 确认。
周回顾后的「照这个来」回复下周次数；「慢慢接上」只用于歇息或沉默后回来。

周日 21:00 使用原 weekly_review 的周期、提示词和知识库文件，不另起任务。
输入增加 goals 聚合数据；当周达成或先放下的目标放在 `goals.finished`，各写一行结局和推进天数。
即使已经开始新目标，旧目标当周的结局仍保留。歇着、等结果的目标不做日常进展回顾。
回顾写好后询问下周是否照旧，不回答维持原计划、不加码。
截止第二天自动进入等结果时说一句辛苦了，占当天目标开口额度，不重复补发。

## 主动消息与回答按钮

通用核心 proactive 持久化幂等键、内容、时间和发送记录，发送与计额度原子完成，重启不重复。
全部 Bot 共用每日额度；目标后台每五分钟检查，安静时段、上课时顺延，过时的问题不补发。
用户消息的模板回答与勾选后的数字追问不占主动额度。

程序只处理已送达、未过期、未回答、未取消、属于当前档案且仍是最新 ready Bot 消息的问题的完全匹配回答。
其他输入交给 Agent。问题过期、取消、已回答后，聊天 API 的 replies 清空；后续回复也收掉旧按钮。
App 把按钮画在问题气泡正下方，要求它是该 Bot 最新聊天消息、Bot 作者、ready、今天且在当前可见时间线中。
摘要和提炼记录排在问题之后也不会把按钮挪到摘要下面；打开昨天的消息也不会重现昨天的按钮。
点后立即收起，仍调用原发送消息接口。

目标提示词拆在 prompts/goals_chat.md，只在 goals 启用的助理对话加载；通用提示词不要求按钮。
非助理、或插件关闭时，chat_reply 忽略 replies。
按钮为空、过长、过多或格式不合格时丢掉按钮，文字仍正常保存，只记录 `chat_replies_ignored`，不打印内容。

| 配置 | 默认值 |
| --- | --- |
| daily_limit · 全部 Bot 每天主动次数 | 2 |
| goal_limit · 目标每天主动次数 | 1，可设 0 |
| quiet_start / quiet_end | 23:00 / 07:30，按设置时区 |
| busy_week_threshold | 4，本轮不调整 |

失败指数退避：5、10、20 分钟起，最多六小时。连续三次由看门狗生成「目标提醒暂时没发出来」的 App 待办，
提示「今天的目标提醒没发出来，我在重试，你不用做什么。」
停用或删除插件会收起未完成步骤和未发问题；正常停机保留状态。
关闭时不创建目标表、注册目标任务或工具。

## 存储和工具

profiles、drafts、steps、progress、questions 均以 goals_ 开头，只在启用时创建。
唯一索引只约束 active/paused/waiting，旧索引会迁移；revision 检查过期修改。
核心 todo_changes 持久化状态变化及 from_agent，插件出错或重启后能续读。

| 工具 | 输入和约束 |
| --- | --- |
| goals_get | 当前档案、可撤销记录和代码计算的本周进度 |
| goals_propose | profile；修改带 revision，可带 changes；只暂存。`{abandon:true}` 丢掉草稿 |
| goals_confirm | draft；紧接复述、用户原话为「定下来」才应用。放下时返回应说明的 text |
| goals_record | 本轮 message_id、完整 quote；可带 criterion/value，数值须出现于原话 |
| goals_revoke | record_id；用户表达不算、撤销或记错 |

profile 保存 title、why、deadline、result_date、criteria、schedule、weekly_title、weekly_target、step_title、activity、homework_ids。
activity 为 study/exercise/homework，旧 running 按 exercise 兼容。
criteria 支持 steps/number/result、target、comparison=gte/lte、samples；number 自带 question 和 2–4 个 `{text,value}` 回答，
「我说个数」「先不说」没有 value。schedule 是 weekday（周一=0）、time、minutes（5–45），step_title 支持 `{n}`。
辅助字段包含 blocker_choices（text/kind）、smaller_step（title/minutes/可选 blocker）、deadline_label、result_question、result_replies、waiting_triggers。
缺省使用通用措辞，不从标题或数字阈值猜目标类型。
changes 可设置 active/paused/waiting/dropped、ask_on、blocker、blocker_day；暂停和等结果必须有再聊日期。

笔记使用 `目标/YYYY-MM 目标名.md`；同月同名的不同目标加序号，避免覆盖。
改目标名时迁移原文件，保留用户的「## 笔记」；旧编号文件兼容迁移，索引同步更新，异常后可重试。
只在正文变化时保存；先放下和达成都写结局与日期。用户文字不替换成所谓内部用词，截断显示加省略号。

## 本轮验证与范围（2026-11-07）

测试只用临时数据库、假时钟、假 harness 和合成消息、课表、健康记录。
六级、跑步、高数、力量训练和骑行逐日场景均保留，按本轮行为更新断言。
新增 test_goals_recheck.py 覆盖第 2 轮 1–11 条；Swift 的 GoalRepliesTests 覆盖按钮日期、可见位置和中文来源。

新增 41 项复查回归，目标场景与提示词授权合计 94 项通过。先用旧行为复现，再修改：每日文件归类、草稿越轮、未达标收尾、失效选项、词表误拒、重复减量和旧笔记迁移等均有回归。
Swift 用例也确认旧可见规则会错误显示昨天或隐藏问题的按钮，修复后通过。

在 backend/：

- `uv run --locked pytest -q`：1379 通过、1 跳过、2 条依赖弃用警告；跳过的是会运行真实安装脚本的 macOS 保护性用例。
- `uv run --locked ruff check .`：通过。
- `uv run --locked ruff format --check .`：214 个文件通过。
- `uv run --locked python -m scripts.export_openapi`：完成，HTTP 模型未改变，导出没有额外差异；精确快照测试通过。

在 frontend/Packages/AssistantCore/：

```sh
swift test --disable-sandbox --scratch-path .gpt-run/swift-test
swift build --disable-sandbox --target AssistantUI --scratch-path .gpt-run/swift-ios --triple arm64-apple-ios18.0 --sdk "$(xcrun --sdk iphoneos --show-sdk-path)"
swift build --disable-sandbox --target AssistantUI --scratch-path .gpt-run/swift-watch --triple arm64_32-apple-watchos10.0 --sdk "$(xcrun --sdk watchos --show-sdk-path)"
```

Swift 171 项测试通过；iOS、watchOS 均编译通过。`git diff --check` 通过。
未提交、推送、安装或重启服务，未操作真实聊天、屏幕、苹果数据、部署状态或私有知识库。

第 2 轮审查全部接受；第 11 条的子项分别由临时减量、周回顾回应、课后空档、原话与省略号、过期卡片、同名笔记迁移、睡眠建议及提示词用例覆盖。

这一轮不做：模板的人设语气批量改写、减量门槛调整、QQ/飞书考试日期自动同步、头像庆祝、推送和响铃。
原 brief 排除的并行目标、积分、连续打卡、卡片动作接口、自动重排等也不增加。
第一个真实目标与为什么、节奏、主动额度、安静时段和允许给云端模型的内容仍待用户决定。
本周回顾和课表插件是否启用也应在试用前确认。
未验证真机视觉、真实手表上传时延、真实模型的商量措辞或 14 天连续使用。
