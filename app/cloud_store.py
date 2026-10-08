"""How the CLOUD import path picks and reads its object store.

Shared by extraction (``download_cloud_spec`` in ``app.connector``) and the
preflight probe (``app.handler``) so the two cannot drift: the probe must test
the store extraction will actually read, and judge a prefix by the same suffix
filter extraction downloads with. Its own module because ``app.connector``
imports ``app.handler``.
"""

from __future__ import annotations

from typing import Any

import orjson
from application_sdk.errors.base import sanitize_cause_repr
from application_sdk.observability.logger_adaptor import get_logger

_logger = get_logger(__name__)

# Object keys a prefix-only CLOUD import downloads. Matched case-insensitively
# with ``endswith``, as ``CloudStore.download(suffix_filter=...)`` does.
CLOUD_SPEC_SUFFIXES: frozenset[str] = frozenset({".json", ".yaml", ".yml", ".zip"})


def has_valid_auth(credentials: dict[str, Any]) -> bool:
    """Return True if credentials have explicit key-based or role-based auth.

    Determines whether to use an external cloud store (Path A) or fall back
    to the tenant's own Dapr-configured store (Path B).

    Must never raise: the resolved credential dict (plaintext password
    included) is in this frame, and the SDK's loguru sinks format tracebacks
    with ``diagnose`` enabled, which annotates frame variables — a raise here
    would write the credential to the logs (CONNECT-812 PF-17 class). A
    malformed ``extra`` therefore reads as "no role auth", not an error.
    """
    has_key_auth = bool(credentials.get("username") and credentials.get("password"))
    extra = credentials.get("extra") or credentials.get("extras") or {}
    if isinstance(extra, str):
        try:
            extra = orjson.loads(extra) if extra else {}
        except orjson.JSONDecodeError as exc:
            # Falling back to "no role auth" is the contract (see the docstring
            # — this must never raise), but the fallback is no longer silent: a
            # malformed ``extra`` is why an operator would otherwise see Path B
            # chosen with no explanation. The cause goes through
            # sanitize_cause_repr and ``exc_info`` stays off, because this
            # frame holds the plaintext credential and loguru's ``diagnose``
            # annotates frame variables into tracebacks (CONNECT-812 PF-17).
            # orjson's message carries only a line/column position, never the
            # payload.
            _logger.warning(
                "credential 'extra' is not valid JSON; treating as no role auth: %s",
                sanitize_cause_repr(exc),
            )
            extra = {}
    if not isinstance(extra, dict):
        extra = {}
    has_role_auth = bool(extra.get("aws_role_arn"))
    return has_key_auth or has_role_auth
