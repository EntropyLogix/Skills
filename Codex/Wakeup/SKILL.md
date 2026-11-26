---
name: wakeup
description: Arms an exact message for the current Codex session to send after the session reaches a usage limit and that limit resets.
---

Use these commands:

- `/wakeup <message>` arms or replaces the message for the current session.
- `/wakeup` shows the current wakeup state.
- `/wakeup stop` cancels the pending wakeup.

The hook handles these commands without invoking the model. The watcher sends
the exact message through `codex queue` after it observes a usage-limit reset
for the saved session UUID. This skill does not create, resume, or modify
Codex Goals and does not create Handoff snapshots.
