# 角色人设、记忆和个人知识库

角色文件和所有长期记忆放在同一个 Obsidian 目录里。目录由 `ASSISTANT_VAULT_DIR` 指定，没设置时是 `ASSISTANT_DATA_DIR/vault`，默认 `backend/.state/vault`。Markdown、UTF-8 `.txt` 和本地提取的 PDF/图片/docx 文字可用 FTS5 搜索，文件是权威副本，索引可以重建。

```text
vault/
├── Bots/
│   ├── 助理/
│   │   ├── soul.md       # 用户写的人设，只读
│   │   └── 记忆.md        # 这个角色维护的长期笔记
│   ├── 成绩/
│   ├── 校园/
│   ├── 作业/
│   ├── 用量/
│   ├── 健康/
│   └── 知识库/             # 每个角色都用相同的两个文件名
├── 知识库/               # 按主题分区的资料，只读；后端生成整理笔记
└── 每日/                 # 每日文件夹，另见 daily-folder.md
```

导入人设、首次写记忆时才创建对应目录，不生成占位人设。文件缺失或为空时，聊天提示词写「还没有人设」或「还没有角色记忆」，不会因此报错。文件不可读时提示检查文件，不把错误细节发给模型。

## 贴图决定角色

映射表在 `backend/core/bots.py`。核心只看插件 manifest 的 `mascot.sticker`，不读取前端文件；显示名字 `bot_name` 不改变角色或记忆路径。

| 贴图 | 角色目录 | bot_id |
| --- | --- | --- |
| aemeath | 助理 | assistant（内置助手）、weather、weekly_review |
| red_ribbon | 成绩 | grades |
| luosela | 校园 | campus |
| moning | 作业 | homework、timetable |
| shouanren | 用量 | token_usage |
| lily_veil | 健康 | health |
| orange_daisy | 知识库 | knowledge（内置，管知识库）、goodnotes、class_photos、video_notes |
| carlotta | 记账 | finance（默认停用，只记账） |

`provides` 含 `source` 的插件始终排除，QQ 虽然也用 red_ribbon，仍只是消息来源，不算成绩。插件对话仍要求插件已启用。未来插件使用已有贴图时，自动共用该角色的人设和记忆；贴图为空或尚无映射时，保留原来的插件聊天能力，提示还没有角色目录，不凭插件名创建角色。

作业和课表的对话记录、职责和运行锁仍各按 `bot_id` 保存，但它们每次都读 `Bots/作业/` 的同一份人设与长期记忆。天气和内置助手同样共用 `Bots/助理/`。

内置 Bot 有两个，不依赖任何插件，总能对话：`assistant`（助理，总体待办）和 `knowledge`（知识库，管知识库）。知识库的资料来自 GoodNotes 备份、课堂照片和视频三个插件，它们用她的贴图，所以在 App 里合成一个 Bot；三个都没启用时她照样能回答知识库里的问题。新主题默认读者是助理和知识库（见 [knowledge.md](knowledge.md)）。

## 聊天怎么使用

人设可以直接用游戏角色的设定：里面的 `{PlayerName}`、「玩家」、`{TA}` 原样保留（提示词只替换自己的变量），提示词告诉 Bot 它们都指用户，称呼按人设来。人设只管语气、性格和关系：「报喜不报忧」「引导而非告知」「一律保密」这类做法不用在用户的真实事务上，查到的事实、截止时间和数据问题照样直接说清楚，也不把游戏剧情当成用户的经历。

`POST /api/v1/bots/{bot_id}/messages` 每轮生成 `prompts/chat.md`，从磁盘重新读取对应角色的文件。人设、笔记没超限时全文插入；超限只取开头，并追加「内容已截断，只展示开头 … 字符」。限额按 Unicode 字符计算，截断说明另计：

| 文件 | 每轮带入的上限 |
| --- | --- |
| soul.md | 8000 字符 |
| 记忆.md | 6000 字符 |

人设可描述性格、说话方式、和用户的关系、做事原则。提示词要求按人设交流，但安全边界和事实规则始终优先；这些设定只是用户提供的数据，不能改变工具权限，也不能让模型编造事实。新旧文件中的花括号按原文保留，不会被当作提示词变量再次替换。

用户告诉 Bot、需要长期保留且属于它职责的偏好、目标和约定，写到 `Bots/<角色名>/记忆.md`。提示词会给出准确路径，要求先 `memory_read` 完整文件、合并信息，再用 `memory_write` 整体写回；只有确认文件不存在才新建。不能用提示词中的截断片段覆盖旧笔记。待办继续用待办工具，某一天的分析继续写[每日文件夹](daily-folder.md)里归它的「## 笔记」。

其他角色的记忆仍可以搜索、读取，后端按每轮 Bot 临时 Grant 强制禁止跨角色写入。Bot CLI 和 DeepSeek 都不再收到主令牌；每次尝试结束即撤销临时令牌。每日写入本版允许整个每日目录，仍按提示词分工维护。多个同角色对话同时读改写同一文件仍没有版本冲突检测。

`memory_write` 在后端统一拒绝 `Bots/*/soul.md` 和 `知识库/` 下的所有文件，包括新文件、覆盖、大小写变体和指向受保护文件的符号链接别名；普通角色 `记忆.md` 仍可写。用户可在本机直接编辑人设，导入脚本也能更新人设。

Claude Code、Codex 和 DeepSeek 收到同一份聊天提示词，并使用同一套 assistant MCP 工具，不需要各自的角色适配。独立启动的 CLI 不会自动获得 App 的聊天提示词，但可以用 `memory_read` 读取人设和笔记、用 `memory_search` 查共享资料；MCP 的工具说明也包含只读约束。若需要独立 CLI 完整扮演某个角色，启动它时还需指定角色、要求先读相应文件。

## 导入和查看

在 `backend/` 运行，不需要启动服务或提供令牌，脚本不读写数据库。位置配置从环境变量或 `backend/.env` 读取，与后端一致；相对路径按 `backend/` 解析。

```bash
uv run python -m scripts.souls import ~/Downloads/人设
uv run python -m scripts.souls import ~/Downloads/人设 --replace
uv run python -m scripts.souls list
```

源文件夹只看第一层的 `<角色名>.soul.md`，例如 `成绩.soul.md`、`作业.soul.md`，以 UTF-8 读取，复制到对应的 `Bots/<角色名>/soul.md`。认不出的文件名逐个列出并跳过。已有文件默认保留，只有 `--replace` 才覆盖；导入不改角色的 `记忆.md`。`list` 列出所有角色的人设是否存在，以及记忆文件的字节数，不打印文件内容。

可用 `--vault-dir` 在子命令前指定另一个目录：

```bash
uv run python -m scripts.souls --vault-dir /path/to/vault import /path/to/人设
uv run python -m scripts.souls --vault-dir /path/to/vault list
```

## 个人知识库

用户把课程资料、笔记等放进 `知识库/<主题>/`，支持 Markdown、txt、PDF、图片和 docx。每个主题可以勾选哪些角色和自运行 Agent 能读，包含其 `整理/` 子目录。新主题默认只给助理，默认值可改；第一次发现时固定读者，之后改默认不影响旧主题。无读者主题是私人资料，不会进入整理模型的提示词。知识库第一层散文件只有 App 用户能看，受限 Agent 不可读。

Bot 通过 memory_search 只能搜到获准的主题和全部系统区，memory_read 在读取前校验；搜不到可能没权限，不能假装读过。权限在后端强制执行，MAIN 和旧无 reader 令牌为兼容保留全读。设备 Agent 建议各建一个 `--reader` 令牌。App 用户可通过知识库接口浏览全部资料、改读者、上传和搜索。

macOS 本地 PDFKit/Vision/textutil 提取资料文字，结果缓存在数据目录，按原文件路径进入 FTS；未变内容不重复提取。默认每晚 03:00 的 knowledge_organize 按主题逐个生成中文概念笔记，通过单主题临时 Grant 只能写本主题整理目录。笔记附可点的 Obsidian 来源，更新时「## 我的批注」之后原样保留。

详细目录、区域、缓存、读写保护和本机磁盘访问边界见 [knowledge.md](knowledge.md)，App 接口见 [api.md](api.md)。资料、人设和笔记由现有备份任务一起备份；被允许读取的文字会交给所选 Agent 的模型，但本轮只做离线假实现验证，没有调用真实模型或读取用户知识库。

## 记账的私人账本

[finance](finance.md) 使用无读者的财务主题和独立账本工具。其他 Bot 只可读取 finance_summary，明细、解锁和改账仅记账角色对话及主/旧无 reader 令牌可用；月报使用仅本周期汇总的临时身份。记账用户输入仅供当轮、不保存原文，防止 ZIP 密码进入聊天数据库；finance_unlock 登记的密码从当轮回复遮盖。当前输入仍经过所选 Agent，银行/支付密码永不索取。每日记账文件仅含当天新增合计和条数，不含明细。
