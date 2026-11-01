#!/bin/bash
# One-command backend setup for the Mac mini. Safe to run again at any time:
# installed tools are detected and skipped, existing .env / LaunchAgent / tailscale serve
# settings are never overwritten. See docs/mac-setup.md for the manual steps around it.
set -euo pipefail

LABEL="local.assistant"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
INSTALL_APPS=1
SERVE=0
RESTART=0
REPLACE_PLIST=0
SKIP_HELPER=0

usage() {
  cat <<'USAGE'
用法：bash backend/scripts/install_mac.sh [选项]

  --no-apps        不安装 Claude Code、Codex、Tailscale（只装 Homebrew、uv 和 Python 依赖）
  --no-helper      不编译 apple-sync、screen-helper、doc-text 和 photo-export
  --serve          Tailscale 没有任何 serve 配置时，用 tailscale serve 把 HTTPS 转发到后端
  --restart        服务已在运行时重启它（更新代码后用）
  --replace-plist  LaunchAgent 和新生成的不同时替换它（先备份旧文件）
  -h, --help       显示本帮助

不带选项可以重复运行：已装的跳过，已有的 .env、LaunchAgent 和 serve 配置不覆盖。
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --no-apps) INSTALL_APPS=0 ;;
    --no-helper) SKIP_HELPER=1 ;;
    --serve) SERVE=1 ;;
    --restart) RESTART=1 ;;
    --replace-plist) REPLACE_PLIST=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "未知选项：$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

step() { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }

if [ "$(uname -s)" != "Darwin" ]; then
  echo "install_mac.sh 只能在 macOS 上运行；Linux/WSL 请按 backend/README.md 操作。" >&2
  exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
  echo "请用登录桌面的普通用户运行，不要用 sudo：LaunchAgent 和系统权限都属于这个用户。" >&2
  exit 1
fi

step "Xcode 命令行工具"
if xcode-select -p >/dev/null 2>&1; then
  info "已安装：$(xcode-select -p)"
else
  xcode-select --install >/dev/null 2>&1 || true
  echo "已弹出安装窗口。装完命令行工具后重新运行本脚本。" >&2
  exit 1
fi

step "Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$candidate" ]; then
      eval "$("$candidate" shellenv)"
      break
    fi
  done
fi
if command -v brew >/dev/null 2>&1; then
  info "已安装：$(brew --version | head -n 1)"
else
  info "用官方脚本安装 Homebrew，过程中需要输入一次登录密码"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
if ! grep -qs "brew shellenv" "$HOME/.zprofile"; then
  # shellcheck disable=SC2016 # the $( ) must stay literal in ~/.zprofile
  printf '\neval "$(%s shellenv)"\n' "$(command -v brew)" >>"$HOME/.zprofile"
  info "已把 brew shellenv 加到 ~/.zprofile"
fi

step "uv"
if [ -x "$HOME/.local/bin/uv" ] && ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$PATH"
fi
if command -v uv >/dev/null 2>&1; then
  info "已安装：$(uv --version)"
else
  brew install uv
fi

if [ "$INSTALL_APPS" -eq 1 ]; then
  step "Claude Code、Codex、Tailscale"
  if command -v claude >/dev/null 2>&1 || [ -x "$HOME/.local/bin/claude" ]; then
    info "Claude Code 已安装"
  else
    brew install --cask claude-code
  fi
  if command -v codex >/dev/null 2>&1; then
    info "Codex 已安装"
  else
    brew install --cask codex
  fi
  if command -v tailscale >/dev/null 2>&1 || [ -d /Applications/Tailscale.app ]; then
    info "Tailscale 已安装"
  else
    info "安装 Tailscale（独立版 pkg，需要输入一次登录密码）"
    brew install --cask tailscale-app
  fi
fi

step "Python 依赖（uv sync --locked）"
cd "$BACKEND"
if [ -x .venv/bin/python ]; then
  # Keep the existing interpreter: a new one would lose the permissions granted to it.
  uv sync --locked
else
  uv sync --locked --python 3.12
fi
info "Python：$(.venv/bin/python --version)，实际路径 $(.venv/bin/python -c 'import os,sys; print(os.path.realpath(sys.executable))')"

step "本机配置 backend/.env"
.venv/bin/python -m scripts.mac_setup env >/dev/null

HELPERS="$HOME/Library/Application Support/AemeathBot/bin"

# Builds a Swift helper, signs it with a fixed identifier and copies it to $HELPERS. macOS
# ties Screen Recording, Reminders and Calendar grants to that exact binary, so an unchanged
# build is not copied again, and the folder stays the same whichever checkout installs it.
install_helper() {
  local name="$1" identifier="$2" built
  (
    # -S: no debug information, so the binary does not depend on the folder it is built in
    # and another checkout of the same code builds the very same file.
    cd "helpers/$name" &&
      swift build -c release -Xlinker -sectcreate -Xlinker __TEXT \
        -Xlinker __info_plist -Xlinker "$PWD/Info.plist" -Xlinker -S >/dev/null
  ) || return 1
  built="$BACKEND/helpers/$name/.build/release/$name"
  codesign -f -s - -i "$identifier" "$built" 2>/dev/null || return 1
  mkdir -p "$HELPERS"
  if cmp -s "$built" "$HELPERS/$name"; then
    info "$name 没有变化：$HELPERS/$name"
  else
    # A new file, not an overwrite: macOS keeps the old code signature of a file it has run
    # and kills the process when the contents under it change.
    rm -f "$HELPERS/$name"
    cp "$built" "$HELPERS/$name"
    info "已安装：$HELPERS/$name（换了新版本，之前给它的系统权限要重新给一次）"
  fi
}

if [ "$SKIP_HELPER" -eq 0 ]; then
  step "EventKit 助手 apple-sync"
  if install_helper apple-sync local.assistant.apple-sync; then
    info "仍是假提醒事项模式；按 docs/mac-setup.md 授权并验证后再切到 real"
  else
    warn "apple-sync 编译失败，其余步骤继续；可以稍后手动重试"
  fi
  step "屏幕读取助手 screen-helper（QQ、微信）"
  if install_helper screen-helper local.assistant.screen-helper; then
    info "在 系统设置 → 隐私与安全性 → 屏幕录制 里打开它；只有关闭预览模式时才要辅助功能"
  else
    warn "screen-helper 编译失败，其余步骤继续；可以稍后手动重试"
  fi
  step "资料文字提取 doc-text（PDF、图片）"
  if install_helper doc-text local.assistant.doc-text; then
    info "只读资料，不需要屏幕录制等权限"
  else
    warn "doc-text 编译失败，其余步骤继续；可以稍后手动重试"
  fi
  step "课堂照片只读导出 photo-export"
  if install_helper photo-export local.assistant.photo-export; then
    info "先打开 iCloud 照片，再在桌面终端运行 photo-export authorize 并允许访问照片"
  else
    warn "photo-export 编译失败，其余步骤继续；可以稍后手动重试"
  fi
fi

step "launchd 常驻服务（$LABEL）"
args=(launchd --target "$PLIST")
if [ "$REPLACE_PLIST" -eq 1 ]; then
  args+=(--replace)
fi
plist_status="$(.venv/bin/python -m scripts.mac_setup "${args[@]}")"
DOMAIN="gui/$(id -u)"
loaded=0
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  loaded=1
fi

bootstrap() {
  for _ in 1 2 3 4 5; do
    if launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  launchctl bootstrap "$DOMAIN" "$PLIST"
}

case "$plist_status" in
  created)
    bootstrap
    info "已生成并注册 $PLIST"
    ;;
  replaced)
    if [ "$loaded" -eq 1 ]; then
      launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    fi
    bootstrap
    info "已替换并重新注册 $PLIST"
    ;;
  unchanged | differs)
    if [ "$loaded" -eq 0 ]; then
      bootstrap
      info "已注册 $PLIST"
    elif [ "$RESTART" -eq 1 ]; then
      launchctl kickstart -k "$DOMAIN/$LABEL"
      info "已重启服务"
    else
      info "服务已注册，保持不动（更新代码后加 --restart）"
    fi
    ;;
  *)
    warn "未知的 LaunchAgent 状态：$plist_status"
    ;;
esac

PORT="$(.venv/bin/python -m scripts.mac_setup port 2>/dev/null || echo 8765)"
if [ "$SERVE" -eq 1 ]; then
  step "tailscale serve（HTTPS → 127.0.0.1:$PORT）"
  TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
  if [ ! -x "$TS" ]; then
    warn "没有找到 tailscale，跳过"
  elif "$TS" serve status --json 2>/dev/null | grep -q '"Web"'; then
    info "已有 serve 配置，不覆盖：$("$TS" serve status 2>/dev/null | head -n 3 | tr '\n' ' ')"
  else
    # First use may print an admin-console link to enable HTTPS certificates and wait for it.
    "$TS" serve --bg --https=443 "http://127.0.0.1:$PORT"
  fi
fi

step "等待后端启动"
(.venv/bin/python -m scripts.mac_setup wait --timeout 20 2>/dev/null || echo "unavailable: .env 无效") | sed 's/^/    /'

step "环境自检（scripts/doctor.py）"
set +e
.venv/bin/python scripts/doctor.py
code=$?
set -e

cat <<EOF

还需要人工完成的事（脚本不会替你登录或点开系统设置）：
  - claude auth login        # 用 Claude 订阅账号登录
  - codex login              # 用 ChatGPT 账号登录
  - 打开 Tailscale 登录同一个 tailnet；然后 bash backend/scripts/install_mac.sh --serve
  - 按 docs/mac-setup.md 逐项完成系统设置和权限，再运行 .venv/bin/python scripts/doctor.py
EOF
exit "$code"
