# Handoff for Claude Code

This skill saves a compact snapshot of a Claude Code session and loads it into
a new session. The snapshot is created when it is saved, so later work in the
source session does not change it.

Its main purpose is to reduce the context size and token usage needed to
continue a task. The snapshot preserves the recent state and selected session
metadata while giving the next session a fresh, explicit starting point.

## Commands

| Command | Result |
| --- | --- |
| `/handoff --save` | Save the current session. |
| `/handoff --save <instruction>` | Save the session with a continuation instruction. |
| `/handoff` | List available snapshots. |
| `/handoff N` | Load snapshot `N` from the last list. |

With the hook enabled, saving and listing are handled before Claude Code calls
the model. Loading a snapshot supplies the selected context to the session.

Each session has one snapshot. Saving again replaces the previous one. The list
shows every unloaded snapshot with its save date; a snapshot disappears from the
list after it is loaded.

## Installation

From the repository root, run:

```bash
./Claude/Handoff/install.sh
```

The installer:

- copies `handoff.py` to `~/.claude/skills/handoff/`;
- creates `SKILL.md` from `SKILL.md.template`;
- adds the `UserPromptSubmit` hook to `~/.claude/settings.json`;
- keeps a backup at `~/.claude/settings.json.bak-handoff` when that file exists.

Start a new Claude Code session after installing. Set `CLAUDE_CONFIG_DIR` to
use a different Claude configuration directory.

Remove the integration with:

```bash
./Claude/Handoff/install.sh --uninstall
```

The installer removes the skill and hook. Existing snapshots remain in the
configuration directory.

## Requirements

- Claude Code with skills and local hooks enabled
- Python 3.8 or newer
- Linux or macOS
- No third-party Python packages

## Storage and snapshot contents

Snapshots and state are stored in `~/.claude/handoffs/`, or in the equivalent
directory under `CLAUDE_CONFIG_DIR`. The script reads local Claude Code
transcripts from `~/.claude/projects/` and does not upload them.

A snapshot can include selected summaries, recent exchanges, session metadata,
document paths, changed file paths, and related saved handoffs. Tool output
and long code blocks are reduced or omitted.

## Verification

1. In a session with some conversation, run `/handoff --save`.
2. Start a new session and run `/handoff`.
3. Load the listed snapshot with `/handoff 1`.

The hook should handle the save and list operations without a model response.
The loaded context should identify the source session and its recent state.

## Files

| File | Purpose |
| --- | --- |
| `handoff.py` | Parses transcripts and manages snapshots. |
| `SKILL.md.template` | Source template for the installed skill. |
| `install.sh` | Installs and removes the skill and hook. |

The parser relies on Claude Code's local transcript format. Changes to that
format may require parser updates.
