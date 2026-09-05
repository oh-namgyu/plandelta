# Prose Plan

## Goal
Deliver a small ingest service that reads CSV files and stores rows.

## Storage design
Rows land in SQLite with a unique index on the source path so re-ingesting a
file is idempotent.

## Observability
Every ingest run writes a summary line with counts and duration.
