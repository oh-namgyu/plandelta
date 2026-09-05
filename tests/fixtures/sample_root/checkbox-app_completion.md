# Checkbox App — Completion

The CLI is done: `app compare plan.md done.md --json` prints the full JSON
document, covered by 14 unit tests.

The report renders a donut chart and, beyond the plan, also renders a sparkline
trend that was not requested.

Load testing only reached 300 requests per second on the test machine.

No admin dashboard was built.

We also added a `--watch` mode nobody asked for.
