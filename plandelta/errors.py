"""Error model shared by the CLI and (later) the HTTP server."""

from __future__ import annotations


class PlandeltaError(Exception):
    """Base error carrying a stable machine-readable code."""

    code = "E_UNKNOWN"

    def as_dict(self) -> dict:
        return {"error": {"code": self.code, "message": str(self)}}


class PathDenied(PlandeltaError):
    code = "E_PATH_DENIED"


class EngineUnavailable(PlandeltaError):
    code = "E_ENGINE_UNAVAILABLE"


class ConsentRequired(PlandeltaError):
    code = "E_CONSENT_REQUIRED"


class DocTooLarge(PlandeltaError):
    code = "E_DOC_TOO_LARGE"


class EngineTimeout(PlandeltaError):
    code = "E_TIMEOUT"


class SchemaViolation(PlandeltaError):
    code = "E_SCHEMA"


class PairAmbiguous(PlandeltaError):
    code = "E_PAIR_AMBIGUOUS"
