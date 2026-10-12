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


def _read_extra(credentials: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """The credential's ``extra`` as a dict, and whether it failed to parse.

    A string ``extra`` that is not valid JSON reads as an empty dict, which
    :func:`has_valid_auth` takes as "no role auth". Never raises, for the
    reason :func:`has_valid_auth` gives.
    """
    extra = credentials.get("extra") or credentials.get("extras") or {}
    malformed = False
    if isinstance(extra, str):
        try:
            extra = orjson.loads(extra) if extra else {}
        except orjson.JSONDecodeError as exc:
            # DEBUG, because this runs on the preflight path too, where the
            # check row is the customer-facing record and the gate levels its
            # own outcome row. Each caller surfaces the fallback where its
            # reader looks (see extra_is_malformed). The cause goes through
            # sanitize_cause_repr and ``exc_info`` stays off, because this
            # frame holds the plaintext credential and loguru's ``diagnose``
            # annotates frame variables into tracebacks (CONNECT-812 PF-17).
            # orjson's message carries only a line/column position, never the
            # payload.
            _logger.debug(
                "credential 'extra' is not valid JSON; treating as no role auth: %s",
                sanitize_cause_repr(exc),
            )
            extra = {}
            malformed = True
    if not isinstance(extra, dict):
        extra = {}
    return extra, malformed


def extra_is_malformed(credentials: dict[str, Any]) -> bool:
    """Whether the credential's ``extra`` is a string that is not valid JSON.

    :func:`has_valid_auth` reads such an ``extra`` as "no role auth", which
    routes the run to the tenant store. Each caller says why on its own
    surface: the preflight probe in its check row, extraction in its log.
    """
    return _read_extra(credentials)[1]


def has_valid_auth(credentials: dict[str, Any]) -> bool:
    """Return True if credentials have explicit key-based or role-based auth.

    Determines whether to use an external cloud store (Path A) or fall back
    to the tenant's own Dapr-configured store (Path B).

    Must never raise: the resolved credential dict (plaintext password
    included) is in this frame, and the SDK's loguru sinks format tracebacks
    with ``diagnose`` enabled, which annotates frame variables — a raise here
    would write the credential to the logs (CONNECT-812 PF-17 class). A
    malformed ``extra`` therefore reads as "no role auth", not an error; use
    :func:`extra_is_malformed` to tell the connection owner so.
    """
    has_key_auth = bool(credentials.get("username") and credentials.get("password"))
    extra, _ = _read_extra(credentials)
    has_role_auth = bool(extra.get("aws_role_arn"))
    return has_key_auth or has_role_auth
