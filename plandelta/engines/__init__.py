"""Engine registry.

``claude-cli`` is the default because it needs no API key, which makes an
end-to-end run possible on a machine that only has a Claude Code subscription.
That is a testability argument, not a privacy one — see ``base.py``.
"""

from __future__ import annotations

from .base import Engine, EngineInfo, consent_granted, record_consent, require_consent
from .claude_cli import DEFAULT_MODEL as CLAUDE_CLI_DEFAULT_MODEL
from .claude_cli import ClaudeCliEngine
from .http_engines import AnthropicApiEngine, OpenAICompatibleEngine

ENGINE_IDS = ("claude-cli", "anthropic-api", "openai-compatible")
DEFAULT_ENGINE = "claude-cli"
DEFAULT_MODELS = {
    "claude-cli": CLAUDE_CLI_DEFAULT_MODEL,
    "anthropic-api": "claude-sonnet-5",
    "openai-compatible": "local-model",
}


def build_engine(
    engine_id: str = DEFAULT_ENGINE,
    *,
    model: str = "",
    base_url: str = "",
    timeout: int = 60,
) -> Engine:
    """Instantiate an engine by id, applying that engine's default model."""
    model = model or DEFAULT_MODELS.get(engine_id, "")
    if engine_id == "claude-cli":
        return ClaudeCliEngine(model=model, timeout=timeout)
    if engine_id == "anthropic-api":
        return AnthropicApiEngine(model=model, timeout=timeout)
    if engine_id == "openai-compatible":
        return OpenAICompatibleEngine(
            base_url=base_url or "http://localhost:1234/v1", model=model, timeout=timeout
        )
    raise ValueError(f"unknown engine: {engine_id} (expected one of {', '.join(ENGINE_IDS)})")


__all__ = [
    "AnthropicApiEngine",
    "ClaudeCliEngine",
    "DEFAULT_ENGINE",
    "ENGINE_IDS",
    "Engine",
    "EngineInfo",
    "OpenAICompatibleEngine",
    "build_engine",
    "consent_granted",
    "record_consent",
    "require_consent",
]
