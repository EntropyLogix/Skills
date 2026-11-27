# Wakeup for Codex

Wakeup arms one exact message for the current Codex session and sends it after
the session reaches a five-hour or weekly usage limit and that limit resets.
The current session can keep working after the command is entered.

## Commands

```text
$wakeup Work on B1234
$wakeup
$wakeup stop
```

`$wakeup <message>` stores the message against the current session UUID. Codex
uses `$` for installed skills. The watcher waits for a reached limit, reads the
reset time from the session transcript, and sends the message with:

```bash
codex queue --resume --thread <exact-session-uuid> --message "<message>"
```

It never selects a session by recency, title, directory, or `--last`. A
message is sent at most once. If the watcher cannot prove that it can send
safely, it leaves the entry pending or records an error.

The watcher remembers a reached limit even after the transcript reports the
new post-reset usage window. If the user has already submitted a newer message
after the reset, it marks the wakeup as superseded and skips delivery to avoid
duplicating manual work.

The message may be any text, including `/goal resume`. Wakeup does not create,
resume, or modify Goals. It does not create Handoff snapshots.

## Installation

From the repository root, run:

```bash
./Codex/Wakeup/install.sh
```

The installer copies the skill and watcher to `~/.agents/skills/wakeup/`, adds
a `UserPromptSubmit` hook to `$CODEX_HOME/hooks.json`, and starts one local
watcher process. Open `/hooks` in Codex and trust the hook.

Set `CODEX_HOME` to use another Codex configuration directory. Set
`CODEX_WAKEUP_SKILL_DIR` to use another installation directory.

The watcher can also be started manually:

```bash
python3 ~/.agents/skills/wakeup/wakeup.py watch
```

The watcher is a local process. It is started by the installer and remains
available while the machine and Codex session stay running. Configure it with
your operating system's user service manager if it should start after login.

Stop the installed watcher with:

```bash
python3 ~/.agents/skills/wakeup/wakeup.py stop-watcher
```

Remove the integration with:

```bash
./Codex/Wakeup/install.sh --uninstall
```

Pending state remains in `$CODEX_HOME/wakeup/` when the skill is removed.

## Requirements

- Codex with local hooks enabled
- Python 3.8 or newer
- `codex` available on `PATH`
- Linux or macOS

The watcher reads the local Codex transcript and uses the official `codex
queue --resume` command. It does not access credentials or send network
requests of its own.

The standard implementation only handles a usage-limit reset. It does not
retry unknown failures, permissions, approvals, cancelled turns, or invalid
requests. If `codex queue` fails, the entry becomes `error` and no duplicate
retry is attempted for an unknown or permanent error.

Known transient errors such as `capacity`, overload, rate limits, 429, and
temporary 5xx responses are retried up to four total attempts with delays of
1, 5, and 15 minutes. A timeout with no clear rejection is treated as
`unknown`, because the original message may already have been delivered.

The target session should remain loaded in Codex while it waits. If Codex is
restarted, open the target conversation before relying on the queued message.

If `$wakeup` produces no command result, restart or resume the target session
after installation and trust the hook from `/hooks`. The skill must not cause
the model to infer or begin another task when the command hook is unavailable.
