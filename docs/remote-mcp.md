# 从其他设备连接记忆中心（远程 MCP）

例子：学校的 Windows 电脑上的 Claude Code 或 Codex，经 Tailscale 读写家里 Mac mini 上的同一份记忆。家里本机的 Agent 继续用 stdio，不受影响。

## 安全边界

- 只在 tailnet 里可达：后端仍只监听 127.0.0.1，由 `tailscale serve` 转成 `https://<机器名>.<tailnet 名>.ts.net`。**不要开 Funnel**（doctor 会判为未通过）。
- 每个请求都要带 `Authorization: Bearer <token>`。**每台远程设备、每种 Agent 用自己的设备令牌，只给 `mcp` 权限并带 `--reader`**：
  - 这个令牌只能访问 `/mcp`（和 stdio 桥接），调用 App 的 HTTP 接口（`/api/v1/…`、`/openapi.json`）一律返回 403。
  - 它仍然能通过 MCP 工具读写待办、摘要；长期记忆按 reader 勾选的区域限制，所以只放在你自己的设备上，存在环境变量里，不要写进会同步或提交的文件。
  - 怀疑泄露或者不再用这台电脑时，在 Mac 上只作废这一个：`uv run python -m scripts.tokens revoke 学校电脑`。下一个请求就会被拒绝（401），不用重启后端，iPhone 等其他设备不受影响。
  - 数据库里只保存令牌的 SHA-256 哈希；令牌本身只在创建时显示一次，丢了就作废再建一个。
- 不要把 `backend/.env` 里的 `ASSISTANT_TOKEN` 复制到别的设备：它有全部权限，而且不能单独作废（只能换掉，换了之后本机 Agent 也要重启）。
- 只有 `ASSISTANT_MCP_ALLOWED_HOSTS` 里列出的主机名能访问 `/mcp`（防 DNS 重绑定）。不支持通配符。
- 在 Tailscale 后台可以用 ACL 进一步限制只有哪几台设备能访问 Mac mini。

## Mac mini 上（一次）

1. 按 [mac-setup.md](mac-setup.md) 第 5 节配好 `tailscale serve`，记下 `https://<机器名>.<tailnet 名>.ts.net`。
2. 在 `backend/.env` 加一行（换成你的实际名字）：

   ```bash
   ASSISTANT_MCP_ALLOWED_HOSTS=mac-mini.tail1234.ts.net
   ```

   多个名字用逗号分隔。如果不用 serve、而是把 `ASSISTANT_HOST` 设成 Mac 的 Tailscale IP，用 `http://<IP 或 MagicDNS 名>:8765` 访问，也把对应的名字写进来（不写端口时任何端口都接受）。
3. 重启后端：`launchctl kickstart -k gui/$(id -u)/local.assistant`。
4. 给这台远程设备创建令牌（在 `backend` 目录执行；`--days` 可选，到期自动失效）：

   ```bash
   uv run python -m scripts.tokens create 学校电脑 --scope mcp --reader "Claude Code" --days 180
   ```

   输出里 `a24_` 开头的那一行就是令牌，只显示这一次。直接复制到 Windows 电脑上（下一节），不要发到聊天里。`uv run python -m scripts.tokens list` 可以看到每个令牌的权限、reader、开头几位、上次使用时间和到期时间（不显示令牌本身）。
5. 在 tailnet 里另一台设备上，用刚创建的令牌验证（`$ASSISTANT_TOKEN` 换成它；应返回 200 和 `"serverInfo"`；Host 不在列表里是 421，令牌不对或已作废是 401，令牌没有 `mcp` 权限是 403）：

   ```bash
   curl -s https://mac-mini.tail1234.ts.net/mcp \
     -H "Authorization: Bearer $ASSISTANT_TOKEN" \
     -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
   ```

## Windows 电脑上

1. 安装 Tailscale，用同一个账号登录，确认能打开 `https://mac-mini.tail1234.ts.net/api/v1/status`（没带令牌返回 401，说明连通了；带上这台电脑的令牌返回 403，说明它确实不能用 App 的接口）。
2. 把上一步创建的设备令牌放进用户环境变量 `ASSISTANT_TOKEN`（PowerShell：`[Environment]::SetEnvironmentVariable("ASSISTANT_TOKEN", "<a24_ 开头的令牌>", "User")`，重开终端生效）。变量名沿用 `ASSISTANT_TOKEN`，下面的配置都读它；里面放的是这台电脑自己的令牌，不是 Mac 的。
3. 接入 Agent（参数以两者的官方文档为准）：

   - **Claude Code**：

     ```powershell
     claude mcp add --transport http assistant https://mac-mini.tail1234.ts.net/mcp --header "Authorization: Bearer $env:ASSISTANT_TOKEN"
     ```

     这条命令会把 token 明文写进 Claude Code 的本机配置（`~/.claude.json`）。这台电脑只有你自己用时可以接受；否则改用项目里的 `.mcp.json`，在 headers 里写 `"Authorization": "Bearer ${ASSISTANT_TOKEN}"`，由 Claude Code 在运行时展开环境变量（以官方文档为准），并且不要提交这个文件以外的任何 token。
   - **Codex**（`~/.codex/config.toml`）：

     ```toml
     [mcp_servers.assistant]
     url = "https://mac-mini.tail1234.ts.net/mcp"
     bearer_token_env_var = "ASSISTANT_TOKEN"
     ```

4. 在 Agent 里列出工具，应看到 `messages_get_unprocessed`、`todos_add`、`memory_search` 等 11 个内置工具（knowledge_note_write 对普通令牌会拒绝），以及已启用插件的工具。

Codex 请另建一个带 `--reader Codex` 的令牌，不和 Claude Code 共用。旧无 reader 令牌显示「未限定」并保留全部记忆读取，建议重建。本机 stdio 也应在 MCP env 放各自设备令牌，不使用 `.env` 的主令牌；完整配置和磁盘访问边界见 [knowledge.md](knowledge.md)。

## 注意

- 远程 Agent 和家里的定时任务读写的是同一个数据库。不要让远程 Agent 去跑「提炼待办」这类会标记消息已处理的流程，除非你确定家里的任务没在跑；读写待办和长期记忆没有这个问题。
- Mac 离线（断电、断网）时远程连接会失败，Agent 会报 MCP 连接错误，不影响家里的数据。
