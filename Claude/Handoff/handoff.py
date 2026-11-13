#!/usr/bin/env python3
import fcntl
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
PROJECTS_DIR = os.path.join(CLAUDE_DIR, "projects")
REGISTRY_DIR = os.path.join(CLAUDE_DIR, "sessions")
HANDOFF_DIR = os.path.join(CLAUDE_DIR, "handoffs")
STATE_PATH = os.path.join(HANDOFF_DIR, "state.json")
LOCK_PATH = os.path.join(HANDOFF_DIR, "state.lock")

SAVE_RE = re.compile(r"^\s*/handoff\s+-save\b:?\s*(?P<note>.*?)\s*$", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"^\s*<([a-zA-Z_-]+)")
SECTION_RE = re.compile(r"(?m)^\s*(\d+)\.\s+\*\*(.+?)\*\*")
FENCE_RE = re.compile(r"```[^\n]*\n([\s\S]*?)```")

LIST_LIMIT = 8
PIN_DAYS = 14
EXCHANGES = 4
PROMPT_CAP = 1500
REPLY_CAP_LAST = 6000
REPLY_CAP_EARLIER = 1200
SUMMARY_SECTION_CAP = 2500
SUMMARY_SECTIONS = ("primary request", "pending tasks", "current work", "next step")
CODE_KEEP_LINES = 6
FILES_CAP = 40
TAIL_BYTES = 2 * 1024 * 1024
MODIFYING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def with_state(mutate):
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    with open(LOCK_PATH, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = {"pins": {}, "lists": {}}
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
        result = mutate(state)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_PATH)
        return result


def read_state():
    if not os.path.exists(STATE_PATH):
        return {"pins": {}, "lists": {}}
    with open(STATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def all_transcripts():
    out = {}
    for project in os.listdir(PROJECTS_DIR):
        pdir = os.path.join(PROJECTS_DIR, project)
        if not os.path.isdir(pdir):
            continue
        for name in os.listdir(pdir):
            if name.endswith(".jsonl"):
                out[name[:-6]] = os.path.join(pdir, name)
    return out


def live_sessions():
    live = {}
    if not os.path.isdir(REGISTRY_DIR):
        return live
    for name in os.listdir(REGISTRY_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(REGISTRY_DIR, name), encoding="utf-8") as f:
                reg = json.load(f)
            os.kill(int(reg["pid"]), 0)
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        live[reg["sessionId"]] = reg
    return live


def user_text(entry):
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = entry.get("message", {}).get("content")
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
    parts = []
    for block in blocks:
        if block.get("type") != "text":
            continue
        text = block.get("text", "")
        if TAG_RE.match(text):
            continue
        parts.append(text.strip())
    text = "\n\n".join(p for p in parts if p)
    return text or None


def is_clear(entry):
    if entry.get("type") != "user":
        return False
    content = entry.get("message", {}).get("content")
    return isinstance(content, str) and "<command-name>/clear</command-name>" in content


def compact_text(entry):
    content = entry.get("message", {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


def tail_meta(path):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - TAIL_BYTES))
        data = f.read().decode("utf-8", errors="replace")
    lines = data.split("\n")
    if size > TAIL_BYTES:
        lines = lines[1:]
    meta = {"title": None, "last_prompt": None, "cwd": None, "branch": None, "ts": None}
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = entry.get("type")
        if kind == "ai-title" and meta["title"] is None:
            meta["title"] = entry.get("aiTitle")
        elif kind == "last-prompt" and meta["last_prompt"] is None:
            meta["last_prompt"] = entry.get("lastPrompt")
        elif kind in ("user", "assistant") and meta["cwd"] is None and entry.get("cwd"):
            meta["cwd"] = entry.get("cwd")
            meta["branch"] = entry.get("gitBranch")
            meta["ts"] = entry.get("timestamp")
        if all(meta[k] is not None for k in ("title", "last_prompt", "cwd")):
            break
    return meta


def parse_transcript(path):
    s = {"summary": None, "summary_ts": None, "files": {}, "exchanges": [], "bash": 0, "cwd": None, "branch": None,
         "ts": None, "title": None}
    current = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = entry.get("type")
            if kind == "ai-title":
                s["title"] = entry.get("aiTitle")
                continue
            if kind not in ("user", "assistant") or entry.get("isSidechain"):
                continue
            if entry.get("cwd"):
                s["cwd"], s["branch"], s["ts"] = entry["cwd"], entry.get("gitBranch"), entry.get("timestamp")
            if is_clear(entry):
                s.update(summary=None, summary_ts=None, files={}, exchanges=[], bash=0)
                current = None
                continue
            if kind == "user":
                if entry.get("isCompactSummary"):
                    s["summary"] = compact_text(entry)
                    s["summary_ts"] = entry.get("timestamp")
                    continue
                text = user_text(entry)
                if text:
                    current = {"prompt": text, "ts": entry.get("timestamp"), "texts": {}, "order": []}
                    s["exchanges"].append(current)
                continue
            msg = entry.get("message", {})
            mid = msg.get("id")
            for block in msg.get("content") or []:
                btype = block.get("type")
                if btype == "text" and current is not None and block.get("text", "").strip():
                    if mid not in current["texts"]:
                        current["texts"][mid] = []
                        current["order"].append(mid)
                    current["texts"][mid].append(block["text"].strip())
                elif btype == "tool_use":
                    name = block.get("name")
                    tin = block.get("input") or {}
                    if name in MODIFYING_TOOLS:
                        fp = tin.get("file_path") or tin.get("notebook_path")
                        if fp:
                            s["files"][fp] = s["files"].pop(fp, 0) + 1
                    elif name == "Bash":
                        s["bash"] += 1
    return s


def shrink_code(text):
    def repl(m):
        body = m.group(1)
        n = body.count("\n")
        if n <= CODE_KEEP_LINES:
            return m.group(0)
        return f"[blok kodu, {n} linii — pominięty]"
    return FENCE_RE.sub(repl, text)


def cap(text, limit):
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    return text[:head].rstrip() + f"\n[… pominięto {len(text) - limit} znaków …]\n" + text[-(limit - head):].lstrip()


def pick_summary(summary):
    marks = list(SECTION_RE.finditer(summary))
    if not marks:
        return cap(summary, SUMMARY_SECTION_CAP * 2)
    out = []
    for i, m in enumerate(marks):
        title = m.group(2).strip()
        if not any(k in title.lower() for k in SUMMARY_SECTIONS):
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(summary)
        body = summary[m.end():end]
        body = body.split("If you need specific details from before compaction")[0].strip(" :\n")
        out.append(f"**{title}**\n{cap(shrink_code(body), SUMMARY_SECTION_CAP)}")
    return "\n\n".join(out)


def short_path(fp, cwd):
    if cwd and fp.startswith(cwd.rstrip("/") + "/"):
        return fp[len(cwd.rstrip("/")) + 1:]
    return fp


def fmt_ts(ts):
    if not ts:
        return "?"
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def render(sid, path, note=None, live=None):
    s = parse_transcript(path)
    out = [f"# HANDOFF — {s['title'] or 'sesja bez tytułu'}", ""]
    status = f"aktywna ({live['name']})" if live else "zamknięta"
    out.append(f"- Sesja: `{sid}` · {status}")
    out.append(f"- cwd: `{s['cwd']}` · gałąź: `{s['branch']}` · ostatnia aktywność: {fmt_ts(s['ts'])}")
    out.append(f"- Transkrypt (pełne szczegóły, czytać tylko w razie potrzeby): `{path}`")
    if note:
        out.append(f"- Polecenie na kontynuację (od użytkownika): **{note}**")
    out.append("")
    if s["summary"]:
        out += [f"## Skrót z ostatniego kompaktowania ({fmt_ts(s['summary_ts'])}; jeśli coś przeczy ostatnim wymianom niżej, wygrywają wymiany)",
                pick_summary(s["summary"]), ""]
    if s["files"]:
        items = list(s["files"].items())[-FILES_CAP:]
        out.append(f"## Pliki zmieniane w sesji (Edit/Write; ostatnio zmieniane na końcu; Bash: {s['bash']} wywołań)")
        out += [f"- {short_path(fp, s['cwd'])} ×{n}" for fp, n in items]
        if len(s["files"]) > FILES_CAP:
            out.append(f"- … oraz {len(s['files']) - FILES_CAP} wcześniejszych")
        out.append("")
    exchanges = s["exchanges"][-EXCHANGES:]
    if exchanges:
        out.append("## Ostatnie wymiany (od najstarszej)")
        for i, ex in enumerate(exchanges):
            last = i == len(exchanges) - 1
            out.append(f"### Użytkownik ({fmt_ts(ex['ts'])})")
            out.append(cap(shrink_code(ex["prompt"]), PROMPT_CAP))
            if ex["order"]:
                reply = "\n\n".join(ex["texts"][ex["order"][-1]])
                out.append("### Claude (ostatnia odpowiedź w tej turze)")
                out.append(cap(shrink_code(reply), REPLY_CAP_LAST if last else REPLY_CAP_EARLIER))
            out.append("")
    return "\n".join(out).rstrip() + "\n", s


def candidates(self_sid):
    state = read_state()
    transcripts = all_transcripts()
    live = live_sessions()
    horizon = time.time() - PIN_DAYS * 86400
    rows = []
    for sid, pin in state["pins"].items():
        if sid == self_sid or pin.get("consumed_by") or pin["saved_epoch"] < horizon or sid not in transcripts:
            continue
        path = transcripts[sid]
        rows.append({"sid": sid, "path": path, "saved": pin["saved_epoch"], "note": pin.get("note"),
                     "live": live.get(sid), **tail_meta(path)})
    rows.sort(key=lambda r: -r["saved"])
    return rows


def age(mtime):
    minutes = int((time.time() - mtime) // 60)
    if minutes < 60:
        return f"{minutes} min temu"
    if minutes < 48 * 60:
        return f"{minutes // 60} h temu"
    return f"{minutes // 1440} dni temu"


def print_row(i, r, cwd):
    status = f"● aktywna ({r['live']['name']})" if r["live"] else "zamknięta"
    where = "" if r["cwd"] == cwd else f" · cwd {r['cwd']}"
    print(f"{i}. **{r['title'] or 'bez tytułu'}** — zapisany {age(r['saved'])} · `{r['sid'][:8]}` · {r['branch']}{where} · sesja {status}")
    print(f"   polecenie na kontynuację: {r['note'] or '(brak — model streści sesję i poczeka)'}")


def print_list(rows, self_sid, header):
    rows = rows[:LIST_LIMIT]
    with_state(lambda st: st["lists"].__setitem__(self_sid, [r["sid"] for r in rows]))
    cwd = os.getcwd()
    print("LISTA ZAPISANYCH HANDOFFÓW")
    print(header)
    print()
    for i, r in enumerate(rows, 1):
        print_row(i, r, cwd)
    print()
    first = rows[0]
    words = " ".join(re.findall(r"\w+", (first["title"] or "").lower())[:2]) or "słowa z tytułu"
    print(f"Wczytanie pozycji 1: `/handoff 1`, `/handoff {first['sid'][:8]}` (id) albo `/handoff {words}` (słowa z tytułu).")


def emit_handoff(row, self_sid):
    text, _ = render(row["sid"], row["path"], row.get("note"), row.get("live"))

    def mark(st):
        pin = st["pins"].get(row["sid"])
        if pin and not pin.get("consumed_by"):
            pin["consumed_by"] = self_sid
            pin["consumed_at"] = now_iso()
    with_state(mark)
    if row.get("live"):
        print(f"UWAGA: sesja źródłowa nadal działa ({row['live']['name']}); jej dalsze zmiany nie są w tym skrócie.\n")
    print(text)
    print(f"[handoff: ~{len(text) // 3} tokenów]")


def cmd_load(self_sid, args):
    if args and args[0].lower() == "-save":
        note = " ".join(args[1:]).strip(" :") or None
        print("HANDOFF ZAPISANY PRZEZ SKILL — hook UserPromptSubmit nie przechwycił polecenia, więc ten zapis kosztował wywołanie modelu.")
        return print(save(self_sid, all_transcripts()[self_sid], note))
    rows = candidates(self_sid)
    if not rows:
        print("BRAK ZAPISANYCH HANDOFFÓW")
        print("Zapis: w sesji, którą chcesz potem kontynuować, wpisz `/handoff -save` albo "
              "`/handoff -save polecenie na kontynuację`.")
        return
    query = " ".join(args).strip()
    if not query:
        if len(rows) == 1:
            return emit_handoff(rows[0], self_sid)
        return print_list(rows, self_sid, "Kilka zapisanych handoffów — wybierz jeden.")
    by_sid = {r["sid"]: r for r in rows}
    if query.isdigit():
        shown = read_state()["lists"].get(self_sid) or [r["sid"] for r in rows[:LIST_LIMIT]]
        idx = int(query) - 1
        if 0 <= idx < len(shown) and shown[idx] in by_sid:
            return emit_handoff(by_sid[shown[idx]], self_sid)
        return print_list(rows, self_sid, f"Nie ma pozycji {query} — aktualne zapisane handoffy:")
    words = query.casefold().split()
    hits = [r for r in rows if r["sid"].startswith(query.lower())
            or all(w in " ".join(str(r[k] or "") for k in ("title", "last_prompt", "note", "branch", "cwd")).casefold() for w in words)]
    if len(hits) == 1:
        return emit_handoff(hits[0], self_sid)
    return print_list(hits or rows, self_sid, f"Pasujące do „{query}”:" if hits else f"Nic nie pasuje do „{query}” — wszystkie zapisane handoffy:")


def save(sid, path, note):
    text, s = render(sid, path, note, None)
    if not s["exchanges"]:
        return "Handoff: ta sesja nie ma jeszcze żadnej wymiany — nic do zapisania. W nowej sesji wczytujesz przez /handoff."
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    md = os.path.join(HANDOFF_DIR, f"{sid}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(text)

    def pin(st):
        st["pins"][sid] = {"note": note, "saved_at": now_iso(), "saved_epoch": time.time(), "consumed_by": None}
    with_state(pin)
    return (f"Handoff zapisany (~{len(text) // 3} tokenów): {md}\n"
            f"Tytuł: {s['title']}" + (f" · polecenie na kontynuację: {note}" if note else "") +
            "\nW nowej sesji: /handoff")


def cmd_hook_prompt():
    data = json.load(sys.stdin)
    m = SAVE_RE.match(data.get("prompt") or "")
    if not m:
        return
    note = (m.group("note") or "").strip() or None
    msg = "Bez wywołania modelu. " + save(data["session_id"], data["transcript_path"], note)
    print(json.dumps({"decision": "block", "reason": msg}, ensure_ascii=False))


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit("użycie: handoff.py load --self SID -- [-save polecenie | zapytanie] | hook-prompt | save SID [polecenie] | show SID")
    cmd = argv[0]
    if cmd == "hook-prompt":
        return cmd_hook_prompt()
    if cmd == "load":
        if len(argv) < 3 or argv[1] != "--self":
            sys.exit("load wymaga --self SID")
        rest = argv[3:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        return cmd_load(argv[2], rest)
    if cmd == "save":
        sid = argv[1]
        return print(save(sid, all_transcripts()[sid], " ".join(argv[2:]) or None))
    if cmd == "show":
        sid = argv[1]
        return print(render(sid, all_transcripts()[sid], None, live_sessions().get(sid))[0])
    sys.exit(f"nieznana komenda: {cmd}")


if __name__ == "__main__":
    main()
