"""How the CLOUD import path picks, reads and classifies its object store.

Shared by extraction (``download_cloud_spec`` in ``app.connector``) and the
preflight probe (``app.handler``) so the two cannot drift: the probe must test
the store extraction will actually read, judge a prefix by the same suffix
filter extraction downloads with, and type a failed read the same way
extraction does. Its own module because ``app.connector`` imports
``app.handler``.
"""

from __future__ import annotations

import re
from typing import Any

import orjson
from application_sdk.errors.base import AppError, sanitize_cause_repr
from application_sdk.observability.logger_adaptor import get_logger

from app.errors import (
    CloudSpecAccessDeniedError,
    CloudSpecCredentialRejectedError,
    CloudSpecNotFoundError,
    ObjectStoreCredentialError,
)

_logger = get_logger(__name__)

# obstore only types a denial as UnauthenticatedError / PermissionDeniedError on
# some backends. The rest arrive as GenericError text, in two shapes: "non-2xx
# status code: 401 ..." from the backend response, and "Client error with status
# 403 ..." from the retry layer. An Azure service principal whose secret the
# identity provider refuses is the first shape, raised from the token request,
# so without the text match it read as a transient and the probe failed open.
_HTTP_STATUS_RE = re.compile(r"status(?: code:)?\s*(\d{3})\b")


def classify_store_failure(exc: BaseException) -> AppError | None:
    """Type an object-store failure, or ``None`` when it is not attributable.

    The SDK wraps every object-store failure in ``StorageError``, whose leaf is
    DEPENDENCY_UNAVAILABLE/PLATFORM. That is right for an outage and wrong for a
    credential the store rejected, so classification reads the obstore cause
    rather than the SDK wrapper — otherwise a bad role token is filed against the
    platform and the probe fails open on the one case it exists to catch.

    Shared by the preflight probe and ``download_cloud_spec`` (CONNECT-3893):
    a secret can expire after preflight passed, so extraction has to type the
    same rejection the same way.

    ``None`` means "could not attribute". Preflight raises it as a retryable
    transient; extraction re-raises the original error. Guessing here would make
    the gate fail closed on an outage.
    """
    from obstore.exceptions import (  # noqa: PLC0415 — native module, lazy
        NotFoundError as ObstoreNotFoundError,
        PermissionDeniedError as ObstorePermissionDeniedError,
        UnauthenticatedError as ObstoreUnauthenticatedError,
    )

    from application_sdk.storage.errors import (  # noqa: PLC0415
        StorageAuthError,
        StorageConfigError,
        StorageGatewayAuthUnavailableError,
        StorageNotFoundError,
        StoragePermissionError,
    )

    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        # Atlan's blobstorage gateway answers 401 when it cannot reach its own
        # identity backend. The SDK types that as a retryable platform leaf;
        # reading the 401 in its text would tell the customer to rotate a
        # credential that is fine.
        if isinstance(current, StorageGatewayAuthUnavailableError):
            return None
        status = _HTTP_STATUS_RE.search(str(current))
        http_status = int(status.group(1)) if status else None
        if (
            isinstance(current, ObstoreUnauthenticatedError | StorageAuthError)
            or http_status == 401
        ):
            return CloudSpecCredentialRejectedError(
                message="the object store rejected the configured credential",
                suggested_action=(
                    "Check the object-store credential on this connection. For a "
                    "role-based setup, confirm the role ARN is correct and its "
                    "trust policy still permits Atlan to assume it. For an Azure "
                    "service principal, confirm the client secret is current and "
                    "is the secret value, not the secret ID. For an access key, "
                    "confirm it is active."
                ),
                cause=current,
            )
        if (
            isinstance(current, ObstorePermissionDeniedError | StoragePermissionError)
            or http_status == 403
        ):
            return CloudSpecAccessDeniedError(
                message="the credential authenticated but cannot read the spec location",
                suggested_action=(
                    "Grant the credential read access to the configured prefix "
                    "and object key."
                ),
                cause=current,
            )
        if isinstance(
            current, ObstoreNotFoundError | StorageNotFoundError | FileNotFoundError
        ):
            return CloudSpecNotFoundError(
                message="the configured spec location does not exist in the object store",
                suggested_action="Check the prefix and object key on this connection.",
                cause=current,
            )
        if isinstance(current, StorageConfigError):
            return ObjectStoreCredentialError(
                message="the resolved object-store credential is not usable",
                suggested_action=(
                    "Check the object-store provider and its fields on this "
                    "connection's credential."
                ),
                cause=current,
            )
        # Honour __suppress_context__: a `raise ... from None` upstream (the
        # PF-17 severing this app does itself) means that context is not this
        # failure's cause, and walking it misattributes an unrelated error.
        if current.__cause__ is not None:
            current = current.__cause__
        elif current.__suppress_context__:
            current = None
        else:
            current = current.__context__
    return None


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
