# Handoff for Codex

This skill saves a compact, frozen snapshot of a Codex session and loads it
into a new session. It is intended for passing focused context between local
sessions while keeping the full transcript available through Codex's native
resume flow.

Its main purpose is to reduce the context size and token usage needed to
continue a task. The snapshot preserves recent work and selected metadata,
while a new session starts from a clear checkpoint instead of the full
transcript.

## Commands

| Command | Result |
| --- | --- |
| `/handoff -save` | Save the current session. |
| `/handoff -save <instruction>` | Save the session with a continuation instruction. |
| `/handoff` | List available snapshots. |
| `/handoff N` | Load snapshot `N` from the last list. |

Each session has one snapshot. Saving again replaces the previous one. A
snapshot is available for 14 days and is marked as consumed after it is loaded.

## Installation

From the repository root, run:

```bash
./Codex/Handoff/install.sh
```

The installer copies the skill to `~/.agents/skills/handoff/` and adds the
`UserPromptSubmit` hook to `$CODEX_HOME/hooks.json`. When `CODEX_HOME` is not
set, the hook state is stored under `~/.codex/`.

Open `/hooks` in Codex and trust the new hook. Start a new session after
installing.

Set `CODEX_HOME` to use another Codex configuration directory. Set
`CODEX_HANDOFF_SKILL_DIR` to use another skill installation directory.

Remove the integration with:

```bash
./Codex/Handoff/install.sh --uninstall
```

## Requirements

- Codex with local hooks enabled
- Python 3.8 or newer
- Linux or macOS
- No third-party Python packages

## Storage and snapshot contents

Snapshots and state are stored in `$CODEX_HOME/handoffs/`, or in
`~/.codex/handoffs/` when `CODEX_HOME` is not set. The script keeps the
snapshot local.

A snapshot includes recent user and Codex messages, session metadata, paths
from applied patches, and the number of `exec` calls. Tool output and long
code blocks are reduced or omitted.

## Verification

1. In a session with some conversation, run `/handoff -save`.
2. Start a new session and run `/handoff`.
3. Load the listed snapshot with `/handoff 1`.

The hook should handle saving and listing before model invocation. Loading adds
the selected snapshot as context to the current session.

## Files

| File | Purpose |
| --- | --- |
| `handoff.py` | Parses transcripts and manages snapshots. |
| `SKILL.md` | Codex skill definition. |
| `SKILL.md.template` | Source copy of the skill definition. |
| `install.sh` | Installs and removes the skill and hook. |

The parser relies on Codex's local transcript format. Changes to that format
may require parser updates.
