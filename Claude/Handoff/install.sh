#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SKILL_DIR="$CLAUDE_DIR/skills/handoff"
SETTINGS="$CLAUDE_DIR/settings.json"
HOOK_CMD="python3 $SKILL_DIR/handoff.py hook-prompt"

usage() {
  echo "usage: $0 [--uninstall]"
  exit 1
}

edit_settings() {
  local mode="$1"
  if [ -f "$SETTINGS" ]; then
    cp "$SETTINGS" "$SETTINGS.bak-handoff"
    echo "settings backup: $SETTINGS.bak-handoff"
  fi
  python3 - "$SETTINGS" "$HOOK_CMD" "$mode" <<'EOF'
import json, os, sys
path, cmd, mode = sys.argv[1:4]
data = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
hooks = data.setdefault("hooks", {})
groups = hooks.get("UserPromptSubmit", [])
kept = []
for group in groups:
    inner = [h for h in group.get("hooks", []) if "handoff.py hook-prompt" not in h.get("command", "")]
    if inner:
        group["hooks"] = inner
        kept.append(group)
if mode == "install":
    kept.append({"hooks": [{"type": "command", "command": cmd, "timeout": 60}]})
if kept:
    hooks["UserPromptSubmit"] = kept
else:
    hooks.pop("UserPromptSubmit", None)
if not hooks:
    data.pop("hooks")
with open(path, "w", encoding="utf-8") as f:
    f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
EOF
}

case "${1:-}" in
  "")
    command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
    mkdir -p "$SKILL_DIR"
    install -m 0755 "$SRC_DIR/handoff.py" "$SKILL_DIR/handoff.py"
    sed "s#__SKILL_DIR__#$SKILL_DIR#g" "$SRC_DIR/SKILL.md.template" > "$SKILL_DIR/SKILL.md"
    edit_settings install
    echo "installed skill: $SKILL_DIR"
    echo "UserPromptSubmit hook: $HOOK_CMD"
    echo "available in new sessions"
    ;;
  --uninstall)
    edit_settings uninstall
    rm -rf "$SKILL_DIR"
    echo "removed skill and hook; saved handoffs remain in $CLAUDE_DIR/handoffs"
    ;;
  *)
    usage
    ;;
esac
