#!/usr/bin/env python3
"""Export DeepSeek chat history (conversations.json) into an Obsidian vault.

Stdlib only: json / urllib (no jq, curl or ollama CLI needed).

  --source PATH         - conversations.json (default: $DEEPSEEK_CONVERSATIONS_JSON;
                          the dump path changes between exports, so the user
                          sets the variable before each run)
  outdir (positional)   - notes directory (default: $DEEPSEEK_OBSIDIAN_VAULT)
  --limit N             - only the first N conversations (tests)
  --tags                - generate tags via local ollama REST (:11434)
  --tag-model NAME      - tag model (default gemma3:4b)
  --tag-url URL         - ollama address (default http://localhost:11434)
  --max-kb KB           - note size limit (default 400 KB): bigger bodies are
                          cut into "<name> p2.md", ... parts at message
                          ("---") and paragraph (blank line) boundaries —
                          words/sentences are never broken; parts are linked
                          with wikilinks (Obsidian crashes on big dense md).

Note format mirrors opencode_obsidian_export.py (telegram-import template):
  frontmatter (create/tags) + quote header (> Source/Created/Tags) + body:
  👤 User / 🤖 model / 🤔 Reasoning / 🔍 Web search / 📄 Attached.
  Message timestamps inside the body stay raw from the dump (+08:00); local
  time is used only for the filename, frontmatter and "> Created:".

Rules:
- mapping tree walk: root -> children[0] -> ... (the selected branch of
  regenerations; see walk_chain);
- incremental: note mtime >= updated_at -> skip (mtime is the marker);
- ollama unreachable -> empty tags, one WARN, rendering continues.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

SOURCE_ENV = "DEEPSEEK_CONVERSATIONS_JSON"  # set before each export
VAULT_ENV = "DEEPSEEK_OBSIDIAN_VAULT"       # persistent (shell profile)
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье"]
FORBIDDEN = re.compile(r'[\\/*?"<>|#^\[\]`]')
TAG_RE = re.compile(r"[0-9a-zа-яё_-]+")


def trunc(s, n):
    return s if len(s) <= n else s[:n] + "\n... (truncated)"


def walk_chain(mapping):
    node = mapping.get("root")
    while node:
        yield node
        children = node.get("children") or []
        node = mapping.get(children[0]) if children else None


def render_body(convo):
    out = []
    for node in walk_chain(convo.get("mapping") or {}):
        m = node.get("message")
        if not m:
            continue
        model = m.get("model") or "deepseek-chat"
        ts = m.get("inserted_at") or ""
        for f in m.get("fragments", []):
            ft = f.get("type")
            if ft == "REQUEST":
                c = (f.get("content") or "").rstrip("\n")
                out.append(f"\n---\n## 👤 User\n*{ts}*\n\n{c}\n")
            elif ft == "RESPONSE":
                c = (f.get("content") or "").rstrip("\n")
                out.append(f"\n---\n## 🤖 {model}\n*{ts}*\n\n{c}\n")
            elif ft == "THINK":
                c = (f.get("content") or "").rstrip("\n")
                out.append("\n<details>\n<summary>🤔 Reasoning</summary>\n\n"
                           f"{trunc(c, 4000)}\n\n</details>\n")
            elif ft in ("TOOL_SEARCH", "SEARCH"):
                links = [f"- [{r.get('title') or r.get('url')}]({r.get('url')})"
                         for r in (f.get("results") or []) if r.get("url")]
                if links:
                    out.append("\n<details>\n<summary>🔍 Web search</summary>\n\n"
                               + "\n".join(links) + "\n\n</details>\n")
            elif ft == "FILE":
                names = [x.get("file_name") for x in (f.get("files") or [])
                         if x.get("file_name")]
                if names:
                    out.append(f"\n> 📄 Attached: {', '.join(names)}\n")
            # TOOL_OPEN / TOOL_FIND: no payload in this dump — skipped
    return "".join(out)


def split_body(body: str, limit: int):
    """Split at message ('---') and paragraph (blank line) boundaries.

    Words, lines and sentences are never broken: an oversized paragraph stays
    whole (a part may exceed the limit — overflow beats a broken text).
    """
    if len(body.encode("utf-8")) <= limit:
        return [body]
    parts, cur, cur_sz = [], [], 0
    for line in body.split("\n"):
        ll = len(line.encode("utf-8")) + 1
        if cur and ((line == "---" and cur_sz + ll > limit)
                    or (line == "" and cur_sz > limit)):
            parts.append("\n".join(cur))
            cur, cur_sz = [], 0
        cur.append(line)
        cur_sz += ll
    if cur:
        parts.append("\n".join(cur))
    return parts


def safe_title(title):
    s = FORBIDDEN.sub(" ", title.replace(":", "_"))
    s = re.sub(r"\s+", " ", s).strip()[:80]
    return s or "conversation"


def note_name(title, inserted_local: datetime, cid):
    return (f"{inserted_local:%Y-%m-%d} {safe_title(title)}__{cid[:8]}.md")


def tag_text(convo):
    texts = [convo.get("title") or ""]
    n = 0
    for node in walk_chain(convo.get("mapping") or {}):
        m = node.get("message")
        if not m:
            continue
        for f in m.get("fragments", []):
            if f.get("type") in ("REQUEST", "RESPONSE") and n < 8:
                c = (f.get("content") or "").strip()
                if c:
                    texts.append(c)
                    n += 1
    return "\n\n".join(t for t in texts if t)[:3000]


def gen_tags(text, model, base_url, timeout=120):
    prompt = ("You are helping catalog notes in Obsidian. Below is the title "
              "and the beginning of an AI assistant session history:\n\n"
              f"{text}\n\n"
              "Return exactly one line: 3-6 comma-separated tags, each word "
              "lowercase, no # symbol, no explanations. "
              "If the text is meaningless, has no topic, or consists only of "
              "quotes and links without content — return exactly one tag: "
              "nodata")
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.2}}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    # local ollama — no proxies of any kind
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            resp = json.load(r).get("response", "")
    except Exception:
        return ""
    line = ""
    for ln in reversed(resp.splitlines()):  # last meaningful line
        if re.search(r"[0-9A-Za-zа-яА-ЯёЁ]", ln):
            line = ln
            break
    tags, seen = [], set()
    for t in line.split(","):
        t = re.sub(r'[#"“”]', "", t).strip().lower()
        t = re.sub(r"\s+", "-", t).strip("-_")
        if 1 < len(t) < 30 and TAG_RE.fullmatch(t) and t not in seen:
            seen.add(t)
            tags.append(t)
    return ", ".join(tags[:6])


def write_note(out_md: Path, header_lines, body_part, footer, updated_epoch):
    with open(out_md, "w", encoding="utf-8") as f:
        for ln in header_lines:
            f.write(ln + "\n")
        f.write(body_part)
        if footer:
            f.write("\n---\n\n" + footer + "\n")
    if updated_epoch:
        os.utime(out_md, (updated_epoch, updated_epoch))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", default=None, metavar="PATH",
                    help=f"conversations.json (default: ${SOURCE_ENV})")
    ap.add_argument("outdir", nargs="?", default=None, metavar="DIR",
                    help=f"notes directory (default: ${VAULT_ENV})")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tags", action="store_true")
    ap.add_argument("--tag-model", default="gemma3:4b")
    ap.add_argument("--tag-url", default="http://localhost:11434")
    ap.add_argument("--max-kb", type=int, default=400)
    args = ap.parse_args()

    # source: --source > $DEEPSEEK_CONVERSATIONS_JSON; neither set is an
    # error (the dump path changes between exports)
    if not args.source:
        s = os.environ.get(SOURCE_ENV, "").strip()
        if not s:
            ap.error(f"source is not set: pass --source or export "
                     f"${SOURCE_ENV} (before each export)")
        args.source = s
    if not os.path.isfile(args.source):
        ap.error(f"source not found: {args.source}")

    # notes dir: positional arg > $DEEPSEEK_OBSIDIAN_VAULT
    if not args.outdir:
        vault = os.environ.get(VAULT_ENV, "").strip()
        if not vault:
            ap.error(f"outdir is not set: pass it positionally or export "
                     f"${VAULT_ENV}")
        args.outdir = vault

    out_root = Path(args.outdir)
    out_root.mkdir(parents=True, exist_ok=True)
    limit = args.max_kb * 1024

    with open(args.source, encoding="utf-8") as f:
        data = json.load(f)
    data.sort(key=lambda c: (c.get("inserted_at") or "", c.get("id") or ""))

    rendered = skipped = failed = tagged = tag_warned = 0
    for i, convo in enumerate(data):
        if args.limit and i >= args.limit:
            break
        cid = convo.get("id") or "no-id"
        id8 = cid[:8]
        try:
            ins = datetime.fromisoformat(convo["inserted_at"])
            upd_epoch = datetime.fromisoformat(convo["updated_at"]).timestamp()
        except (KeyError, ValueError) as e:
            print(f"FAIL: {id8} (bad timestamps: {e})", file=sys.stderr)
            failed += 1
            continue

        loc = ins.astimezone()
        title = convo.get("title") or id8
        name = note_name(title, loc, cid)
        out_md = out_root / name
        base = name[:-3]  # without .md

        # skip BEFORE rendering (10 ms tolerance: utime/timestamp float noise
        # otherwise causes spurious re-renders)
        if out_md.exists() \
                and out_md.stat().st_mtime >= upd_epoch - 0.01:
            print(f"SKIP: {id8} (up to date)")
            skipped += 1
            continue

        try:
            body = render_body(convo)
        except Exception as e:
            print(f"FAIL: {id8} (render: {e})", file=sys.stderr)
            failed += 1
            continue

        tags = []
        if args.tags:
            ollama_tags = gen_tags(tag_text(convo), args.tag_model, args.tag_url)
            if not ollama_tags and not tag_warned:
                print(f"WARN: ollama ({args.tag_model} at {args.tag_url}) did not answer — "
                      "tags skipped", file=sys.stderr)
                tag_warned = 1
            if ollama_tags:
                tagged += 1
                tags = ollama_tags.split(", ")

        create_fm = (f"{loc:%Y-%m-%d}, {loc.hour}:{loc.minute:02d}:"
                     f"{loc.second:02d} {WEEKDAYS[loc.weekday()]}")
        header = ["---",
                  f'create: "{create_fm}"',
                  f"tags: [{', '.join(tags)}]" if tags else "tags:",
                  "---",
                  f"> Source: deepseek · {cid}",
                  f"> Created: {loc:%Y-%m-%d %H:%M:%S}"]
        if tags:
            header.append("> Tags: " + " ".join("#" + t for t in tags))

        body_parts = split_body(body, limit)
        # drop stale parts from a previous render
        for old in out_root.glob(f"{base} p*.md"):
            old.unlink()

        total = len(body_parts)
        for k, bp in enumerate(body_parts, 1):
            part_md = out_md if k == 1 else out_root / f"{base} p{k}.md"
            if k == 1:
                hdr = header + ["", f"# {title}"]
                foot = (f"⏭ Продолжение: [[{base} p2|часть 2 из {total}]]"
                        if total > 1 else "")
            else:
                hdr = [f"# {title} — часть {k}/{total}", "",
                       f"> Source: deepseek · {cid}"]
                prev = base if k == 2 else f"{base} p{k-1}"
                foot = f"⏮ [[{prev}|часть {k-1}]]"
                if k < total:
                    foot += f" · ⏭ [[{base} p{k+1}|часть {k+1}]]"
                else:
                    foot += " · конец"
            write_note(part_md, hdr, bp, foot, upd_epoch)

        print(f"OK: {id8} -> {name}"
              f"{' [' + ', '.join(tags) + ']' if tags else ''}"
              f"{' (parts: %d)' % total if total > 1 else ''}")
        rendered += 1

    msg = (f"Done. Rendered: {rendered}, skipped: {skipped}, failed: {failed}"
           + (f", tagged: {tagged}" if args.tags else "")
           + f" | Folder: {out_root}")
    print(msg)


if __name__ == "__main__":
    main()
