# Security

## Reporting a problem

Please open an issue at <https://github.com/oh-namgyu/plandelta/issues>. There
is no private disclosure channel; if a finding should not be public, say so in a
short issue without the details and a maintainer will follow up.

## Where your documents go

plandelta reads plans and completion reports and sends their text to whichever
engine you select. **Running the tool locally does not mean the inference is
local.**

| Engine | Data path | Consent required |
|---|---|---|
| `claude-cli` (default) | **External** — the local `claude` binary sends the text to Anthropic | Yes |
| `anthropic-api` | External — Anthropic Messages API | Yes |
| `openai-compatible`, remote base URL | External — whichever host you configure | Yes |
| `openai-compatible`, `localhost` base URL | Local inference only | No |

Every external engine refuses to run until you pass `--yes-send-external` once
(or set `PLANDELTA_YES_SEND_EXTERNAL=1`). The grant is recorded in
`.plandelta/consent.json` with mode `0600`.

## Untrusted input

Plans and completion reports are treated as untrusted:

- **Prompt injection.** Document text is wrapped in `<document>` fences and the
  system prompt states that fenced content is data, never instructions. Model
  replies are accepted only if they validate against the expected schema.
- **Fabricated evidence.** Every quote the model returns is checked against the
  source document. An unverifiable quote is dropped, and an item claimed as
  delivered with no surviving quote is demoted to `unknown` rather than trusted.
- **HTML/SVG in documents.** Reports escape all document text with
  `html.escape`. Markup inside a document renders as characters; the only SVG in
  a report is the chart markup plandelta generates itself. Reports load nothing
  from the network.
- **Path traversal.** File access is confined to `--root`. Paths are resolved
  (following symlinks) before the containment check, so a link pointing outside
  the tree is rejected with `E_PATH_DENIED`.

## Subprocess and secret handling

- The prompt is written to the engine's **stdin**, never argv, so document text
  never appears in the process table.
- Subprocesses run with `shell=False`, a trimmed environment (see
  `PASSTHROUGH_ENV`; extend it deliberately with `PLANDELTA_ENV_PASSTHROUGH`),
  and `start_new_session=True` so a timeout kills the whole process group.
- Engine stdout is capped at 8 MB. Error text is redacted before it reaches a
  message, so API keys cannot leak through a failure path.
- API keys are read from the environment only, and are never written to the
  database, the JSON output, or a report.

## Local files

`.plandelta/` is created with mode `0700`, and the SQLite database, its WAL and
SHM sidecars, backups and the consent file are all `0600`. Backups are taken
through SQLite's backup API (a file copy of a WAL database is not consistent)
and are integrity-checked before they are kept.
