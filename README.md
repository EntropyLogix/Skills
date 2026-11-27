# Skills

This repository contains local skills for AI coding agents. Each skill is
organized by the platform it integrates with and includes its installer,
runtime code, and platform-specific instructions.

## Available skills

### Handoff

Handoff carries a compact, frozen snapshot of one coding session into another
session. It is useful when a task needs to continue in a fresh session without
loading the full source transcript.

The primary goal is to reduce the context size and token usage required to
continue work. Handoff keeps the parts that help the next session act quickly,
including recent decisions, pending work, selected messages, session metadata,
and relevant file paths. It also provides a clear checkpoint between sessions,
keeps the source session independent, and avoids forwarding tool output that
does not help with the next step.

| Platform | Implementation | Installation target |
| --- | --- | --- |
| Claude Code | [`Claude/Handoff`](Claude/Handoff/) | `~/.claude/skills/handoff/` |
| Codex | [`Codex/Handoff`](Codex/Handoff/) | `$HOME/.agents/skills/handoff/` |

Both implementations provide the same user-facing workflow:

1. Save the current session with `/handoff -save`.
2. Optionally attach a continuation instruction with `/handoff -save <instruction>`.
3. List available snapshots with `/handoff`.
4. Load a snapshot by selecting its list number with `/handoff N`.

Each session has one snapshot. Saving again replaces the previous snapshot for
that session. Snapshots are retained for 14 days and are marked as consumed
after they are loaded.

The snapshot contains selected conversation context and session metadata. Tool
outputs and long code blocks are reduced or omitted so that the next session
receives a focused context.

### Wakeup for Codex

[`Codex/Wakeup`](Codex/Wakeup/) arms one exact message for an active Codex
session and sends it after the session reaches a five-hour or weekly usage
limit and that limit resets. Use `$wakeup <message>` to arm it, `$wakeup` to
inspect it, and `$wakeup stop` to cancel it. Codex uses `$` to invoke installed
skills. The implementation uses the
exact session UUID and the official `codex queue --resume` command. It does not create,
resume, or modify Goals and does not create Handoff snapshots.

## Why the implementations are separate

Claude Code and Codex expose different skill discovery paths, hook
configuration files, transcript formats, and context-injection interfaces.
The repository therefore keeps one platform adapter for each product while
maintaining the same Handoff workflow and terminology in both implementations.

This structure keeps installation and runtime behavior explicit. Shared logic
can be extracted later when the platform implementations have a stable common
interface.

## Installation

### Claude Code

From the repository root, run:

```bash
./Claude/Handoff/install.sh
```

The installer copies the runtime into `~/.claude/skills/handoff/`, generates
the skill file from the template, and adds the `UserPromptSubmit` hook to
`~/.claude/settings.json`. An existing settings file is backed up as
`settings.json.bak-handoff`. Start a new Claude Code session after installing.

Set `CLAUDE_CONFIG_DIR` when Claude Code uses a different configuration
directory.

To remove the integration, run:

```bash
./Claude/Handoff/install.sh --uninstall
```

### Codex

From the repository root, run:

```bash
./Codex/Handoff/install.sh
```

The installer copies the runtime into `$HOME/.agents/skills/handoff/` and adds
the `UserPromptSubmit` hook to `$CODEX_HOME/hooks.json`. Open `/hooks` in Codex
and trust the new hook, then start a new session.

Set `CODEX_HOME` to use a different Codex configuration directory. Set
`CODEX_HANDOFF_SKILL_DIR` when the skill should be installed in a different
directory.

To remove the integration, run:

```bash
./Codex/Handoff/install.sh --uninstall
```

To install Wakeup for Codex, run:

```bash
./Codex/Wakeup/install.sh
```

This adds a separate `UserPromptSubmit` hook and starts a local watcher. Set
`CODEX_WAKEUP_SKILL_DIR` to use another installation directory. The watcher
must remain running while the Codex session waits for its usage limit to reset.

Remove Wakeup with:

```bash
./Codex/Wakeup/install.sh --uninstall
```

## Requirements

- Python 3.8 or newer
- Linux or macOS
- A local Claude Code or Codex installation with skills and local hooks enabled
- No third-party Python packages

The scripts use `fcntl` for state-file locking and should be used on a
Unix-like system.

## Repository layout

```text
Claude/Handoff/
├── handoff.py          # Transcript parser and snapshot manager
├── install.sh           # Claude Code installer and uninstaller
├── SKILL.md.template    # Claude Code skill template
└── README.md            # Claude Code documentation

Codex/Handoff/
├── handoff.py          # Transcript parser and snapshot manager
├── install.sh           # Codex installer and uninstaller
├── SKILL.md             # Codex skill definition
└── README.md            # Codex documentation

Codex/Wakeup/
├── wakeup.py            # Wakeup hook and reset watcher
├── install.sh           # Codex installer and uninstaller
├── SKILL.md             # Codex skill definition
└── README.md            # Codex Wakeup documentation
```

The platform directories contain the detailed implementation notes and
operational checks for each integration.

## Local data and privacy

Handoff stores snapshots and state in the platform configuration directory.
Snapshots can include selected prompt and response text, local paths, branch
names, and session metadata. They are intended to stay on the local machine
and are not uploaded by these scripts.

## License

This project is released under the [MIT License](LICENSE).
