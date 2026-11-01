# 视频资料

Windows 原生后端的安装、差别和校准见 [Windows 说明](windows.md)。

启用 `video_notes` 后，在 App 对知识库（或任何一个 Bot）发 B站或 YouTube 的单个公开视频链接，Bot 调用 MCP 工具 `video_notes_add`（arguments 是 `{url, topic?}`）入队；也可以直接用 App scope 的 `POST /api/v1/videos`。默认主题是「视频」，不存在时按知识库默认读者新建。这个功能只增加后端，iPhone 分享菜单入口尚未实现。

## 夜间流程

任务 `plugin:video_notes:process` 默认按运行设置时区在 01:30 启动，一次只处理一个视频，一轮最多 5 个。可以通过 `POST /api/v1/jobs/plugin:video_notes:process/run` 立即启动。默认一轮最多 85 分钟，而且在下一次 `knowledge_organize` 前 30 秒停止；赶不完的保留进度到下一轮，不能保证五个长视频当晚全部完成。3 点整理只处理已写好的资料。

1. yt-dlp 读取标题、作者、时长和发布日期，拒绝直播、合集、超过默认 180 分钟的视频，以及需要登录或付费的内容。
2. 优先下载字幕（含自动字幕），依次选择 `zh-Hans, zh-CN, zh, ai-zh, en` 中可用的 VTT/SRT，转成带时间点的文字。有字幕就不运行语音识别。
3. 没字幕时只下载音频，每 10 分钟用 ffmpeg 转成 16 kHz 单声道 WAV，逐段在本机识别。每段保存进度；不自动下载模型。
4. 默认下载最高 720p 的画面，用 ffmpeg 检测场景变化（阈值 0.3、相隔至少 5 秒、最多 200 帧），逐帧调用本机 doc-text 的 `image` 接口。去掉空文字及与上一帧相似度至少 92% 的文字，最多留下 40 张有文字的 JPEG。
5. 写入 `知识库/<主题>/视频/<标题>（<平台> <ID>）.md`，包含 YAML 来源信息、约 5 分钟一节的时间点链接、幻灯片文字和 Obsidian 图片引用。JPEG 放同目录。这是 source，沿用主题读者；普通知识库同步标为 pending，再由已有的 03:00 整理任务写概念笔记。无读者的主题标为 skipped，不交给整理模型。
6. 成功或最终失败时删除数据目录 `videos/<队列 id>/` 中下载的音视频、临时 WAV、字幕和候选帧。SQLite 保存队列、文字和检查点；记忆目录中的资料和保留图片不删除。每天的 `今天.md` 分工会提到已完成视频，知识库负责 `视频.md`。

每步默认超时 900 秒；普通故障重试 2 次（共 3 次，默认间隔 2 秒、4 秒），失败原因只存简短说明。直播、登录付费、超长等明确拒绝条件直接失败。命令/模型缺失记为 `needs_setup`，磁盘达到上限也用这个状态暂停，修好后下次运行自动继续。`queued / processing / done / failed / needs_setup` 可从队列 API 或「视频」list 卡片查看。排队、处理和暂停条目持续显示；完成、失败条目显示 72 小时。

`step` 是最后完成的阶段：`queued → metadata → subtitles → audio → transcript → slides → output → cleanup`。语音分段和逐帧 OCR 也保存子进度。重启、取消或超时不会重新运行已完成阶段。写文件前先存意图和内容指纹，用独占创建避免覆盖；同名用户文件自动换名。写完文件后崩溃可核对指纹恢复，不覆盖或删除已有记忆文件。

## 安装（由用户在本机终端执行）

后端不增加 Python 依赖，也不代为安装下面的工具。

```bash
brew install yt-dlp ffmpeg
```

macOS Apple Silicon 上可以把语音识别单独装进一个环境，不改后端依赖：

```bash
python3 -m venv "$HOME/Library/Application Support/AemeathBot/whisper-env"
"$HOME/Library/Application Support/AemeathBot/whisper-env/bin/pip" install mlx-whisper huggingface-hub
"$HOME/Library/Application Support/AemeathBot/whisper-env/bin/hf" download mlx-community/whisper-small-mlx --local-dir "$HOME/Library/Application Support/AemeathBot/models/whisper-small-mlx"
```

这是用户主动下载模型，夜间任务不会运行 `hf download`。示例 small 模型权重约 **481 MB**，另需依赖、缓存和推理内存；磁盘大小不等于运行内存。安装与 CLI 参数见 [MLX Whisper 官方说明](https://github.com/ml-explore/mlx-examples/tree/main/whisper)，文件与大小见 [模型仓库](https://huggingface.co/mlx-community/whisper-small-mlx/tree/main)。可以换更大的模型，但要自行预留空间并手动下载。

`asr_command` 填独立环境下的 `bin/mlx_whisper`，`asr_model` 填**本地模型目录**（需有 config.json 和 weights.npz 或 safetensors 权重），不能只填 Hub 仓库名。调用时设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。识别器通过 `ASREngine` 接口替换；兼容 CLI 接收音频路径、`--model`、`--output-dir`、`--output-format json`、`--verbose False`，输出同名 JSON，其中 `segments` 的每项包含秒数 `start` 和 `text`。Linux 可提供兼容本地 CLI；默认 mlx_whisper 只适用于支持 MLX 的 Mac。

幻灯片需已有的 doc-text（默认 `~/Library/Application Support/AemeathBot/bin/doc-text`）；本功能不修改或安装它。非 macOS 可配置兼容的本地识别程序（`doc-text image`，stdin 为 `{"path":"绝对图片路径"}`，stdout 为 `{"text":"识别文字"}`），或设 `slides: false`。`uv run python -m scripts.doctor` 对启用的视频插件只读检查工具和模型；缺项只作为可选警告，不影响核心就绪判断，也不运行下载或识别。

## 配置

通过 `PATCH /api/v1/plugins/video_notes` 设置 `enabled: true` 和 `config`；提交 config 时是整体替换，需保留原来希望使用的配置。

| 配置 | 默认值 | 作用 |
| --- | --- | --- |
| default_topic | 视频 | 未传主题时的主题名，沿用 Knowledge 名称校验 |
| domains | bilibili.com、www.bilibili.com、m.bilibili.com、b23.tv、youtube.com、www.youtube.com、m.youtube.com、youtu.be | 精确主机白名单；仍只支持两种平台的单视频路径 |
| process_cron | `30 1 * * *` | 处理时间，建议在 3 点整理之前 |
| max_per_run | 5 | 每轮最多处理的条目数 |
| run_timeout_minutes | 85 | 本轮时间上限；还会受下次知识库整理时间约束 |
| max_duration_minutes | 180 | 最长视频时长 |
| subtitle_languages | zh-Hans、zh-CN、zh、ai-zh、en | 字幕优先顺序 |
| slides | true | 提取幻灯片 |
| yt_dlp / ffmpeg / asr_command | yt-dlp / ffmpeg / mlx_whisper | 命令名或可执行文件路径（不解析 Shell 字符串） |
| doc_text | 上述 Application Support 路径 | 本地图片文字提取命令 |
| asr_model | 未配置 | 已手动下载的模型目录 |
| asr_chunk_seconds | 600 | 每段语音长度，30–1800 秒 |
| step_timeout | 900 | 每次子进程的最长秒数 |
| disk_limit_gb | 10 | 数据目录 videos 下的空间上限（GiB，含临时音频、视频和帧） |
| retry_delay_seconds | 2 | 首次重试延迟，下次翻倍 |

磁盘在每步前和子进程运行中约每 0.2 秒检查，超出会结束整个进程组并暂停，可能有一个检查间隔内的少量超量；不是文件系统硬配额。暂停时保留中间文件以便继续，用户可释放其他视频数据空间或调高上限。若手动删掉了当前条目的检查点文件，需要恢复文件或重新建立该任务；本版没有重置/删除队列 HTTP 接口。最终 failed 的历史记录保留，重复提交返回历史状态，不会自动重试失败记录。

仅接受 HTTPS，去掉跟踪参数和片段，拒绝 URL 用户名/密码、播放列表、频道和多分 P>1；所有 yt-dlp 调用都带 `--no-playlist`。b23.tv 不由后端发 HTTP 请求，夜间让 yt-dlp 解析；首次短链提交只能按短码去重，解析出真实 ID 后合并重复条目，随后短链也能直接返回已有条目。已知的相同平台和视频 ID 返回原条目，保留原主题；未完成队列（含 needs_setup）最多 50 条，历史 done/failed 不占这 50 条。

## 隐私与使用范围

只处理**公开视频**，下载内容仅供个人学习，不外传。yt-dlp 以 `--ignore-config --no-cookies --no-cookies-from-browser --no-cache-dir` 启动（不加 `--netrc`，所以也不读 .netrc），不读取浏览器 cookie、用户下载器配置或登录凭据，也不索取账号信息。不能公开访问的内容记失败，不尝试绕过登录或付费限制。命令用法以 [yt-dlp 官方参数说明](https://github.com/yt-dlp/yt-dlp#usage-and-options) 为准。

音视频和截图只留本机；云端 Agent 只通过现有知识库读取文字，并受主题读者权限限制。视频标题、字幕和 OCR 文字都作为资料数据进入已有 `knowledge_organize` 提示词，其中的命令或越权要求不会获得执行权限。日志和 runs 不记录文字稿全文、令牌或子进程原始错误输出。

场景抽帧依据 [FFmpeg select 文档](https://ffmpeg.org/ffmpeg-filters.html#select_002c-aselect) 和 [image2 时间戳文件名文档](https://ffmpeg.org/ffmpeg-formats.html#image2-2)。当前验证用临时目录和假 yt-dlp、ffmpeg、ASR、OCR 命令完成，不联网、不读真实视频或用户知识库；字幕质量、平台反爬、MLX 实际内存、Vision OCR 和真实时间戳仍需本机验收。
