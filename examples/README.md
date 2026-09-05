# Example pair

A tiny synthetic plan and completion report, deliberately containing one of each
outcome: a kept promise, an exceeded one, a shortfall stated in the report, a
promise the report says was dropped, an item the report never mentions, and one
piece of unplanned work.

```bash
python3 -m plandelta pairs --root examples
python3 -m plandelta compare checkbox-app --root examples \
    --yes-send-external --report /tmp/plandelta-example
```

Expected shape of the result (exact wording depends on the model):

| Item | Status |
|---|---|
| CLI accepts a plan file and prints JSON | `done` |
| Report renders a donut chart | `exceeded` — the report mentions an extra sparkline |
| Load test sustains 1k requests per second | `missed` — the report says 300 rps |
| Admin dashboard ships | `missed` — the report says it was not built |
| Build the renderer | `unknown` — nothing in the report settles it |
| `--watch` mode | `extra` — delivered, never planned |
