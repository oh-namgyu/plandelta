# Contributing to plandelta

Thanks for your interest! Bug reports, misjudged verdicts (with the plan and
completion excerpts that caused them) and pull requests are welcome.

## Development setup

There is nothing to build and no dependency to install.

```sh
python -m unittest discover -s tests -v
python -m plandelta --help
python -m plandelta pairs --root examples
```

CI runs the suite on Python 3.9 and 3.12; please keep both working.

## Guidelines

- **Zero runtime dependencies.** Standard library only; the HTTP engines use
  `urllib`. Do not add a third-party import.
- **No model calls in tests.** The suite uses fake engines and stub
  binaries; a test must never reach the real `claude` CLI, an API or a
  network endpoint.
- **Abstain rather than guess.** A `done`/`exceeded` verdict needs a verbatim
  quote and a `partial`/`missed` verdict needs a quote that states the
  shortfall. Changes to the judge must keep both rules.
- **Bump the fingerprint when judging changes.** If a prompt or algorithm
  change can alter a verdict, update its version in `plandelta/hashing.py` so
  cached verdicts are not silently reused.
- **Consent before sending.** Any path that sends document text off the
  machine stays behind `--yes-send-external`. Read [SECURITY.md](SECURITY.md)
  for the data paths and the server's token, `Host` and `Origin` checks.
- **Type hints** on new functions, and a regression test for any bug fix.

## Pull requests

Keep PRs focused on one change, describe what changed and how you verified
it, and make sure the test suite is green before opening one.
