# GoodNotes 备份与课堂照片

这两个入口只把原资料带进知识库，后续沿用 `memory_index` 的本机文字提取和每晚 03:00 的 `knowledge_organize`。主题无读者时不会交给整理 Agent；照片和 PDF 原件留在本机，模型收到的只有提取文字。App 状态接口和字段见 [api.md](api.md)。

## 用户配置步骤

1. 在 Mac mini 的 `backend/` 创建独立的设备令牌（只显示一次，请直接保存到设备；不要放进插件配置或 Git）：

   ```bash
   uv run python -m scripts.tokens create "GoodNotes（iPad）" --scope dav
   ```

2. 在 iPad / iPhone 安装 Tailscale，登录和 Mac mini 相同的 tailnet。Mac 按 [mac-setup.md 第 5 节](mac-setup.md#5-tailscale-和-tailscale-servehttps) 开启整个端口的 HTTPS 转发：

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:8765
   ```

   后端仍只监听 `127.0.0.1:8765`；使用 Serve，不要启用 Funnel。

3. GoodNotes 6：设置 → 自动备份 → WebDAV。服务器填 `https://<机器名>.<tailnet>.ts.net/dav/goodnotes/`，用户名任意非空值（例如 `ipad`），密码填刚创建的 **dav 设备令牌**。备份格式选 **PDF**。这是 GoodNotes 到 Mac 的单向备份，不是笔记本双向同步。在后端启用 `goodnotes` 插件。PDF 放进与知识库主题同名的文件夹，或配置 `mapping`；首次备份完成后至少等待两分钟，再等下一次扫描。
4. Mac mini 登录与 iPhone 相同的 Apple 账户，在「照片」App 设置 → iCloud 打开「iCloud 照片」。优化存储可以保留，导出时会从 iCloud 下载原图，需要网络和可用磁盘空间。
5. 安装新工具后，在 Mac 桌面的终端主动申请一次照片权限：

   ```bash
   "$HOME/Library/Application Support/AemeathBot/bin/photo-export" authorize
   "$HOME/Library/Application Support/AemeathBot/bin/photo-export" status
   ```

   首次弹窗允许访问照片。启用并配置 `timetable`（见 [timetable.md](timetable.md)），再启用 `class_photos`。Linux 会显示该插件不支持，不调用 PhotoKit。

两个插件默认均停用。通过 `PATCH /api/v1/plugins/<id>` 启用，`config` 是完整替换；没有修改的配置可传 `{}` 使用默认值。立即扫描可调用 `POST /api/v1/jobs/plugin:goodnotes:scan/run` 或 `POST /api/v1/jobs/plugin:class_photos:scan/run`，202 之后查 runs。停用导入插件不会撤销 DAV 令牌，也不会关闭 DAV 服务；要停止设备备份访问，运行 `uv run python -m scripts.tokens revoke "GoodNotes（iPad）"`，并关闭设备自动备份。

## WebDAV 协议和存储

入口 `/dav/` 的磁盘根是 `ASSISTANT_DATA_DIR/dav/`（默认 `backend/.state/dav/`），与记忆目录分开。服务自动准备 `goodnotes/`。设备可以在整个 DAV 根目录管理备份，`dav` scope 不按子目录隔离。

遵循 [RFC 4918](https://www.rfc-editor.org/rfc/rfc4918) 的文件/集合方法约定：

| 方法 | 行为 |
| --- | --- |
| OPTIONS | `DAV: 1`，Allow 列出下面支持的方法；不宣称锁支持 |
| PROPFIND | `Depth: 0` 为当前资源，`1` 加直接子项；207 Multi-Status XML，支持空 body/allprop、propname、指定 prop；未知属性单独返回 404 propstat |
| MKCOL | 建一个集合，父目录必须存在；201，已有 405，不支持带 body 的扩展 MKCOL（415） |
| PUT | 流式写入私有临时文件，完成后原子替换；新建 201，更新 204；断连或超限清理本次临时文件、保留旧文件 |
| GET / HEAD | 原文件 / 只有头；集合不提供 HTML 目录列表（405） |
| DELETE | 删除 DAV 中的文件或集合；204；不能删除 DAV 根 |
| MOVE / COPY | Destination 可用同 Host 的完整 HTTP(S) URI 或 `/dav/...`；不跨服务器、不越出 DAV。Overwrite 默认 T，已有目标且 F 返回 412；新建 201，替换 204 |
| LOCK / UNLOCK | 405；本版没有锁，尚未用真实 GoodNotes 验证是否存在额外客户端要求 |

PROPFIND 的属性：`getcontentlength`、HTTP 格式的 `getlastmodified`、集合的 `resourcetype/collection`、`getetag`、ISO 格式的 `creationdate`。链接会被跳过，目录项 href 正确进行 URL 编码和 XML 转义。Depth infinity（含省略 Depth 的默认 infinity）返回 403 和 `propfind-finite-depth`；COPY 集合支持 Depth 0 / infinity，MOVE 集合只支持 infinity。拒绝自身或祖先/后代之间的 MOVE/COPY。单文件 PUT 支持 `If-Match` 和 `If-None-Match` 的单 ETag / `*` 条件；不实现 DAV If 锁条件、PROPPATCH、死属性或版本管理。

Bearer 和 HTTP Basic 都接受。Basic 用户名不参与验证，密码必须是设备令牌；不会把 Basic 的环境主令牌当设备令牌。`dav` 或 `app` scope 均可访问，其他 scope 返回 403。所有其他 HTTP / MCP 接口继续只接受 Bearer。无效、作废、过期令牌返回 401；DAV 401 同时提供 Basic/Bearer challenge。DAV 方法不进入 OpenAPI，App 应使用知识库状态接口。

路径拒绝 `..`、绝对文件路径、反斜杠、控制字符和任意路径组件的符号链接；递归复制/移动/删除也拒绝含链接的目录。PUT 不经过 App 上传的 50 MiB 缓冲中间件。单文件默认 **500 MiB**、整个 DAV 默认 **20 GiB**，通过 `PATCH /api/v1/settings` 调整 `dav_max_file_bytes` / `dav_max_total_bytes`（正整数，单位字节，立即生效）。超出返回 **507**。请求在最终提交前串行校验配额；替换按 `当前总量 - 旧文件 + 新文件` 计，COPY 计入新增量。临时文件在同一数据目录的 `dav_uploads/`，替换期间磁盘还需要容纳临时副本；磁盘满也返回 507。App 普通 `POST /knowledge/files` 上传仍为 50 MiB。

## GoodNotes 插件

配置示例：

```json
{
  "cron": "*/15 * * * *",
  "settle_seconds": 120,
  "mapping": {"本学期/数据结构/": "数据结构", "本学期/": "课程资料"}
}
```

`settle_seconds` 至少 120；最新写入尚不足这个秒数的 PDF 显示 waiting。mapping 对相对 `goodnotes/` 的路径进行大小写敏感的字符串前缀匹配，最长优先；目录前缀请保留末尾 `/`。显式 mapping 可以新建主题，使用当时的默认读者。否则用直接所在文件夹名，再用笔记本名，匹配已有主题（忽略大小写和首尾空白）。主题名复用 Knowledge 的校验；没有匹配就显示 unmapped，不复制。

目标为 `知识库/<主题>/GoodNotes/<笔记本名>.pdf`。同名笔记本映射到同主题会冲突，不会合并或覆盖。SQLite 的 `goodnotes_files` 保存相对备份路径、目标、上次写出的 SHA-256 和状态：

| status | 意思 |
| --- | --- |
| copied | 已安全复制 / 已经是相同内容 |
| unmapped | 没有对应主题；message 提示移动文件夹或配置 mapping |
| conflict | 目标是用户文件、属于其他备份，或已被用户修改；不覆盖 |
| missing | 备份里已没有这个路径；保留记忆目录副本和归属记录 |
| wrong_format | `.goodnotes` 文件不导入；提示「请在 GoodNotes 里把备份格式选为 PDF」 |
| waiting | 文件还未静置两分钟，或复制期间源文件发生变化；下次再试 |

更新只会原子替换本插件登记过、且当前 SHA 仍等于上次写出值的目标。映射改名、备份重命名和删除不会删除记忆目录里的旧副本。用户文件不能仅凭内容碰巧相同就取得插件归属。数据库与文件没有跨介质事务：崩溃后若缺少写出收据，会保守显示 conflict，而不会认领已有文件。请保留 SQLite 备份。

PDF 提取与原文件预览允许到 **500 MiB**，其他格式仍为 50 MiB；提取文字最多 2,000,000 UTF-8 字节，超出标 truncated。doc-text 对 PDF 的上限也是 500 MiB（逐页读，不整份进内存），图片仍是 50 MiB；它不需要任何系统权限，重新编译安装不会丢权限。真实手写的 Vision 效果仍需真机验收。打字和导入课件通常有文字层；压平的手写笔迹靠本机 OCR，准确率取决于字迹、清晰度和版面，不能保证完整识别。

## 课堂照片插件

配置示例（所有字段可省略）：

```json
{
  "cron": "*/30 * * * *",
  "lookback_days": 3,
  "before_minutes": 5,
  "after_minutes": 10,
  "min_text_chars": 15,
  "course_topics": {"高等数学I": "高数"},
  "skip_courses": ["体育"]
}
```

`helper` 可指定 photo-export 的绝对路径，默认是安装位置。`lookback_days` 范围 1–30；前后宽限 0–120 分钟；文字门槛 1–10000 个非空白字符。

第一次查最近 3 天，之后从持久化游标按日取新照片，同时重看最近 lookback_days 天的元数据，接住 iCloud 延迟同步的照片；完整处理一个窗口后才推进游标，失败窗口保留以重试。创建时间是拍摄时间，不是下载到 Mac 的时间；延迟超过回看窗口的旧照片仍可能漏过。需要补查时可在维护窗口重置 `plugin:class_photos:cursor`（SQLite kv）到目标日期的带时区 ISO 时间，已记录的 ID 仍不会重复导出。单批最多 2000；达到上限自动对时间范围二分，避免静默丢掉后续条目。极端情况下同一毫秒超过 2000 张会报告 `class_photos_batch_full` 并保留游标。

通过当前启用的课表实例调用 `timetable_days`，只匹配 kind=`课程`，包含课前 5 分钟和课后 10 分钟的两个端点。重叠窗口优先选实际上课时段，再选离开课时间最近的一节。推测周也采用，同时保存 `estimated_from`；完全未知的日期没有候选课。没有启用课表时记录失败并保留游标。课程名先去掉末尾 `（推测）` / `(推测)`，再应用跳过列表与主题映射。

未落入课程窗口、或被跳过的课，只读 ID、创建时间和图片类型等元数据，不导出、不做 OCR。候选图片导出到数据目录暂存，用 Documents 做本机 OCR；不足门槛（含零字）记「不像课件」，丢掉暂存原图。提取器不可用等技术失败保留窗口等待重试，同时继续处理后续照片，不因单张失败挡住其他图片。当前使用 Documents 支持的 PNG/JPG/JPEG/HEIC；其他图片格式或超过 50 MiB 的图片记 unreadable、丢弃暂存，不阻塞后续导入。达到门槛后新建 `知识库/<主题>/课堂照片/<YYYY-MM-DD>/<HHMM>-<id前8位>.<原扩展名>`（文件名中的非安全 ID 字符改成 `_`）；现有同名文件保留，内部记录 conflict。主题首次出现固定默认读者。暂存副本在处理完成/失败后清理；启动时清理这个插件自己留下的 `photo-*` 暂存目录。

SQLite 的 `class_photos_files` 按完整 PhotoKit ID 保存处理结果、拍摄日期、课程、主题、推测来源和目标。已入库、判为非课件、跳过或冲突的 ID 不再导出；用户删掉知识库照片也不会恢复。OCR 的文字门槛只是启发式，有足够文字的私人照片也可能被误判，课表推测也可能有误；可用 skip_courses 限制范围。

`photo-export` 独立签名并以自身责任进程运行，权限归给它，不归 Python。PhotoKit 的 [PHAccessLevel](https://developer.apple.com/documentation/photos/phaccesslevel) 只有 addOnly / readWrite，读取必须请求名为 readWrite 的级别，没有独立 readOnly；工具没有任何照片库写入调用，也没有 AddUsageDescription。limited 表示只能读用户选中的照片，可能漏课件；authorized 才有全库读取范围。status/list/export 都不会主动申请权限，只有 authorize 会弹窗。工具允许原件从 [iCloud 下载](https://developer.apple.com/documentation/photos/phassetresourcerequestoptions/isnetworkaccessallowed)，单次导出超时 120 秒并取消请求。不会建相册、改收藏或删除照片。

## 验证边界

自动测试全部使用临时资料、假 PhotoKit / 假提取器、内存 ASGI 请求，不需要真实账号、照片或监听端口。没有在沙盒内编译 Swift，也没有验证真实 GoodNotes 的完整备份、PhotoKit 权限弹窗/iCloud 原件下载、真实大 PDF 的版面和 Vision 效果；这些要在沙盒外按上面的配置步骤验收。合成 51 MiB PDF 的系统 PDFKit 解析和图片渲染路径已验证，OCR 用假提取器。已有 screen-helper、apple-sync、doc-text 源码和安装身份保持原样。
