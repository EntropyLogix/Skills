---
name: wakeup
description: Arms an exact message for the current Codex session to send after the session reaches a usage limit and that limit resets.
---

Codex invokes this skill with `$wakeup`. Use these forms:

- `$wakeup <message>` arms or replaces the message for the current session.
- `$wakeup` shows the current wakeup state.
- `$wakeup stop` cancels the pending wakeup.

The hook handles these commands without invoking the model. The watcher sends
the exact message through `codex queue` after it observes a usage-limit reset
for the saved session UUID. This skill does not create, resume, or modify
Codex Goals and does not create Handoff snapshots. It retries only named
transient queue failures with a bounded backoff. Unknown failures stop safely.

Treat `$wakeup` as a command only. Do not infer a coding task or continue other
work when it is invoked. If no command result appears, report that the hook is
not loaded or trusted and wait for the user to restart the session and trust it.
