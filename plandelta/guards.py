"""Request guards for the local UI server.

Kept apart from routing because these checks are the security argument, and it
should be readable on its own: a page you visit in another tab must not be able
to drive this port, and a request without the session token must not be answered
at all.
"""

from __future__ import annotations

import json
from typing import BinaryIO
from urllib.parse import urlparse

# The page loads only its own stylesheet and script, talks only to this origin,
# and never embeds anything. Stating that as policy means a successful injection
# still has nowhere to send data and nothing to load.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
LOCAL_HOSTNAMES = ("localhost", "127.0.0.1", "::1")
MAX_BODY_BYTES = 1024 * 1024
DRAIN_LIMIT = MAX_BODY_BYTES * 4


def host_allowed(header: str) -> bool:
    """Only names that already resolve to this machine may address the server.

    This is the DNS-rebinding check: an attacker-controlled name that resolves
    to 127.0.0.1 arrives with its own Host header, and is refused here.
    """
    if not header:
        return False
    name = header.rsplit(":", 1)[0] if header.count(":") == 1 else header
    if header.startswith("["):
        name = header.split("]")[0] + "]"
    return name in LOCAL_HOSTS


def origin_allowed(origin: str) -> bool:
    """Writes need an Origin, and it must be this machine."""
    if not origin:
        return False
    hostname = urlparse(origin).hostname
    return bool(hostname) and hostname in LOCAL_HOSTNAMES


def read_body(stream: BinaryIO, content_length: str) -> tuple[dict | None, str]:
    """Parse a JSON body, or explain why it was refused.

    Returns ``(payload, error_code)``. An oversized body is drained before the
    refusal so the sender is not left writing into a closed pipe, and the drain
    is capped so a hostile sender cannot make us read forever.
    """
    length = int(content_length or 0)
    if length > MAX_BODY_BYTES:
        remaining = min(length, DRAIN_LIMIT)
        while remaining > 0:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
        return None, "E_DOC_TOO_LARGE"
    raw = stream.read(length) if length else b"{}"
    try:
        return json.loads(raw or b"{}"), ""
    except json.JSONDecodeError:
        return None, "E_SCHEMA"
