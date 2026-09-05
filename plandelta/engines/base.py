"""Engine interface and the consent gate for engines that leave the machine.

A "local CLI" is not the same thing as local inference: `claude -p` runs on your
machine but sends the document to Anthropic. Engines therefore declare a
``data_path`` of ``external`` or ``local``, and every external engine needs
explicit consent before it sees a single line of your documents.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConsentRequired

CONSENT_ENV = "PLANDELTA_YES_SEND_EXTERNAL"
CONSENT_FILE = ".plandelta/consent.json"


@dataclass
class EngineInfo:
    id: str
    model_id: str
    data_path: str  # "external" | "local"

    def as_dict(self) -> dict:
        return {"engine": self.id, "model": self.model_id, "data_path": self.data_path}


class Engine:
    """Minimal contract: turn one prompt into one text completion."""

    info: EngineInfo

    def complete(self, prompt: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def resolved_model(self) -> str:
        """Model id actually reported by the last call (defaults to the pin)."""
        return self.info.model_id


def consent_granted(root: Path) -> bool:
    if os.environ.get(CONSENT_ENV) == "1":
        return True
    return (root / CONSENT_FILE).is_file()


def record_consent(root: Path, engine_id: str) -> None:
    path = root / CONSENT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{{"engine": "{engine_id}", "granted": true}}\n', encoding="utf-8")
    path.chmod(0o600)


def require_consent(root: Path, info: EngineInfo, *, granted_now: bool = False) -> None:
    """Refuse to run an external engine until the user has agreed once."""
    if info.data_path != "external":
        return
    if granted_now:
        record_consent(root, info.id)
        return
    if consent_granted(root):
        return
    raise ConsentRequired(
        f"engine '{info.id}' sends document text to an external service. "
        f"Re-run with --yes-send-external (or set {CONSENT_ENV}=1) to allow it."
    )
