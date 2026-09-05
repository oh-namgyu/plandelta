import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from plandelta.engines import build_engine, require_consent
from plandelta.engines.claude_cli import ClaudeCliEngine
from plandelta.errors import ConsentRequired, EngineTimeout, EngineUnavailable, SchemaViolation

FAKE_OK = """#!/bin/sh
# Echo back what arrived on stdin so the test can prove argv was not used.
payload=$(cat)
printf '{"is_error": false, "result": %s, "modelUsage": {"claude-sonnet-5": {"canonicalModel": "claude-sonnet-5"}}}' \\
  "$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')"
"""

FAKE_WRONG_MODEL = """#!/bin/sh
cat > /dev/null
printf '{"is_error": false, "result": "hi", "modelUsage": {"other-model": {"canonicalModel": "other-model"}}}'
"""

FAKE_HANG = """#!/bin/sh
cat > /dev/null
sleep 30
"""

FAKE_FAIL = """#!/bin/sh
cat > /dev/null
echo "boom ANTHROPIC_API_KEY=sk-ant-secret" >&2
exit 2
"""


def write_binary(directory: Path, name: str, body: str) -> str:
    path = directory / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class ClaudeCliEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_prompt_travels_on_stdin_not_argv(self) -> None:
        engine = ClaudeCliEngine(binary=write_binary(self.dir, "fake-ok", FAKE_OK))
        secret = "PLAN DOCUMENT BODY that must not appear in argv"
        self.assertEqual(engine.complete(secret), secret)
        self.assertNotIn(secret, " ".join(engine._argv()))

    def test_model_pin_mismatch_is_rejected(self) -> None:
        engine = ClaudeCliEngine(binary=write_binary(self.dir, "fake-model", FAKE_WRONG_MODEL))
        with self.assertRaises(SchemaViolation):
            engine.complete("x")

    def test_timeout_kills_the_process(self) -> None:
        engine = ClaudeCliEngine(binary=write_binary(self.dir, "fake-hang", FAKE_HANG), timeout=1)
        with self.assertRaises(EngineTimeout):
            engine.complete("x")

    def test_failure_redacts_secrets_from_stderr(self) -> None:
        engine = ClaudeCliEngine(binary=write_binary(self.dir, "fake-fail", FAKE_FAIL))
        with self.assertRaises(EngineUnavailable) as ctx:
            engine.complete("x")
        self.assertNotIn("sk-ant-secret", str(ctx.exception))
        self.assertIn("[redacted]", str(ctx.exception))

    def test_missing_binary_reports_engine_unavailable(self) -> None:
        engine = ClaudeCliEngine(binary=str(self.dir / "does-not-exist"))
        with self.assertRaises(EngineUnavailable):
            engine.complete("x")

    def test_environment_is_trimmed(self) -> None:
        os.environ["PLANDELTA_TEST_LEAK"] = "1"
        try:
            from plandelta.engines.claude_cli import _clean_env

            self.assertNotIn("PLANDELTA_TEST_LEAK", _clean_env())
        finally:
            del os.environ["PLANDELTA_TEST_LEAK"]


class ConsentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        os.environ.pop("PLANDELTA_YES_SEND_EXTERNAL", None)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_external_engine_needs_consent(self) -> None:
        engine = build_engine("claude-cli")
        with self.assertRaises(ConsentRequired):
            require_consent(self.root, engine.info)

    def test_consent_is_remembered_after_being_granted(self) -> None:
        engine = build_engine("claude-cli")
        require_consent(self.root, engine.info, granted_now=True)
        require_consent(self.root, engine.info)
        self.assertTrue((self.root / ".plandelta" / "consent.json").is_file())

    def test_local_engine_skips_the_gate(self) -> None:
        engine = build_engine("openai-compatible", base_url="http://localhost:1234/v1")
        self.assertEqual(engine.info.data_path, "local")
        require_consent(self.root, engine.info)

    def test_remote_openai_endpoint_is_external(self) -> None:
        engine = build_engine("openai-compatible", base_url="https://api.example.com/v1")
        self.assertEqual(engine.info.data_path, "external")

    def test_unknown_engine_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_engine("telepathy")


if __name__ == "__main__":
    unittest.main()
