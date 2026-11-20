#!/usr/bin/env python3
import fcntl
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

CODEX_DIR = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
SESSIONS_DIR = os.path.join(CODEX_DIR, "sessions")
INDEX_PATH = os.path.join(CODEX_DIR, "session_index.jsonl")
HANDOFF_DIR = os.path.join(CODEX_DIR, "handoffs")
STATE_PATH = os.path.join(HANDOFF_DIR, "state.json")
LOCK_PATH = os.path.join(HANDOFF_DIR, "state.lock")

SAVE_RE = re.compile(r"^\s*/handoff\s+-save\b:?\s*(?P<note>.*?)\s*$", re.IGNORECASE | re.DOTALL)
LIST_RE = re.compile(r"^\s*/handoff\s*$", re.IGNORECASE)
HANDOFF_RE = re.compile(r"^\s*/handoff(?:\s|$)", re.IGNORECASE)
SESSION_RE = re.compile(r"-([0-9a-f]{8}-[0-9a-f-]{27,})\.jsonl$", re.IGNORECASE)
FENCE_RE = re.compile(r"```[^\n]*\n([\s\S]*?)```")
TERM_RE = re.compile(r"\b[A-Za-z]{1,8}-?\d{2,}\b")

LIST_LIMIT = 8
PIN_DAYS = 14
EXCHANGES = 4
PROMPT_CAP = 1500
REPLY_CAP_LAST = 6000
REPLY_CAP_EARLIER = 1200
CODE_KEEP_LINES = 6
FILES_CAP = 40
RELATED_CAP = 8


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def with_state(mutate):
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    with open(LOCK_PATH, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = {"pins": {}, "lists": {}}
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH, encoding="utf-8") as f:
                    state = json.load(f)
            except (OSError, json.JSONDecodeError):
                state = {"pins": {}, "lists": {}}
        state.setdefault("pins", {})
        state.setdefault("lists", {})
        result = mutate(state)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_PATH)
        return result


def read_state():
    if not os.path.exists(STATE_PATH):
        return {"pins": {}, "lists": {}}
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"pins": {}, "lists": {}}
    state.setdefault("pins", {})
    state.setdefault("lists", {})
    return state


def index_meta():
    out = {}
    if not os.path.exists(INDEX_PATH):
        return out
    with open(INDEX_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = row.get("id")
            if sid:
                out[sid] = row
    return out


def transcript_sid(path):
    match = SESSION_RE.search(os.path.basename(path))
    if match:
        return match.group(1)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                payload = entry.get("payload") or {}
                if entry.get("type") == "session_meta":
                    return payload.get("session_id") or payload.get("id")
    except (OSError, json.JSONDecodeError):
        return None
    return None


def all_transcripts():
    out = {}
    if not os.path.isdir(SESSIONS_DIR):
        return out
    for root, _, names in os.walk(SESSIONS_DIR):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(root, name)
            sid = transcript_sid(path)
            if sid:
                out[sid] = path
    return out


def cap(text, limit):
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    return text[:head].rstrip() + f"\n[… pominięto {len(text) - limit} znaków …]\n" + text[-(limit - head):].lstrip()


def shrink_code(text):
    def repl(match):
        body = match.group(1)
        lines = body.count("\n")
        if lines <= CODE_KEEP_LINES:
            return match.group(0)
        return f"[blok kodu, {lines} linii — pominięty]"

    return FENCE_RE.sub(repl, text)


def text_blocks(payload):
    content = payload.get("content") or []
    if isinstance(content, str):
        return [content]
    return [block.get("text", "") for block in content if isinstance(block, dict) and block.get("text")]


def session_meta(path):
    meta = {"sid": transcript_sid(path), "cwd": None, "branch": None, "ts": None}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                entry = json.loads(line)
                payload = entry.get("payload") or {}
                if entry.get("type") == "session_meta":
                    meta["cwd"] = payload.get("cwd")
                    git = payload.get("git") or {}
                    meta["branch"] = git.get("branch")
                    meta["ts"] = payload.get("timestamp") or entry.get("timestamp")
                    break
    except (OSError, json.JSONDecodeError):
        pass
    return meta


def parse_patch_paths(command):
    if isinstance(command, list):
        command = " ".join(str(part) for part in command)
    paths = []
    for marker in ("*** Update File:", "*** Add File:", "*** Delete File:"):
        for line in command.splitlines():
            if line.strip().startswith(marker):
                path = line.split(marker, 1)[1].strip()
                if path and path not in paths:
                    paths.append(path)
    return paths


def parse_transcript(path):
    meta = session_meta(path)
    exchanges = []
    files = {}
    commands = 0
    current = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = entry.get("payload") or {}
                if entry.get("type") == "response_item":
                    if payload.get("type") != "message":
                        continue
                    role = payload.get("role")
                    text = "\n\n".join(t.strip() for t in text_blocks(payload) if t.strip())
                    if not text:
                        continue
                    if role == "user":
                        current = {"prompt": text, "ts": entry.get("timestamp"), "replies": []}
                        exchanges.append(current)
                    elif role == "assistant" and current is not None:
                        current["replies"].append(text)
                elif entry.get("type") == "event_msg" and payload.get("type") == "item_completed":
                    item = payload.get("item") or {}
                    if item.get("type") != "CommandExecution":
                        continue
                    commands += 1
                    command = item.get("command") or ""
                    for path_name in parse_patch_paths(command):
                        files[path_name] = files.get(path_name, 0) + 1
    except OSError:
        pass
    return {"meta": meta, "exchanges": exchanges, "files": files, "commands": commands}


def fmt_ts(ts):
    if not ts:
        return "?"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ts


def short_path(path, cwd):
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        return path[len(cwd.rstrip("/")) + 1:]
    return path


def related_sessions(sid, title, note):
    terms = {term.upper() for term in TERM_RE.findall(" ".join(filter(None, [title, note])))}
    if not terms:
        return []
    rows = []
    for other, pin in read_state()["pins"].items():
        if other == sid:
            continue
        haystack = " ".join(str(pin.get(key) or "") for key in ("title", "note", "branch", "cwd"))
        found = sorted(term for term in terms if re.search(rf"\b{re.escape(term)}\b", haystack, re.IGNORECASE))
        if found:
            rows.append({"sid": other, "pin": pin, "terms": found})
    rows.sort(key=lambda row: row["pin"].get("saved_epoch", 0), reverse=True)
    return rows[:RELATED_CAP]


def render_snapshot(sid, path, note, title=None):
    data = parse_transcript(path)
    meta = data["meta"]
    title = title or "sesja bez tytułu"
    out = [f"# HANDOFF — {title}", "", f"- Sesja: `{sid}` · snapshot zapisany: {fmt_ts(now_iso())}"]
    out.append(f"- cwd: `{meta['cwd']}` · gałąź: `{meta['branch']}` · aktywność: {fmt_ts(meta['ts'])}")
    out.append(f"- Transkrypt źródłowy (pełne szczegóły, czytać tylko w razie potrzeby): `{path}`")
    if note:
        out.append(f"- Polecenie na kontynuację (od użytkownika): **{note}**")
    out.append("")
    if data["files"]:
        out.append(f"## Pliki wskazane w patchach (wywołania narzędzi pominięte; exec: {data['commands']})")
        for file_path, count in list(data["files"].items())[-FILES_CAP:]:
            out.append(f"- {short_path(file_path, meta['cwd'])} ×{count}")
        if len(data["files"]) > FILES_CAP:
            out.append(f"- … oraz {len(data['files']) - FILES_CAP} wcześniejszych")
        out.append("")
    elif data["commands"]:
        out += ["## Aktywność narzędzi", f"- Wywołania exec: {data['commands']} — wyniki narzędzi pominięte", ""]
    related = related_sessions(sid, title, note)
    if related:
        out.append("## Powiązane zapisane handovery")
        for row in related:
            pin = row["pin"]
            out.append(f"- **{pin.get('title') or 'bez tytułu'}** · `{row['sid'][:8]}` · {', '.join(row['terms'])}")
        out.append("")
    exchanges = data["exchanges"][-EXCHANGES:]
    if exchanges:
        out.append("## Ostatnie wymiany (od najstarszej)")
        for index, exchange in enumerate(exchanges):
            last = index == len(exchanges) - 1
            out.append(f"### Użytkownik ({fmt_ts(exchange['ts'])})")
            out.append(cap(shrink_code(exchange["prompt"]), PROMPT_CAP))
            if exchange["replies"]:
                out.append("### Codex (ostatnia odpowiedź w tej turze)")
                reply = exchange["replies"][-1]
                out.append(cap(shrink_code(reply), REPLY_CAP_LAST if last else REPLY_CAP_EARLIER))
            out.append("")
    return "\n".join(out).rstrip() + "\n", data


def snapshot_path(sid):
    return os.path.join(HANDOFF_DIR, f"{sid}.md")


def save(sid, path, note, title=None):
    text, data = render_snapshot(sid, path, note, title)
    if not data["exchanges"]:
        return "Handoff: ta sesja nie ma jeszcze żadnej wymiany — nic do zapisania."
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    target = snapshot_path(sid)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, target)
    meta = data["meta"]

    def pin(state):
        state["pins"][sid] = {
            "note": note,
            "title": title,
            "cwd": meta["cwd"],
            "branch": meta["branch"],
            "saved_at": now_iso(),
            "saved_epoch": time.time(),
            "snapshot": target,
            "consumed_by": None,
        }

    with_state(pin)
    return f"Handoff zapisany jako zamrożony snapshot: {target}\nW nowej sesji: /handoff"


def candidates(self_sid):
    state = read_state()
    horizon = time.time() - PIN_DAYS * 86400
    rows = []
    for sid, pin in state["pins"].items():
        target = pin.get("snapshot") or snapshot_path(sid)
        if sid == self_sid or pin.get("consumed_by") or pin.get("saved_epoch", 0) < horizon or not os.path.exists(target):
            continue
        rows.append({"sid": sid, "path": target, **pin})
    rows.sort(key=lambda row: -row.get("saved_epoch", 0))
    return rows


def age(epoch):
    minutes = int((time.time() - epoch) // 60)
    if minutes < 60:
        return f"{minutes} min temu"
    if minutes < 48 * 60:
        return f"{minutes // 60} h temu"
    return f"{minutes // 1440} dni temu"


def row_text(index, row, cwd):
    where = "" if row.get("cwd") == cwd else f" · cwd {row.get('cwd')}"
    return (f"{index}. **{row.get('title') or 'bez tytułu'}** — zapisany {age(row['saved_epoch'])} · `{row['sid'][:8]}` · "
            f"{row.get('branch')}{where}\n   polecenie na kontynuację: {row.get('note') or '(brak — model streści sesję i poczeka)'}")


def list_text(rows, self_sid, header, cwd):
    rows = rows[:LIST_LIMIT]
    with_state(lambda state: state["lists"].__setitem__(self_sid, [row["sid"] for row in rows]))
    if not rows:
        return no_handoffs_text()
    return "\n".join(["LISTA ZAMROŻONYCH HANDOFFÓW", header, ""] +
                     [row_text(index, row, cwd) for index, row in enumerate(rows, 1)] +
                     ["", "Wczytanie pozycji wybiera się numerem z tej listy, np. `/handoff 1`."])


def no_handoffs_text():
    return ("BRAK ZAPISANYCH HANDOFFÓW\n"
            "Zapis: w sesji, którą chcesz potem kontynuować, wpisz `/handoff -save` albo "
            "`/handoff -save polecenie na kontynuację`.")


def select_row(self_sid, args):
    rows = candidates(self_sid)
    if not rows:
        return rows, None
    query = " ".join(args).strip()
    if query.isdigit():
        shown = read_state()["lists"].get(self_sid) or [row["sid"] for row in rows[:LIST_LIMIT]]
        index = int(query) - 1
        if 0 <= index < len(shown):
            return rows, next((row for row in rows if row["sid"] == shown[index]), None)
        return rows, None
    return rows, None


def mark_consumed(sid, self_sid):
    def mark(state):
        pin = state["pins"].get(sid)
        if pin and not pin.get("consumed_by"):
            pin["consumed_by"] = self_sid
            pin["consumed_at"] = now_iso()

    with_state(mark)


def load_snapshot(row, self_sid):
    try:
        with open(row["path"], encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return "BRAK SNAPSHOTU HANDOFFU — zapisany plik nie jest dostępny."
    mark_consumed(row["sid"], self_sid)
    return text


def read_snapshot(row):
    try:
        with open(row["path"], encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "BRAK SNAPSHOTU HANDOFFU — zapisany plik nie jest dostępny."


def load_context(text):
    return ("Wczytano zamrożony snapshot handoffu. Traktuj go jako kontekst poprzedniej sesji. "
            "Jeśli zawiera polecenie na kontynuację, wykonaj je od razu. Jeśli go nie zawiera, "
            "streść stan w 2–4 zdaniach po polsku i czekaj na dalsze polecenie.\n\n" + text)


def cmd_load(self_sid, args):
    if args and args[0].lower() == "-save":
        path = all_transcripts().get(self_sid)
        if not path:
            return print("Nie znaleziono transcriptu bieżącej sesji.")
        note = " ".join(args[1:]).strip(" :") or None
        title = index_meta().get(self_sid, {}).get("thread_name")
        return print(save(self_sid, path, note, title))
    rows, row = select_row(self_sid, args)
    if not rows:
        return print(no_handoffs_text())
    if row:
        return print(load_snapshot(row, self_sid))
    query = " ".join(args).strip()
    header = "Nieprawidłowy wybór — aktualne zamrożone handoffy:" if query else "Zamrożone handoffy — wybierz jeden."
    return print(list_text(rows, self_sid, header, os.getcwd()))


def cmd_hook_prompt():
    data = json.load(sys.stdin)
    prompt = data.get("prompt") or ""
    sid = data.get("session_id")
    if LIST_RE.match(prompt):
        rows = candidates(sid)
        body = list_text(rows, sid, "Zamrożone handoffy — wybierz jeden.", data.get("cwd")) if rows else no_handoffs_text()
        print(json.dumps({"decision": "block", "reason": "Bez wywołania modelu.\n\n" + body}, ensure_ascii=False))
        return
    match = SAVE_RE.match(prompt)
    if match:
        path = data.get("transcript_path")
        note = (match.group("note") or "").strip() or None
        title = index_meta().get(sid, {}).get("thread_name")
        message = save(sid, path, note, title) if path else "Nie znaleziono transcriptu bieżącej sesji."
        print(json.dumps({"decision": "block", "reason": "Bez wywołania modelu. " + message}, ensure_ascii=False))
        return
    if not HANDOFF_RE.match(prompt):
        return
    args = prompt.strip().split()[1:]
    rows, row = select_row(sid, args)
    if not rows:
        print(json.dumps({"decision": "block", "reason": no_handoffs_text()}, ensure_ascii=False))
        return
    if not row:
        query = " ".join(args).strip()
        header = "Nieprawidłowy wybór — aktualne zamrożone handoffy:" if query else "Zamrożone handoffy — wybierz jeden."
        print(json.dumps({"decision": "block", "reason": list_text(rows, sid, header, data.get("cwd"))}, ensure_ascii=False))
        return
    text = load_snapshot(row, sid)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": load_context(text)}}, ensure_ascii=False))


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit("użycie: handoff.py load --self SID -- [-save polecenie | numer] | hook-prompt")
    command = argv[0]
    if command == "hook-prompt":
        return cmd_hook_prompt()
    if command == "load":
        if len(argv) < 3 or argv[1] != "--self":
            sys.exit("load wymaga --self SID")
        rest = argv[3:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        return cmd_load(argv[2], rest)
    if command == "save":
        sid = argv[1]
        path = all_transcripts().get(sid)
        if not path:
            sys.exit("nie znaleziono transcriptu sesji")
        title = index_meta().get(sid, {}).get("thread_name")
        return print(save(sid, path, " ".join(argv[2:]) or None, title))
    if command == "show":
        sid = argv[1]
        row = read_state()["pins"].get(sid)
        if not row:
            sys.exit("nie znaleziono handoffu")
        return print(read_snapshot({"sid": sid, "path": row.get("snapshot") or snapshot_path(sid)}))
    sys.exit(f"nieznana komenda: {command}")


if __name__ == "__main__":
    main()
