# DeepSeek History Export

Renders the DeepSeek chat export (`conversations.json`) into Obsidian notes.

## Usage

```bash
DEEPSEEK_CONVERSATIONS_JSON=/path/to/conversations.json ./deepseek_obsidian_export.sh
DEEPSEEK_CONVERSATIONS_JSON=... ./deepseek_obsidian_export.sh --tags          # + ollama tags
DEEPSEEK_CONVERSATIONS_JSON=... ./deepseek_obsidian_export.sh /tmp/test --limit 3
DEEPSEEK_CONVERSATIONS_JSON=... ./deepseek_obsidian_export.sh --max-kb 200
```

The `.sh` is a thin wrapper; all logic lives in `deepseek_obsidian_export.py`
(python3, stdlib only — no jq/curl needed).

- **Source** (`--source PATH`, default `$DEEPSEEK_CONVERSATIONS_JSON`): the
  DeepSeek export dump. The dump path changes between exports, so the env var
  is set before each run; missing both is an error.
- **Target** (positional `outdir`, default `$DEEPSEEK_OBSIDIAN_VAULT`): your
  vault directory; missing both is an error.
- `--limit N` — only the first N conversations (tests).
- `--tags` — generate 3–6 tags per note via the local ollama REST API
  (`http://localhost:11434`, `--tag-model`/`--tag-url` to override the
  default `gemma3:4b`). Meaningless/citation-only text gets the single tag
  `nodata`. No proxy is used for the local call; if ollama is unreachable,
  tags are skipped with one WARN and rendering continues.
- `--max-kb KB` (default 400) — notes over the limit are split into
  `<name> p2.md`, ... parts at message/paragraph boundaries (never
  mid-word), linked with wikilinks: Obsidian's renderer crashes on big
  dense markdown.

## Note format

YAML frontmatter (`create:` in `YYYY-MM-DD, H:MM:SS weekday-ru` format,
`tags:`) + quote header (`> Source: deepseek · <id>`, `> Created:`,
`> Tags:`) + conversation body — 👤 User / 🤖 model / 🤔 Reasoning
(`<details>`, truncated at 4000 chars) / 🔍 Web search links / 📄 attached
files. Message timestamps in the body stay raw from the dump (+08:00);
filenames and headers use local time.

## Incremental behavior

Each conversation's `updated_at` is the change marker: a note is skipped when
its mtime >= `updated_at` (with a 10 ms float-noise tolerance). After
rendering, the note's mtime is set to `updated_at` — never touch mtimes in
the notes directory. Unchanged re-runs are all `SKIP`.

## conversations.json format (verified against a real export)

- Top level: list of `{id: uuid, title, inserted_at, updated_at, mapping}`.
- `mapping` is a dict `id → node` `{id, parent, children: [id...], message|null}`.
  The tree branches on regenerations; the **selected branch is the chain
  `root → children[0] → children[0]...`** until empty `children`
  (see `walk_chain` in the .py).
- `message`: `{model: "deepseek-chat", inserted_at, fragments: [{type, ...}]}`.
- Fragment types (with observed counts from one dump):
  - `REQUEST` (user text, `.content`), `RESPONSE` (assistant, `.content`),
    `THINK` (reasoning, `.content`)
  - `TOOL_SEARCH` and `SEARCH` — both `.results[]` of `{title, url}`
    (`SEARCH` looks like the older type name; same shape)
  - `FILE` — `.files[]` of `{file_id, file_name, file_size}`
    (attachment metadata only, no inline content)
  - `TOOL_OPEN` / `TOOL_FIND` — no payload; `TOOL_OPEN` never carried a
    `url` in the observed dump (0 of 1988), despite older tooling expecting one
- Gotchas: `.content` often has a trailing `\n`; `inserted_at`/`updated_at`
  are ISO8601 with **+08:00 (DeepSeek server TZ)** — convert to local time
  for filenames/headers, or dates shift by 5+ hours.

## Requirements

- python3 (stdlib only)
- local ollama — optional, only for `--tags`

## ⚠ Exports are private

Rendered notes contain full conversation history: personal data, local
paths, sometimes credentials. Never commit, push, or share the vault output.

## License

MIT — see [LICENSE](LICENSE).
