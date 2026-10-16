# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Scoped rounds: `compare --scope TEXT` (repeatable), or a `scope` per pair in
  the manifest. Items outside the scope are marked `out_of_scope`, are not
  sent to the model and are not scored.
- Human overrides: `plandelta override <pair> <item_key> --status ...` and the
  review pane can settle any verdict by hand, and `--revoke` withdraws one.
  The model's verdict is kept next to the human one, superseded overrides are
  revoked rather than deleted, and an override survives later comparisons.
  Totals are recomputed from what is displayed, so the rate and the list
  agree.
- Prose plans (no checkboxes or ordered lists): one cached model call per plan
  revision decides which headings are commitments, and the rest are set aside
  as `out_of_scope` rather than deleted. `--no-prose-llm` turns this off.

### Changed

- Unplanned-work detection now asks a yes/no question about each candidate
  paragraph instead of asking the model to find unplanned work, splits bullet
  lists per bullet, also considers how much of a paragraph no plan item
  accounts for, and batches candidates instead of capping them. A finding
  carries the paragraph that was sent rather than a quote retyped by the
  model. It remains a hint: repeated runs can still disagree.

### Fixed

- The front-matter filter only checks a paragraph's first line, so a
  multi-line bullet list is no longer discarded because a later line looks
  like metadata.

### Security

- The unauthenticated `/api/health` endpoint no longer returns the absolute
  path of the documents root.

## [0.1.0] - 2026-09-06

Initial public release.

### Added

- `plandelta pairs` discovers plan/completion pairs from
  `plandelta.pairs.json` or the default `<slug>_final.md` (or `_draft.md`) and
  `<slug>_completion.md` globs, and reports plans without a completion
  document instead of dropping them.
- `plandelta compare` extracts plan items (checkboxes, ordered lists, then
  headings), retrieves evidence from the completion documents and judges each
  item as `done`, `exceeded`, `partial`, `missed` or `unknown`, plus `extra`
  for unplanned work. `done` and `exceeded` need a verbatim quote, `partial`
  and `missed` need a quote that states the shortfall; otherwise the verdict
  is `unknown`.
- Scoring with a completion rate, separate bonus and penalty, and evidence
  coverage reported next to the rate.
- Engines: `claude-cli` (default), `anthropic-api` and `openai-compatible`
  (for example a local model server), with `--model` pinning and a mismatch
  check. Sending document text to an external engine requires
  `--yes-send-external`.
- A verdict cache keyed on the item, the retrieved evidence, prompt and
  algorithm versions, engine and model, so unchanged documents cost no model
  call and only items whose evidence moved are re-judged.
- Snapshots per comparison round with stable item keys across revisions,
  `plandelta snapshots` with `--rm` and a verified `--backup`.
- Standalone HTML report per pair (`--report DIR`) and `--json` output.
- `plandelta serve`: a local review UI on `127.0.0.1:6188` with a KPI row,
  status donut, per-round trend and per-item scores, and a plan/evidence
  split. It requires a session token, checks `Host` and the `Origin` of
  writes, binds loopback unless `--host` is given and sets a Content Security
  Policy.
- A synthetic example pair in `examples/`.
- No runtime dependencies, Python 3.9+.
