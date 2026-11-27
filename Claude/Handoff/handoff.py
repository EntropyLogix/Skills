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

SAVE_RE = re.compile(r"^\s*/handoff\s+--save\b:?\s*(?P<note>.*?)\s*$", re.IGNORECASE | re.DOTALL)
LIST_RE = re.compile(r"^\s*/handoff\s*$", re.IGNORECASE)
HANDOFF_RE = re.compile(r"^\s*/handoff(?:\s|$)", re.IGNORECASE)
TAG_RE = re.compile(r"^\s*<([a-zA-Z_-]+)")
SECTION_RE = re.compile(r"(?m)^\s*(\d+)\.\s+\*\*(.+?)\*\*")
FENCE_RE = re.compile(r"```[^\n]*\n([\s\S]*?)```")

EXCHANGES = 4
PROMPT_CAP = 1500
REPLY_CAP_LAST = 6000
REPLY_CAP_EARLIER = 1200
SUMMARY_SECTION_CAP = 2500
SUMMARY_SECTIONS = ("primary request", "pending tasks", "current work", "next step")
EARLIER_SECTION = "current work"
EARLIER_CAP = 500
EARLIER_BUDGET = 12000
CODE_KEEP_LINES = 6
FILES_CAP = 40
DOCS_CAP = 40
DOC_EXTENSIONS = (".md", ".txt", ".tsv")
TERM_RE = re.compile(r"\b[A-Za-z]{1,8}-?\d{2,}\b")
RELATED_CAP = 8
TAIL_BYTES = 2 * 1024 * 1024
CHARS_PER_TOKEN = 2
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
    s = {"summary": None, "summary_ts": None, "summaries": [], "files": {}, "exchanges": [], "bash": 0, "cwd": None,
         "branch": None, "ts": None, "title": None}
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
                s.update(summary=None, summary_ts=None, summaries=[], files={}, exchanges=[], bash=0)
                current = None
                continue
            if kind == "user":
                if entry.get("isCompactSummary"):
                    s["summary"] = compact_text(entry)
                    s["summary_ts"] = entry.get("timestamp")
                    s["summaries"].append((s["summary_ts"], s["summary"]))
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
        return f"[code block, {n} lines omitted]"
    return FENCE_RE.sub(repl, text)


def cap(text, limit):
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    return text[:head].rstrip() + f"\n[… {len(text) - limit} characters omitted …]\n" + text[-(limit - head):].lstrip()


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


def summary_section(summary, key):
    marks = list(SECTION_RE.finditer(summary))
    for i, m in enumerate(marks):
        if key in m.group(2).lower():
            end = marks[i + 1].start() if i + 1 < len(marks) else len(summary)
            return summary[m.end():end].split("If you need specific details from before compaction")[0].strip(" :\n")
    return summary


def earlier_summaries(summaries):
    picked = []
    used = 0
    for ts, text in reversed(summaries[:-1]):
        body = cap(shrink_code(summary_section(text, EARLIER_SECTION)), EARLIER_CAP)
        if used + len(body) > EARLIER_BUDGET:
            break
        picked.append((ts, body))
        used += len(body)
    return list(reversed(picked)), len(summaries) - 1 - len(picked)


def related_sessions(sid, s, note):
    terms = {t.upper() for t in TERM_RE.findall(" ".join(filter(None, [s["title"], note])))}
    if not terms:
        return []
    pins = read_state()["pins"]
    rows = []
    for other, path in all_transcripts().items():
        if other == sid:
            continue
        meta = tail_meta(path)
        haystack = " ".join(filter(None, [meta["title"], meta["last_prompt"], (pins.get(other) or {}).get("note")]))
        found = sorted(t for t in terms if re.search(rf"\b{re.escape(t)}\b", haystack, re.IGNORECASE))
        if found:
            rows.append({"sid": other, "path": path, "terms": found, **meta})
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    return rows[:RELATED_CAP]


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
    out = [f"# HANDOFF — {s['title'] or 'untitled session'}", ""]
    status = f"active ({live['name']})" if live else "snapshot"
    out.append(f"- Session: `{sid}` · {status}")
    out.append(f"- cwd: `{s['cwd']}` · branch: `{s['branch']}` · last activity: {fmt_ts(s['ts'])}")
    out.append(f"- Transcript (read for full details only when needed): `{path}`")
    if note:
        out.append(f"- Continuation instruction (from user): **{note}**")
    out.append("")
    if s["summary"]:
        out += [f"## Latest compaction summary ({fmt_ts(s['summary_ts'])}; later exchanges take precedence)",
                pick_summary(s["summary"]), ""]
    earlier, skipped = earlier_summaries(s["summaries"])
    if earlier:
        out.append("## Earlier compaction history (Current Work section; oldest first)")
        if skipped:
            out.append(f"- … omitted {skipped} earlier compactions (snapshot limit)")
        for ts, body in earlier:
            out += [f"### {fmt_ts(ts)}", body]
        out.append("")
    docs = {fp: n for fp, n in s["files"].items() if fp.lower().endswith(DOC_EXTENSIONS)}
    code = {fp: n for fp, n in s["files"].items() if fp not in docs}
    for title, group, limit in (("Documents saved in the session (reports, notes; most recently changed last)", docs, DOCS_CAP),
                                (f"Files changed in the session (Edit/Write; most recently changed last; Bash: {s['bash']} calls)", code, FILES_CAP)):
        if not group:
            continue
        out.append(f"## {title}")
        out += [f"- {short_path(fp, s['cwd'])} ×{n}" for fp, n in list(group.items())[-limit:]]
        if len(group) > limit:
            out.append(f"- … and {len(group) - limit} earlier items")
        out.append("")
    related = related_sessions(sid, s, note)
    if related:
        out.append("## Related sessions (shared identifiers from the title and instruction; read transcripts only when needed)")
        for r in related:
            out.append(f"- **{r['title'] or 'untitled'}** · `{r['sid'][:8]}` · {fmt_ts(r['ts'])} · {', '.join(r['terms'])} · `{r['path']}`")
        out.append("")
    exchanges = s["exchanges"][-EXCHANGES:]
    if exchanges:
        out.append("## Recent exchanges (oldest first)")
        for i, ex in enumerate(exchanges):
            last = i == len(exchanges) - 1
            out.append(f"### User ({fmt_ts(ex['ts'])})")
            out.append(cap(shrink_code(ex["prompt"]), PROMPT_CAP))
            if ex["order"]:
                reply = "\n\n".join(ex["texts"][ex["order"][-1]])
                out.append("### Claude (last response in this turn)")
                out.append(cap(shrink_code(reply), REPLY_CAP_LAST if last else REPLY_CAP_EARLIER))
            out.append("")
    return "\n".join(out).rstrip() + "\n", s


def candidates(self_sid):
    state = read_state()
    transcripts = all_transcripts()
    live = live_sessions()
    rows = []
    for sid, pin in state["pins"].items():
        if sid == self_sid or pin.get("consumed_by") or sid not in transcripts:
            continue
        path = transcripts[sid]
        rows.append({"sid": sid, "path": path, "saved": pin["saved_epoch"], "note": pin.get("note"),
                     "live": live.get(sid), "size": snapshot_size(sid), **tail_meta(path)})
    rows.sort(key=lambda r: -r["saved"])
    return rows


def size_text(text):
    return f"{len(text.encode('utf-8')) / 1024:.1f} KB, ~{len(text) // CHARS_PER_TOKEN} tokens"


def snapshot_size(sid):
    try:
        with open(os.path.join(HANDOFF_DIR, f"{sid}.md"), encoding="utf-8") as f:
            return size_text(f.read())
    except OSError:
        return "snapshot missing"


def saved_when(epoch):
    minutes = int((time.time() - epoch) // 60)
    date = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")
    if minutes < 60:
        return f"{date} ({minutes} min ago)"
    if minutes < 48 * 60:
        return f"{date} ({minutes // 60} h ago)"
    return f"{date} ({minutes // 1440} days ago)"


def row_text(i, r, cwd):
    status = f"● active ({r['live']['name']})" if r["live"] else "closed"
    where = "" if r["cwd"] == cwd else f" · cwd {r['cwd']}"
    return (f"{i}. **{r['title'] or 'untitled'}** — saved {saved_when(r['saved'])} · {r['size']} · `{r['sid'][:8]}` · {r['branch']}{where} · session {status}\n"
            f"   continuation instruction: {r['note'] or '(none — the model summarizes the session and waits)'}")


def list_text(rows, self_sid, header, cwd):
    with_state(lambda st: st["lists"].__setitem__(self_sid, [r["sid"] for r in rows]))
    return "\n".join(["SAVED HANDOFFS", header, ""] +
                     [row_text(i, r, cwd) for i, r in enumerate(rows, 1)] +
                     ["", "Load an entry by its number from this list, for example `/handoff 1`.",
                      "Save the current session with `/handoff --save [instruction]`."])


def print_list(rows, self_sid, header):
    print(list_text(rows, self_sid, header, os.getcwd()))


def no_handoffs_text():
    return ("NO SAVED HANDOFFS\n"
            "Save one in the session you want to continue with by typing `/handoff --save` or "
            "`/handoff --save continuation instruction`.")


def invalid_header(query):
    return f"Invalid argument `{query}` — current saved handoffs:"


def emit_handoff(row, self_sid):
    snapshot = os.path.join(HANDOFF_DIR, f"{row['sid']}.md")
    try:
        with open(snapshot, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return print(f"SAVED SNAPSHOT NOT FOUND — {snapshot}")

    def mark(st):
        pin = st["pins"].get(row["sid"])
        if pin and not pin.get("consumed_by"):
            pin["consumed_by"] = self_sid
            pin["consumed_at"] = now_iso()
    with_state(mark)
    if row.get("live"):
        print(f"WARNING: the source session is still active ({row['live']['name']}); the snapshot is from the time it was saved.\n")
    print(text)
    print(f"[handoff: {size_text(text)}]")


def cmd_load(self_sid, args):
    if args and args[0].lower() == "--save":
        note = " ".join(args[1:]).strip(" :") or None
        print("HANDOFF SAVED BY SKILL — the UserPromptSubmit hook did not intercept the command, so saving this handoff required a model call.")
        return print(save(self_sid, all_transcripts()[self_sid], note))
    rows = candidates(self_sid)
    if not rows:
        return print(no_handoffs_text())
    query = " ".join(args).strip()
    if not query:
        return print_list(rows, self_sid, "Saved handoffs — choose one.")
    if not query.isdigit():
        return print_list(rows, self_sid, invalid_header(query))
    shown = read_state()["lists"].get(self_sid) or [r["sid"] for r in rows]
    idx = int(query) - 1
    if 0 <= idx < len(shown):
        row = next((r for r in rows if r["sid"] == shown[idx]), None)
        if row:
            return emit_handoff(row, self_sid)
    return print_list(rows, self_sid, invalid_header(query))


def save(sid, path, note):
    text, s = render(sid, path, note, None)
    if not s["exchanges"]:
        return "Handoff: this session has no exchanges yet, so there is nothing to save. Load it in a new session with /handoff."
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    md = os.path.join(HANDOFF_DIR, f"{sid}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(text)

    def pin(st):
        st["pins"][sid] = {"note": note, "saved_at": now_iso(), "saved_epoch": time.time(), "consumed_by": None}
    with_state(pin)
    return (f"Handoff saved ({size_text(text)}): {md}\n"
            f"Title: {s['title']}" + (f" · continuation instruction: {note}" if note else "") +
            "\nIn a new session: /handoff")


def cmd_hook_prompt():
    data = json.load(sys.stdin)
    prompt = data.get("prompt") or ""
    if LIST_RE.match(prompt):
        rows = candidates(data["session_id"])
        body = list_text(rows, data["session_id"], "Saved handoffs — choose one.", data["cwd"]) if rows else no_handoffs_text()
        msg = "No model call.\n\n" + body
    else:
        m = SAVE_RE.match(prompt)
        if not m:
            if not HANDOFF_RE.match(prompt):
                return
            args = prompt.strip().split()[1:]
            rows = candidates(data["session_id"])
            if args and args[0].isdigit():
                shown = read_state()["lists"].get(data["session_id"]) or [r["sid"] for r in rows]
                idx = int(args[0]) - 1
                if 0 <= idx < len(shown) and any(r["sid"] == shown[idx] for r in rows):
                    return
            body = list_text(rows, data["session_id"], invalid_header(" ".join(args)), data["cwd"]) if rows else no_handoffs_text()
            return print(json.dumps({"decision": "block", "reason": "No model call.\n\n" + body}, ensure_ascii=False))
        note = (m.group("note") or "").strip() or None
        msg = "No model call. " + save(data["session_id"], data["transcript_path"], note)
    print(json.dumps({"decision": "block", "reason": msg}, ensure_ascii=False))


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit("usage: handoff.py load --self SID -- [--save instruction | number] | hook-prompt | save SID [instruction] | show SID")
    cmd = argv[0]
    if cmd == "hook-prompt":
        return cmd_hook_prompt()
    if cmd == "load":
        if len(argv) < 3 or argv[1] != "--self":
            sys.exit("load requires --self SID")
        rest = argv[3:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        return cmd_load(argv[2], rest)
    if cmd == "save":
        sid = argv[1]
        return print(save(sid, all_transcripts()[sid], " ".join(argv[2:]) or None))
    if cmd == "show":
        sid = argv[1]
        snapshot = os.path.join(HANDOFF_DIR, f"{sid}.md")
        try:
            with open(snapshot, encoding="utf-8") as f:
                return print(f.read())
        except OSError:
            sys.exit(f"saved snapshot not found: {snapshot}")
    sys.exit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
