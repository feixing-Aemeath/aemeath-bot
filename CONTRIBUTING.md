# 参与开发

欢迎提 issue 和 pull request。

## 写一个插件

插件放在 `backend/plugins/<id>/`，一个目录两个文件：

- `manifest.yaml`：声明插件，字段有
  - id、name、version、description；
  - platforms（`macos`、`windows`、`linux`、`any`）；
  - permissions；
  - schedule（cron）；
  - provides（`source`、`cards`、`mcp_tools`）；
  - config_schema（JSON Schema，App 的设置页按它生成表单）；
  - mascot（用哪张贴图、什么颜色）。
- `plugin.py`：一个 `Plugin` 类，按需实现这些方法：
  - `setup`、`teardown`；
  - `fetch`：数据源读消息；
  - `jobs`：定时任务；
  - `mcp_tools`：给 AI 的工具；
  - `cards`：给 App 的卡片。

规则：

- **插件出错不影响核心**：插件抛出的异常会被核心捕获，记进运行记录。
- **卡片只组合现有类型**：`list`、`stat`、`chart`、`photo_grid`、`text`、`todo_list`，其中 `text` 卡片可以带一张表格。这样 App 不用为新插件重新编译。
- **提示词放在 `backend/prompts/`**，不要写死在代码里。
- **数字由代码算**：模型只拿汇总、负责写成人话。

现有插件就是最好的例子，比如 `weather`（卡片加 MCP 工具）、`homework`（从消息里整理作业）。接口约定见 [docs/api.md](docs/api.md)，整体结构见 [docs/architecture.md](docs/architecture.md)。

## 提交前

- 后端：在 `backend/` 里运行 `uv run pytest`、`uv run ruff check .`、`uv run ruff format --check .`。
- 苹果 App：`swift test --package-path frontend/Packages/AssistantCore`。
- 安卓和 Windows 桌面：由 CI 编译和测试。本地需要 JDK 17，安卓还需要 Android SDK。
- 每个改动配上测试。只能在某个系统上跑的部分（截图、提醒事项、照片库），先写接口和假实现，让测试在任何系统上都能跑。

## 不接受的改动

- 往 QQ、微信、飞书发送任何内容，或者做加好友、点赞之类的写操作。
- 破解或解密聊天软件的本地数据库，或者用 hook、第三方协议登录。
- 把截图、原始聊天或令牌写进日志，或者发给云端。
