---
name: handoff
description: Saves a compact, frozen Codex session snapshot and loads it into a new session by selecting its list number.
---

`UserPromptSubmit` intercepts `/handoff --save [instruction]` and `/handoff` before model invocation. On save, the script reads the transcript, omits tool output, and writes a frozen snapshot. Later changes in the source session do not change it.

When `/handoff N` is used, the hook adds the selected snapshot as context to the current prompt. Any other argument shows the list again and names the rejected argument. If the snapshot contains a continuation instruction, execute it immediately. Otherwise, summarize the session in 2–4 sentences and wait for the next instruction. Always reply in the language the user used in the previous session.

`/resume` remains available for restoring the full session. Handoff provides a focused context for continuing work.
