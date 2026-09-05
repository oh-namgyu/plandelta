# Prose Plan — Completion

The ingest service reads CSV files and writes rows into SQLite. The unique index
on source path is in place and re-ingest is idempotent (test: test_reingest).

Observability was deferred; no summary line is written yet.
