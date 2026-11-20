#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
SKILL_DIR="${CODEX_HANDOFF_SKILL_DIR:-$HOME/.agents/skills/handoff}"
LEGACY_SKILL_DIR="$HOME/.agents/skills/codex-handoff"
HOOKS="$CODEX_DIR/hooks.json"
HOOK_CMD="python3 $SKILL_DIR/handoff.py hook-prompt"

usage() {
  echo "użycie: $0 [--uninstall]"
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
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
hooks = data.setdefault("hooks", {})
groups = hooks.get("UserPromptSubmit", [])
kept = []
for group in groups:
    inner = [item for item in group.get("hooks", []) if item.get("command") != command and "/codex-handoff/" not in item.get("command", "")]
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
with open(path, "w", encoding="utf-8") as f:
    f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
PY
}

case "${1:-}" in
  "")
    command -v python3 >/dev/null || { echo "brak python3"; exit 1; }
    mkdir -p "$SKILL_DIR"
    install -m 0755 "$SRC_DIR/handoff.py" "$SKILL_DIR/handoff.py"
    sed "s#__SKILL_DIR__#$SKILL_DIR#g" "$SRC_DIR/SKILL.md.template" > "$SKILL_DIR/SKILL.md"
    edit_hooks install
    python3 - "$LEGACY_SKILL_DIR" "$SKILL_DIR" <<'PY'
import shutil
import sys
from pathlib import Path

legacy, current = map(Path, sys.argv[1:])
if legacy != current and legacy.exists():
    shutil.rmtree(legacy)
PY
    echo "zainstalowano skill: $SKILL_DIR"
    echo "hook UserPromptSubmit: $HOOK_CMD"
    echo "otwórz /hooks w Codex, aby zaufać nowemu hookowi"
    ;;
  --uninstall)
    edit_hooks uninstall
    python3 - "$SKILL_DIR" "$LEGACY_SKILL_DIR" <<'PY'
import shutil
import sys
from pathlib import Path

for path in map(Path, sys.argv[1:]):
    if path.exists():
        shutil.rmtree(path)
PY
    echo "usunięto skill i hook; zamrożone handovery zostają w $CODEX_DIR/handoffs"
    ;;
  *)
    usage
    ;;
esac
