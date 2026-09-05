"""HTTP-based engines, implemented on the standard library only.

``openai-compatible`` also covers local servers (LM Studio, llama.cpp, Ollama's
OpenAI shim). When its base URL points at localhost the engine is classified as
``local``, which is the only configuration where document text never leaves the
machine — and therefore the only one that skips the consent gate.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

from ..errors import EngineTimeout, EngineUnavailable, SchemaViolation
from .base import Engine, EngineInfo

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
DEFAULT_TIMEOUT = 60


def _post_json(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise EngineUnavailable(f"{url} returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise EngineUnavailable(f"cannot reach {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise EngineTimeout(f"{url} timed out after {timeout}s") from exc


class OpenAICompatibleEngine(Engine):
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_env: str = "PLANDELTA_API_KEY",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        host = (urlparse(base_url).hostname or "").lower()
        data_path = "local" if host in LOCAL_HOSTS else "external"
        self.info = EngineInfo(id="openai-compatible", model_id=model, data_path=data_path)
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout
        self._resolved_model = model

    def resolved_model(self) -> str:
        return self._resolved_model

    def complete(self, prompt: str) -> str:
        key = os.environ.get(self.api_key_env, "")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        data = _post_json(
            f"{self.base_url}/chat/completions",
            headers,
            {
                "model": self.info.model_id,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
            self.timeout,
        )
        self._resolved_model = str(data.get("model") or self.info.model_id)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise SchemaViolation(f"unexpected chat-completions payload: {exc}") from exc


class AnthropicApiEngine(Engine):
    """Anthropic Messages API over urllib — no SDK dependency.

    Untested on the author's machine (no API key available there); the CLI
    engine is the verified default.
    """

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: str = "https://api.anthropic.com",
        timeout: int = DEFAULT_TIMEOUT,
        max_tokens: int = 4096,
    ) -> None:
        self.info = EngineInfo(id="anthropic-api", model_id=model, data_path="external")
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._resolved_model = model

    def resolved_model(self) -> str:
        return self._resolved_model

    def complete(self, prompt: str) -> str:
        key = os.environ.get(self.api_key_env)
        if not key:
            raise EngineUnavailable(f"{self.api_key_env} is not set")
        data = _post_json(
            f"{self.base_url}/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {
                "model": self.info.model_id,
                "max_tokens": self.max_tokens,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
            self.timeout,
        )
        self._resolved_model = str(data.get("model") or self.info.model_id)
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        if not text:
            raise SchemaViolation("messages API returned no text block")
        return text
