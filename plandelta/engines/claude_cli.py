"""Engine backed by the local `claude` CLI.

Hardening choices, all deliberate:

- the prompt goes in on **stdin**, never argv, so document text never shows up
  in the process table;
- ``shell=False`` and a trimmed environment;
- output is capped, and a timeout kills the whole process group rather than
  leaking a detached child;
- the model reported by the CLI is compared against the pinned model, because a
  silently swapped model invalidates every cached verdict.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from typing import Sequence

from ..errors import EngineTimeout, EngineUnavailable, SchemaViolation
from .base import Engine, EngineInfo

DEFAULT_MODEL = "claude-sonnet-5"
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 120
# The CLI needs enough environment to find its own config and credentials —
# measured, not guessed: with only PATH and HOME it fails to start. Anything
# beyond this list (including API keys) is deliberately withheld; users who need
# more can name the variables in PLANDELTA_ENV_PASSTHROUGH.
PASSTHROUGH_ENV = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "TMPDIR", "LANG", "LC_ALL")
EXTRA_ENV_VAR = "PLANDELTA_ENV_PASSTHROUGH"


class ClaudeCliEngine(Engine):
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        binary: str = "claude",
    ) -> None:
        self.info = EngineInfo(id="claude-cli", model_id=model, data_path="external")
        self.timeout = timeout
        self.binary = binary
        self._resolved_model = model

    def resolved_model(self) -> str:
        return self._resolved_model

    def _argv(self) -> list[str]:
        return [self.binary, "-p", "--output-format", "json", "--model", self.info.model_id]

    def complete(self, prompt: str) -> str:
        payload = _run(self._argv(), prompt, self.timeout)
        return self._parse(payload)

    def _parse(self, payload: str) -> str:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SchemaViolation(f"claude CLI did not return JSON: {exc}") from exc
        if data.get("is_error"):
            raise EngineUnavailable(str(data.get("result") or "claude CLI reported an error"))
        self._resolved_model = _canonical_model(data) or self.info.model_id
        _check_model_pin(self.info.model_id, self._resolved_model)
        result = data.get("result")
        if not isinstance(result, str):
            raise SchemaViolation("claude CLI response has no textual result")
        return result


def _canonical_model(data: dict) -> str:
    usage = data.get("modelUsage") or {}
    if not isinstance(usage, dict) or not usage:
        return ""
    # One call, one model; take the canonical name of the only entry.
    name, detail = sorted(usage.items())[0]
    if isinstance(detail, dict):
        return str(detail.get("canonicalModel") or name)
    return str(name)


def _check_model_pin(pin: str, actual: str) -> None:
    """A pinned run must not silently execute on another model."""
    if not actual or actual == pin or actual.startswith(pin) or pin.startswith(actual):
        return
    raise SchemaViolation(f"model pin mismatch: pinned {pin!r}, served {actual!r}")


def _clean_env() -> dict:
    names = list(PASSTHROUGH_ENV)
    extra = os.environ.get(EXTRA_ENV_VAR, "")
    names.extend(name.strip() for name in extra.split(",") if name.strip())
    env = {key: os.environ[key] for key in names if key in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin")
    return env


def _run(argv: Sequence[str], prompt: str, timeout: int) -> str:
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=_clean_env(),
            start_new_session=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise EngineUnavailable(f"{argv[0]} not found on PATH") from exc

    try:
        out, err = proc.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_group(proc)
        proc.communicate()
        raise EngineTimeout(f"{argv[0]} timed out after {timeout}s") from exc

    if len(out) > MAX_OUTPUT_BYTES:
        raise SchemaViolation(f"{argv[0]} produced more than {MAX_OUTPUT_BYTES} bytes")
    if proc.returncode != 0:
        raise EngineUnavailable(f"{argv[0]} exited {proc.returncode}: {_redact(err)[:400]}")
    return out


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - race
        proc.kill()


def _redact(text: str) -> str:
    """Never let a key reach a log line."""
    out = []
    for token in (text or "").split():
        if token.startswith(("sk-", "ghp_", "xoxb-")) or "API_KEY" in token:
            out.append("[redacted]")
        else:
            out.append(token)
    return " ".join(out)
