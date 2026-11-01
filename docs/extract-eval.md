# 待办提炼评测集

用来衡量 `backend/prompts/extract_todos.md` 配合 Claude Code 或 Codex 的提炼效果：哪些消息该建待办、截止时间对不对、同一件事有没有合并成一条。数据全部是合成的，人名、群名、内容都是虚构的。

## 文件

| 路径 | 内容 |
| --- | --- |
| `backend/evals/extract_todos/dataset.jsonl` | 168 条消息，每行一条，附标注 |
| `backend/evals/extract_todos/meta.json` | 评测时刻、时区、写进记忆的虚构用户资料「关于我.md」 |
| `backend/evals/dataset.py` | 读取和校验数据集 |
| `backend/evals/score.py` | 打分，可单独运行 |
| `backend/evals/run_extract.py` | 跑一遍提炼并打分 |
| `backend/evals/mock_agent.py` | 假的 `claude` / `codex` 命令，只用来验证流程 |

## 数据集

模拟一个大学生（虚构的「林小满」，软件2301班）在 2026 年 10 月 12 日至 15 日收到的消息：

- QQ（121 条，`plugin=qq_screen`）：班级群、课程群、年级群、学生会、篮球社、宿舍楼群，以及和室友、学姐的私聊；
- 飞书（47 条，`plugin=feishu`）：实验室周会群、大创项目组、实习产品组、秋招信息群、学院通知群，以及和导师的私聊。

内容包括辅导员和班委的通知、@全体成员、群公告、作业和考试安排、活动宣传、闲聊，以及被重复转发的同一条通知（其中 4 组在 QQ 和飞书之间重复）。

| 标注类别（`label.kind`） | 条数 | 是否待办 |
| --- | --- | --- |
| deadline / exam / event / request / action / update | 55 | 是（去重后 33 件事） |
| notice：纯通知、活动和讲座宣传 | 33 | 否 |
| conditional：只针对部分人，而用户资料表明不适用（奖学金、补考、体测补测…） | 8 | 否 |
| optional：自愿参加、报名、接龙 | 8 | 否 |
| others_task：交给别人的任务 | 5 | 否 |
| chat：闲聊、收到、表情、图片 | 59 | 否 |

### 每条样本

```json
{"id": "a12", "plugin": "qq_screen", "chat_name": "软件2301班级群", "sender": "孙悦",
 "sent_at": "2026-10-13T10:05:00+08:00", "text": "…", "priority": true,
 "label": {"is_todo": true, "title": "交数据结构第三次作业（第4章4.2、4.5、4.9）",
           "due_at": "2026-10-20", "duplicates": ["b03"], "kind": "deadline"}}
```

标注约定：

- **是不是待办**：有截止时间，或者要求用户本人做某件事（包括必须参加的班会、考试），就算待办。纯通知、自愿参加的活动、条件性通知（用户资料表明不适用）、别人的任务、闲聊都不算。拿不准的样本在 `label.note` 里写了理由。
- **一条消息最多一个待办**。
- **标题**是规范写法，同一组重复样本的标题相同。标题的措辞不计入得分，只给出字符相似度作参考。
- **截止时间**：有具体时刻的写带时区的 ISO 8601；只有日期（如"周五前"、"上课前"、"下班前"）的写 `YYYY-MM-DD`；没有截止时间的写 `null`。"24:00 前"记为 23:59。
- **重复**：转发、重复提醒、改期通知、私聊里对同一任务的补充，都互相列进 `duplicates`，而且必须双向列出。同一组共用一个标题和截止时间；如果中途改过期，整组都用改后的时间（例如班会从 20:00 改到 19:30，a18 和 a42 都标 19:30）。
- 评测时刻固定为 `2026-10-15T22:00+08:00`（晚于所有消息）。按核心的规则，相对日期以每条消息的发送时间为准。

## 指标（`evals/score.py`）

| 指标 | 怎么算 |
| --- | --- |
| 待办识别 精确率、召回率、F1 | 按消息算：只要有一条生成的待办在 `source_refs` 里引用了某条消息，这条消息就算"预测为待办" |
| 截止时间 完全正确 | 只看识别正确的消息。有具体时刻的，误差在 1 分钟以内（所以 24:00 和 23:59 都算对）；只有日期的，要求日期相同且 `has_time=false`；都没有截止时间也算对 |
| 截止时间 日期正确 | 本地日期对就算，不管时刻；另外分别统计缺失、编造（消息没给却填了时间）、错误三种情况 |
| 去重 成对精确率、召回率、F1 | 在识别正确的消息里两两配对，判断"是不是同一件事"，和标注比较 |
| 重复组完全合并 | 每个重复组是否恰好合并成一条待办，而且这条待办没有混进别组的消息 |
| 待办条数 | 标注的去重后件数和实际生成的条数，用来看拆得过碎还是合并过头 |

报告最后会列出误报、漏报、截止时间不对的消息和没合并上的重复组，方便逐条检查。

## 运行

```bash
cd backend
# 验证流程（不调用模型）：假 CLI 走真实的 harness 命令行、MCP stdio 桥接和核心
uv run python -m evals.run_extract --harness claude_code --mock
uv run python -m evals.run_extract --harness codex --mock

# 真实评测（在 Mac 上，CLI 已登录）：会把 168 条合成消息发给模型，消耗额度
uv run python -m evals.run_extract --harness claude_code
uv run python -m evals.run_extract --harness codex --timeout 1800 --rounds 3

# 只给已有结果打分
uv run python -m evals.score .cache/evals/<目录>/predictions.json --json report.json
```

运行脚本的做法：在临时目录启动一个一次性的核心（临时数据库、假的提醒事项、关闭定时任务，监听本机随机端口），把数据集当作 qq_screen / feishu 消息入库，把用户资料写进记忆，然后用和定时任务完全一样的提示词模板和 MCP 接法启动所选的 CLI（只把提示词里的"当前时间"固定为评测时刻）。还有未处理的消息时最多重跑 `--rounds` 轮。结果写在 `backend/.cache/evals/<时间>-<harness>/`：`predictions.json`、`report.json`、`report.md`，这个目录已被 Git 忽略。

mock 用简单的关键词规则，它的分数不代表任何模型的效果，只证明整条链路通了：harness → CLI → MCP → 写待办和摘要 → 标记已处理 → 打分。

## 维护

- 改提示词之后跑一遍，对比 `report.md`。
- 加样本：在 `dataset.jsonl` 里追加一行，按上面的约定标注，然后运行 `uv run pytest tests/test_eval.py`。重复关系不一致、非待办却带标题等问题都会被校验出来。
- `run_extract.py` 里的提示词和 MCP 配置是照着 `core/jobs.py` 的 `extract` 写的，那边改了，这里要一起改。
