"""Unit tests for cloud download helpers in connector.py.

Store construction and download I/O are covered by the SDK's own test suite
(application_sdk.storage.cloud.CloudStore). Only the app-level dispatch
predicate _has_valid_auth is tested here.
"""

from __future__ import annotations

import orjson


from app.connector import _has_valid_auth


class TestHasValidAuth:
    def test_key_auth(self):
        creds = {"username": "AKIA...", "password": "secret", "extra": {}}
        assert _has_valid_auth(creds) is True

    def test_role_auth(self):
        creds = {
            "username": None,
            "password": None,
            "extra": {"aws_role_arn": "arn:aws:iam::123:role/test"},
        }
        assert _has_valid_auth(creds) is True

    def test_no_auth(self):
        creds = {"username": None, "password": None, "extra": {}}
        assert _has_valid_auth(creds) is False

    def test_empty_username(self):
        creds = {"username": "", "password": "secret", "extra": {}}
        assert _has_valid_auth(creds) is False

    def test_extra_as_json_string(self):
        creds = {
            "username": None,
            "password": None,
            "extra": orjson.dumps({"aws_role_arn": "arn:aws:iam::123:role/x"}).decode(),
        }
        assert _has_valid_auth(creds) is True

    def test_extra_empty_string(self):
        creds = {"username": None, "password": None, "extra": ""}
        assert _has_valid_auth(creds) is False
