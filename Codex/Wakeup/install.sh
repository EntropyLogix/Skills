#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
SKILL_DIR="${CODEX_WAKEUP_SKILL_DIR:-$HOME/.agents/skills/wakeup}"
HOOKS="$CODEX_DIR/hooks.json"
HOOK_CMD="python3 $SKILL_DIR/wakeup.py hook-prompt"

usage() {
  echo "usage: $0 [--uninstall]"
  exit 1
}

edit_hooks() {
  local mode="$1"
  mkdir -p "$CODEX_DIR"
  python3 - "$HOOKS" "$HOOK_CMD" "$mode" <<'PY'
import json
import os
import sys

path, command, mode = sys.argv[1:]
data = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
hooks = data.setdefault("hooks", {})
groups = hooks.get("UserPromptSubmit", [])
kept = []
for group in groups:
    inner = [item for item in group.get("hooks", []) if "/wakeup.py hook-prompt" not in item.get("command", "")]
    if inner:
        group["hooks"] = inner
        kept.append(group)
if mode == "install":
    kept.append({"hooks": [{"type": "command", "command": command, "timeout": 60}]})
if kept:
    hooks["UserPromptSubmit"] = kept
else:
    hooks.pop("UserPromptSubmit", None)
if not hooks:
    data.pop("hooks", None)
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2, ensure_ascii=False)
    handle.write("\n")
PY
}

start_watcher() {
  mkdir -p "$CODEX_DIR/wakeup"
  local pid_file="$CODEX_DIR/wakeup/watcher.pid"
  if [ -f "$pid_file" ]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      return
    fi
    rm -f "$pid_file"
  fi
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup python3 "$SKILL_DIR/wakeup.py" watch >> "$CODEX_DIR/wakeup/watcher.log" 2>&1 < /dev/null &
  else
    nohup python3 "$SKILL_DIR/wakeup.py" watch >> "$CODEX_DIR/wakeup/watcher.log" 2>&1 < /dev/null &
  fi
}

stop_watcher_process() {
  local pid_file="$CODEX_DIR/wakeup/watcher.pid"
  if [ ! -f "$pid_file" ]; then
    return
  fi
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    local attempts=0
    while [ "$attempts" -lt 20 ] && kill -0 "$pid" 2>/dev/null; do
      sleep 0.1
      attempts=$((attempts + 1))
    done
  fi
  rm -f "$pid_file"
}

restart_watcher() {
  stop_watcher_process
  start_watcher
}

case "${1:-}" in
  "")
    command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
    command -v codex >/dev/null || { echo "codex is required on PATH"; exit 1; }
    mkdir -p "$SKILL_DIR"
    install -m 0755 "$SRC_DIR/wakeup.py" "$SKILL_DIR/wakeup.py"
    install -m 0644 "$SRC_DIR/SKILL.md" "$SKILL_DIR/SKILL.md"
    edit_hooks install
    restart_watcher
    echo "installed wakeup skill: $SKILL_DIR"
    echo "UserPromptSubmit hook: $HOOK_CMD"
    echo "wakeup watcher: $CODEX_DIR/wakeup/watcher.log"
    echo "open /hooks in Codex to trust the new hook"
    ;;
  --uninstall)
    stop_watcher_process
    edit_hooks uninstall
    rm -rf "$SKILL_DIR"
    echo "removed wakeup skill and hook; state remains in $CODEX_DIR/wakeup"
    ;;
  *)
    usage
    ;;
esac
