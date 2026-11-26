#!/usr/bin/env python3
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone


CODEX_DIR = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
WAKEUP_DIR = os.path.join(CODEX_DIR, "wakeup")
STATE_PATH = os.path.join(WAKEUP_DIR, "state.json")
LOCK_PATH = os.path.join(WAKEUP_DIR, "state.lock")
PID_PATH = os.path.join(WAKEUP_DIR, "watcher.pid")
LOG_PATH = os.path.join(WAKEUP_DIR, "watcher.log")
TAIL_BYTES = 2 * 1024 * 1024
POLL_SECONDS = 30
MESSAGE_LIMIT = 8000
WAKEUP_RE = re.compile(r"^\s*/wakeup(?:\s+(?P<argument>[\s\S]*?))?\s*$", re.IGNORECASE)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_dir():
    os.makedirs(WAKEUP_DIR, exist_ok=True)


def log_error(error):
    ensure_dir()
    with open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(f"{now_iso()} {error}\n")


def with_state(mutate):
    ensure_dir()
    with open(LOCK_PATH, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = {"entries": {}}
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH, encoding="utf-8") as handle:
                    state = json.load(handle)
            except (OSError, json.JSONDecodeError):
                state = {"entries": {}}
        state.setdefault("entries", {})
        result = mutate(state)
        temporary = STATE_PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, STATE_PATH)
        return result


def read_state():
    if not os.path.exists(STATE_PATH):
        return {"entries": {}}
    try:
        with open(STATE_PATH, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"entries": {}}
    state.setdefault("entries", {})
    return state


def format_time(epoch):
    if not epoch:
        return "unknown"
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def entry_text(entry):
    status = entry.get("status", "unknown")
    lines = [
        f"Wakeup status: {status}",
        f"Session: {entry.get('session_id', 'unknown')}",
        f"Message: {entry.get('message', '')}",
        f"Armed: {entry.get('armed_at', 'unknown')}",
    ]
    if entry.get("reset_at"):
        lines.append(f"Observed reset: {format_time(entry['reset_at'])}")
    if entry.get("sent_at"):
        lines.append(f"Sent: {entry['sent_at']}")
    if entry.get("last_error"):
        lines.append(f"Last error: {entry['last_error']}")
    return "\n".join(lines)


def entry_for(session_id):
    return read_state()["entries"].get(session_id)


def arm(session_id, transcript_path, cwd, message):
    timestamp = now_iso()

    def mutate(state):
        state["entries"][session_id] = {
            "session_id": session_id,
            "transcript_path": transcript_path,
            "cwd": cwd,
            "message": message,
            "status": "armed",
            "armed_at": timestamp,
            "reset_at": None,
            "sent_at": None,
            "attempts": 0,
            "last_error": None,
        }

    with_state(mutate)
    return f"Wakeup armed for session {session_id}. The message will be sent after a usage-limit reset.\n\n{message}"


def cancel(session_id):
    def mutate(state):
        entry = state["entries"].get(session_id)
        if not entry:
            return "No wakeup is armed for this session."
        if entry.get("status") == "sending":
            return "Wakeup is already being sent and cannot be cancelled safely."
        if entry.get("status") == "sent":
            return "The wakeup message has already been sent."
        entry["status"] = "cancelled"
        entry["cancelled_at"] = now_iso()
        return "Wakeup cancelled."

    return with_state(mutate)


def tail_lines(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - TAIL_BYTES))
            data = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = data.splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]
    return lines


def latest_rate_limits(path):
    for line in reversed(tail_lines(path)):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") or {}
        limits = payload.get("rate_limits")
        if isinstance(limits, dict):
            return limits
    return None


def reached_window(window):
    return isinstance(window, dict) and (
        window.get("used_percent", 0) >= 100 or window.get("reached") is True
    )


def reset_for_limits(limits):
    if not limits:
        return None
    windows = []
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if reached_window(window) and isinstance(window.get("resets_at"), (int, float)):
            windows.append(float(window["resets_at"]))
    if limits.get("rate_limit_reached_type") and not windows:
        for key in ("primary", "secondary"):
            window = limits.get(key)
            if isinstance(window, dict) and isinstance(window.get("resets_at"), (int, float)):
                windows.append(float(window["resets_at"]))
    return min(windows) if windows else None


def mark_waiting(session_id, reset_at):
    def mutate(state):
        entry = state["entries"].get(session_id)
        if not entry or entry.get("status") in ("cancelled", "sent"):
            return
        entry["status"] = "waiting"
        entry["reset_at"] = reset_at

    with_state(mutate)


def claim_for_send(session_id):
    def mutate(state):
        entry = state["entries"].get(session_id)
        if not entry or entry.get("status") not in ("armed", "waiting"):
            return None
        entry["status"] = "sending"
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        return dict(entry)

    return with_state(mutate)


def finish_send(session_id, success, error=None):
    def mutate(state):
        entry = state["entries"].get(session_id)
        if not entry:
            return
        if success:
            entry["status"] = "sent"
            entry["sent_at"] = now_iso()
            entry["last_error"] = None
        else:
            entry["status"] = "error"
            entry["last_error"] = error or "codex queue failed"

    with_state(mutate)


def send_message(entry):
    command = [
        os.environ.get("CODEX_BIN", "codex"),
        "queue",
        "--thread",
        entry["session_id"],
        "--message",
        entry["message"],
    ]
    try:
        result = subprocess.run(
            command,
            cwd=entry.get("cwd") or None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout).strip()
    return False, detail or f"codex queue exited with status {result.returncode}"


def process_entry(entry, dry_run=False):
    if entry.get("status") not in ("armed", "waiting"):
        return
    limits = latest_rate_limits(entry.get("transcript_path", ""))
    reset_at = reset_for_limits(limits)
    if reset_at is None:
        return
    if time.time() < reset_at:
        mark_waiting(entry["session_id"], reset_at)
        return
    if dry_run:
        return
    claimed = claim_for_send(entry["session_id"])
    if not claimed:
        return
    success, error = send_message(claimed)
    finish_send(claimed["session_id"], success, error)


def process_once(dry_run=False):
    for entry in list(read_state()["entries"].values()):
        process_entry(entry, dry_run=dry_run)


def run_watcher(once=False, dry_run=False):
    ensure_dir()
    while True:
        try:
            process_once(dry_run=dry_run)
        except Exception as error:
            log_error(error)
        if once:
            return
        time.sleep(POLL_SECONDS)


def watcher_pid():
    try:
        with open(PID_PATH, encoding="utf-8") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def process_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def run_daemon():
    ensure_dir()
    current = watcher_pid()
    if current and current != os.getpid() and process_alive(current):
        return
    with open(PID_PATH, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    try:
        run_watcher()
    finally:
        try:
            os.unlink(PID_PATH)
        except OSError:
            pass


def usage():
    return "usage: wakeup.py hook-prompt | watch [--once] [--dry-run] | status SESSION_ID | cancel SESSION_ID | stop-watcher"


def hook_prompt():
    data = json.load(sys.stdin)
    prompt = data.get("prompt") or ""
    match = WAKEUP_RE.match(prompt)
    if not match:
        return
    session_id = data.get("session_id")
    if not session_id:
        print(json.dumps({"decision": "block", "reason": "No session ID was provided by Codex."}))
        return
    argument = (match.group("argument") or "").strip()
    if argument.lower() == "stop":
        message = cancel(session_id)
    elif not argument:
        entry = entry_for(session_id)
        message = entry_text(entry) if entry else "No wakeup is armed for this session."
    else:
        if len(argument) > MESSAGE_LIMIT:
            message = f"Wakeup message is too long. The limit is {MESSAGE_LIMIT} characters."
        else:
            message = arm(session_id, data.get("transcript_path"), data.get("cwd"), argument)
    print(json.dumps({"decision": "block", "reason": "No model call.\n\n" + message}, ensure_ascii=False))


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit(usage())
    command = argv[0]
    if command == "hook-prompt":
        return hook_prompt()
    if command == "watch":
        return run_daemon() if "--once" not in argv[1:] else run_watcher(once=True, dry_run="--dry-run" in argv[1:])
    if command == "status" and len(argv) == 2:
        entry = entry_for(argv[1])
        print(entry_text(entry) if entry else "No wakeup is armed for this session.")
        return
    if command == "cancel" and len(argv) == 2:
        print(cancel(argv[1]))
        return
    if command == "stop-watcher":
        pid = watcher_pid()
        if pid and process_alive(pid):
            os.kill(pid, signal.SIGTERM)
            print(f"Stopped wakeup watcher {pid}.")
        else:
            print("Wakeup watcher is not running.")
        return
    sys.exit(usage())


if __name__ == "__main__":
    main()
